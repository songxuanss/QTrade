"""Actual PostgreSQL invariants. Run via scripts/selftest-postgres.py after Compose."""

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from qtrade_contracts import (
    Order,
    OrderCommand,
    OrderEvent,
    RiskDecision,
    RiskReason,
    StrategyVersion,
)
from qtrade_domain.alert_jobs import AlertEvaluationJob
from qtrade_domain.alerts import (
    AlertEvent,
    AlertRuleVersion,
    AlertSeverity,
    AlertSource,
    AlertStatus,
)
from qtrade_domain.identity import Role
from qtrade_domain.kill_switch import KillSwitch, RecoveryRequest
from qtrade_domain.notification_delivery import NotificationAttempt
from qtrade_domain.order_execution import OrderExecutionRecord
from qtrade_domain.orders import transition_order
from qtrade_domain.ports import BrokerOrder, ErrorCode, Notification, PortError
from qtrade_domain.reconciliation import ReconciliationDifference, ReconciliationReport
from qtrade_domain.risk_records import RiskDecisionRecord
from qtrade_domain.strategies import Strategy
from qtrade_postgres.alert_evaluation import PostgresAlertEvaluationStore
from qtrade_postgres.alert_events import PostgresAlertEventStore
from qtrade_postgres.alert_jobs import PostgresAlertEvaluationQueue
from qtrade_postgres.alerts import PostgresAlertRuleStore
from qtrade_postgres.kill_switch import PostgresKillSwitchStore
from qtrade_postgres.migrate import MIGRATIONS, MigrationError, apply, discover, verify
from qtrade_postgres.notification_delivery import PostgresNotificationDeliveryQueue
from qtrade_postgres.notification_inbox import PostgresNotificationInbox
from qtrade_postgres.order_execution import PostgresOrderExecutionStore
from qtrade_postgres.reconciliation import (
    PostgresAccountTradingGateStore,
    PostgresReconciliationReportStore,
)
from qtrade_postgres.risk import PostgresRiskDecisionStore, PostgresRiskLimitStore
from qtrade_postgres.strategies import PostgresStrategyStore

URL = os.environ.get("QTRADE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="requires explicit local PostgreSQL integration target"
)


@pytest.fixture
def database():
    schema = "qtrade_test_" + uuid4().hex
    try:
        yield URL, schema
    finally:
        with psycopg.connect(URL) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def conn(database):
    url, schema = database
    apply(url, schema=schema)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        yield conn


def seed(conn):
    user, workspace = uuid4(), uuid4()
    conn.execute(
        "INSERT INTO qtrade_user(user_id,issuer,subject,display_name) VALUES (%s,%s,%s,%s)",
        (user, "https://sso.test", str(user), "Test user"),
    )
    conn.execute(
        "INSERT INTO workspace(workspace_id,name,kind,owner_id) VALUES (%s,%s,'personal',%s)",
        (workspace, "Personal", user),
    )
    conn.execute(
        "INSERT INTO membership(workspace_id,user_id,role) VALUES (%s,%s,'admin')",
        (workspace, user),
    )
    return user, workspace


def test_collection_preferences_user_scope_isolation_and_live_authority(conn, database):
    from qtrade_contracts.collection_preferences import CollectionPreferences
    from qtrade_postgres.collection_preferences import PostgresCollectionPreferences

    url, schema = database
    owner, scope = seed(conn)
    other, other_scope = seed(conn)
    conn.execute(
        "INSERT INTO membership(workspace_id,user_id,role) VALUES(%s,%s,'admin')", (scope, other)
    )
    store = PostgresCollectionPreferences(url, schema=schema)
    draft = CollectionPreferences.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "source": "mairui.pro.bars",
                "granularity": "5m",
                "first": "2025-09-01",
                "last": "2025-09-05",
                "instruments": "601288.SH",
                "fields": ["pe_ttm"],
                "mode": "missing",
            }
        )
    )
    assert store.get(owner, scope) is None
    store.put(owner, scope, draft)
    assert PostgresCollectionPreferences(url, schema=schema).get(owner, scope) == draft
    assert store.get(other, scope) is None
    assert store.get(other, other_scope) is None
    store.put(other, scope, draft.model_copy(update={"instruments": "600519.SH"}))
    assert store.get(owner, scope).instruments == "601288.SH"
    assert store.get(other, scope).instruments == "600519.SH"
    conn.execute(
        "UPDATE membership SET role='viewer' WHERE workspace_id=%s AND user_id=%s", (scope, other)
    )
    assert store.get(other, scope).instruments == "600519.SH"
    with pytest.raises(PortError) as rejected:
        store.put(other, scope, draft)
    assert rejected.value.code == ErrorCode.REJECTED
    conn.execute(
        "UPDATE membership SET active=false WHERE workspace_id=%s AND user_id=%s", (scope, other)
    )
    with pytest.raises(PortError) as revoked:
        store.get(other, scope)
    assert revoked.value.code == ErrorCode.UNAUTHORIZED
    with pytest.raises(PortError):
        store.put(other, scope, draft)
    with pytest.raises(PortError):
        store.get(owner, other_scope)
    assert store.get(owner, scope) == draft
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE collection_preferences SET draft=%s::jsonb "
            "WHERE user_id=%s AND workspace_id=%s",
            (json.dumps({"schema_version": None}), owner, scope),
        )
    conn.execute(
        "UPDATE collection_preferences SET draft=%s::jsonb WHERE user_id=%s AND workspace_id=%s",
        (json.dumps({"schema_version": 1, "unknown_field": "fixture"}), owner, scope),
    )
    with pytest.raises(PortError) as invalid:
        store.get(owner, scope)
    assert invalid.value.code == ErrorCode.UNAVAILABLE


def copy_current_migrations(target):
    for migration in MIGRATIONS.glob("*.sql"):
        shutil.copy(migration, target)


def test_empty_database_repeatable_migration_and_concurrent_start(database):
    url, schema = database
    versions = tuple(item.version for item in discover())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: apply(url, schema=schema), range(4)))
    assert results.count(versions) == 1
    assert results.count(()) == 3
    with psycopg.connect(url) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema,)
            )
        }
    assert {
        "qtrade_user",
        "workspace",
        "membership",
        "session",
        "audit_event",
        "execution_runtime",
        "kill_switch_state",
        "kill_switch_recovery_request",
        "schema_migration",
    } <= tables


def test_postgres_kill_switch_scope_revision_and_independent_recovery(conn, database):
    user_id, workspace_id = seed(conn)
    other_user, _ = seed(conn)
    account_id = uuid4()
    now = datetime.now(UTC)
    store = PostgresKillSwitchStore(database[0], schema=database[1])
    active = store.set_state(
        KillSwitch(workspace_id, account_id, True, "account loss limit", user_id, now, 1),
        expected_revision=None,
    )
    assert store.is_active(workspace_id, account_id)
    assert not store.is_active(workspace_id, uuid4())
    assert store.get_state(workspace_id, account_id) == active
    with pytest.raises(PortError, match="changed concurrently"):
        store.set_state(
            KillSwitch(workspace_id, account_id, False, "stale", user_id, now, 2),
            expected_revision=0,
        )

    request = RecoveryRequest(uuid4(), workspace_id, account_id, user_id, now)
    store.create(request)
    with pytest.raises(PortError, match="already used"):
        store.consume(request.request_id, approver_id=user_id, now=now)
    assert store.consume(request.request_id, approver_id=other_user, now=now) == request
    with pytest.raises(PortError, match="already used"):
        store.consume(request.request_id, approver_id=other_user, now=now)


def test_risk_decision_history_is_workspace_scoped_and_append_only(conn, database):
    _user_id, workspace_id = seed(conn)
    now = datetime.now(UTC)
    decision = RiskDecision(
        schema_version=1,
        workspace_id=workspace_id,
        decision_id=uuid4(),
        intent_id=uuid4(),
        decided_at=now,
        outcome="deny",
        rule_set_version="risk.v1",
        input_sha256="a" * 64,
        reasons=(
            RiskReason(
                rule_id="loss.daily",
                rule_version="limits.v3",
                code="limit_exceeded.account.daily_loss",
                summary="Daily loss limit exceeded.",
                current_value="110",
                limit_value="100",
            ),
        ),
        trace_id=uuid4(),
    )
    record = RiskDecisionRecord(decision, '{"daily_loss":"110"}')
    store = PostgresRiskDecisionStore(database[0], schema=database[1])

    assert store.record(record) == record
    assert store.record(record) == record
    assert store.get_for_intent(workspace_id, decision.intent_id) == record
    assert store.list(workspace_id, outcome="deny", since=now, limit=10) == (record,)
    assert store.list(uuid4(), outcome=None, since=None, limit=10) == ()
    conn.execute(
        "INSERT INTO risk_limit(workspace_id,limit_id,dimension,scope_id,metric,maximum,version) "
        "VALUES (%s,'workspace.exposure','workspace',%s,'notional_exposure',1000,'limits.v1')",
        (workspace_id, str(workspace_id)),
    )
    limits = PostgresRiskLimitStore(database[0], schema=database[1]).list(workspace_id)
    assert len(limits) == 1
    assert limits[0].limit_id == "workspace.exposure"
    with pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE risk_decision SET rule_set_version='tampered' "
            "WHERE workspace_id=%s AND decision_id=%s",
            (workspace_id, decision.decision_id),
        )


def test_order_execution_journal_recovers_append_only_command_and_fills(conn, database):
    _user_id, workspace_id = seed(conn)
    fixtures = Path(__file__).parent / "fixtures/contracts"
    command = OrderCommand.model_validate_json((fixtures / "OrderCommand.json").read_text())
    intent = command.intent.model_copy(update={"workspace_id": workspace_id})
    decision = command.decision.model_copy(update={"workspace_id": workspace_id})
    command = command.model_copy(
        update={"workspace_id": workspace_id, "intent": intent, "decision": decision}
    )
    initial = Order.model_validate_json((fixtures / "Order.json").read_text())
    order = initial.model_copy(
        update={
            "workspace_id": workspace_id,
            "order_id": uuid4(),
            "account_id": intent.account_id,
            "runtime_id": intent.runtime_id,
            "intent_id": intent.intent_id,
            "command_id": command.command_id,
            "idempotency_key": command.idempotency_key,
            "quantity": intent.quantity,
            "instrument_id": intent.instrument_id,
            "side": intent.side,
        }
    )
    created = OrderEvent(
        schema_version=1,
        workspace_id=workspace_id,
        event_id=uuid4(),
        order_id=order.order_id,
        revision=1,
        from_status=None,
        to_status="created",
        occurred_at=order.created_at,
        source="system",
        filled_quantity=order.filled_quantity,
    )
    order, approved = transition_order(
        order,
        "risk_approved",
        occurred_at=order.updated_at,
        source="risk",
        event_id=uuid4(),
    )
    order, submitted = transition_order(
        order,
        "submitted",
        occurred_at=order.updated_at,
        source="system",
        event_id=uuid4(),
    )
    prepared = OrderExecutionRecord(command, order, (created, approved, submitted))
    store = PostgresOrderExecutionStore(database[0], schema=database[1])

    assert store.prepare(prepared) == prepared
    assert store.prepare(prepared) == prepared
    filled, fill_event = transition_order(
        order,
        "filled",
        occurred_at=order.updated_at + timedelta(seconds=1),
        source="broker",
        event_id=uuid4(),
        filled_quantity=order.quantity,
        average_fill_price=Decimal("10.5"),
    )
    broker_order = BrokerOrder(
        workspace_id,
        intent.account_id,
        command.command_id,
        str(command.idempotency_key),
        "filled",
        intent.quantity,
        intent.quantity,
        average_fill_price=Decimal("10.5"),
    )
    completed = OrderExecutionRecord(command, filled, (*prepared.events, fill_event), broker_order)
    assert store.update(completed) == completed
    assert store.get_for_intent(workspace_id, intent.intent_id) == completed


def test_reconciliation_report_is_immutable_and_gate_blocks_live_account(conn, database):
    _user_id, workspace_id = seed(conn)
    account_id = uuid4()
    report = ReconciliationReport(
        uuid4(),
        workspace_id,
        account_id,
        datetime.now(UTC),
        "mismatch",
        (ReconciliationDifference("cash", "account", "900", "1000", "cash differs"),),
    )
    reports = PostgresReconciliationReportStore(database[0], schema=database[1])
    gates = PostgresAccountTradingGateStore(database[0], schema=database[1])

    assert reports.record(report) == report
    assert reports.latest(workspace_id, account_id) == report
    gates.block(workspace_id, account_id, report.report_id, report.reconciled_at)
    assert gates.is_blocked(workspace_id, account_id)
    with pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE reconciliation_report SET status='matched' "
            "WHERE workspace_id=%s AND report_id=%s",
            (workspace_id, report.report_id),
        )


def test_failed_migration_rolls_back_and_can_be_repaired_forward(database, tmp_path):
    url, schema = database
    shutil.copy(MIGRATIONS / "0001_identity.sql", tmp_path)
    (tmp_path / "0002_failure.sql").write_text(
        "CREATE TABLE partial(id integer); SELECT nonexistent_function();"
    )
    with pytest.raises(psycopg.Error):
        apply(url, schema=schema, directory=tmp_path)
    with psycopg.connect(url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname=%s", (schema,)
            ).fetchone()[0]
            == 0
        )
    (tmp_path / "0002_failure.sql").write_text("CREATE TABLE partial(id integer);")
    assert apply(url, schema=schema, directory=tmp_path) == (1, 2)
    assert apply(url, schema=schema, directory=tmp_path) == ()


def test_existing_migration_checksums_and_newer_schema_rejected(database, tmp_path):
    url, schema = database
    apply(url, schema=schema)
    copy_current_migrations(tmp_path)
    (tmp_path / "0001_identity.sql").write_text(
        (MIGRATIONS / "0001_identity.sql").read_text() + "\n-- edited\n"
    )
    with pytest.raises(MigrationError, match="checksum changed"):
        apply(url, schema=schema, directory=tmp_path)
    with psycopg.connect(url) as conn:
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.schema_migration(version,name,checksum) VALUES (99,'future',%s)"
            ).format(sql.Identifier(schema)),
            ("0" * 64,),
        )
    with pytest.raises(MigrationError, match="newer"):
        apply(url, schema=schema)


def test_failed_upgrade_preserves_existing_schema_and_data(database, conn, tmp_path):
    user, workspace = seed(conn)
    versions = tuple(item.version for item in discover())
    copy_current_migrations(tmp_path)
    (tmp_path / f"{versions[-1] + 1:04d}_upgrade.sql").write_text(
        "ALTER TABLE workspace ADD COLUMN new_field text; SELECT nonexistent_function();"
    )
    with pytest.raises(psycopg.Error):
        apply(database[0], schema=database[1], directory=tmp_path)
    assert conn.execute("SELECT workspace_id FROM workspace").fetchone()[0] == workspace
    assert conn.execute("SELECT user_id FROM qtrade_user").fetchone()[0] == user
    assert conn.execute("SELECT version FROM schema_migration ORDER BY version").fetchall() == [
        (version,) for version in versions
    ]
    assert (
        conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema=%s AND column_name='new_field'",
            (database[1],),
        ).fetchone()[0]
        == 0
    )


def test_unique_user_personal_workspace_and_membership_constraints(conn):
    user, workspace = seed(conn)
    for statement, parameters in [
        (
            "INSERT INTO qtrade_user(user_id,issuer,subject,display_name) VALUES (%s,'https://sso.test',%s,'Duplicate')",
            (uuid4(), str(user)),
        ),
        (
            "INSERT INTO workspace(workspace_id,name,kind,owner_id) "
            "VALUES (%s,'Duplicate','personal',%s)",
            (uuid4(), user),
        ),
        (
            "INSERT INTO membership(workspace_id,user_id,role) VALUES (%s,%s,'admin')",
            (workspace, user),
        ),
        (
            "INSERT INTO membership(workspace_id,user_id,role) VALUES (%s,%s,'invalid')",
            (workspace, uuid4()),
        ),
    ]:
        with pytest.raises(psycopg.IntegrityError):
            conn.execute(statement, parameters)
    assert conn.execute("SELECT live_trading_enabled FROM workspace").fetchone() == (False,)


def test_five_roles_supported_and_invalid_role_rejected(conn):
    user, workspace = seed(conn)
    for role in Role:
        conn.execute(
            "UPDATE membership SET role=%s WHERE workspace_id=%s AND user_id=%s",
            (role.value, workspace, user),
        )
        assert conn.execute("SELECT role FROM membership").fetchone()[0] == role.value
    with pytest.raises(psycopg.IntegrityError):
        conn.execute("UPDATE membership SET role='superuser'")


def test_session_expiry_scope_and_encryption_metadata(conn):
    user, workspace = seed(conn)
    conn.execute(
        """INSERT INTO session(session_digest,user_id,current_workspace_id,csrf_digest,expires_at)
                     VALUES (%s,%s,%s,%s,now()+interval '1 hour')""",
        ("a" * 64, user, workspace, "b" * 64),
    )
    for statement in [
        "UPDATE session SET expires_at=created_at",
        "UPDATE session SET session_digest='raw-cookie'",
        "UPDATE session SET token_ciphertext='abc'::bytea",
        "UPDATE session SET csrf_digest='raw-csrf'",
    ]:
        with pytest.raises(psycopg.IntegrityError):
            conn.execute(statement)
    with pytest.raises(psycopg.IntegrityError):
        conn.execute("UPDATE session SET current_workspace_id=%s", (uuid4(),))
    conn.execute(
        "UPDATE session SET token_ciphertext='encrypted'::bytea,encryption_key_id='key-v1'"
    )
    conn.execute("UPDATE session SET revoked_at=now()")
    assert conn.execute("SELECT revoked_at IS NOT NULL FROM session").fetchone() == (True,)


def test_audit_records_context_and_rejects_mutation(conn):
    user, workspace = seed(conn)
    trace = uuid4()
    row = conn.execute(
        """INSERT INTO audit_event(workspace_id,actor_type,actor_id,action,result,source,source_ip,
         trace_id,details)
         VALUES (%s,'user',%s,'membership.changed','success',
         'api','127.0.0.1',%s,'{"role":"viewer"}')
         RETURNING workspace_id,actor_id,action,result,occurred_at,source,trace_id,details""",
        (workspace, user, trace),
    ).fetchone()
    assert row[:4] == (workspace, user, "membership.changed", "success")
    assert row[4].utcoffset() is not None
    assert row[5:] == ("api", trace, {"role": "viewer"})
    for statement in [
        "UPDATE audit_event SET action='changed'",
        "DELETE FROM audit_event",
        "TRUNCATE audit_event",
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            conn.execute(statement)
    assert conn.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 1
    with pytest.raises(psycopg.IntegrityError):
        conn.execute(
            "INSERT INTO audit_event(actor_type,action,result,source,trace_id,details)"
            " VALUES ('user','x','success','api',%s,'[]')",
            (uuid4(),),
        )


def test_first_and_repeat_login_preserve_identity_and_authorization(database, conn):
    from qtrade_application.identity import IdentityService
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_postgres.identity import PostgresIdentityStore

    service = IdentityService(PostgresIdentityStore(database[0], schema=database[1]))
    identity = VerifiedIdentity("https://sso.test", "subject-1", "Original", "one@example.test")
    first = service.initialize(identity)
    assert first.membership.role == Role.ADMIN
    assert first.personal_workspace.owner_id == first.user.user_id
    assert first.personal_workspace.live_trading_enabled is False
    conn.execute("UPDATE workspace SET name='Custom name',archived=true")
    conn.execute("UPDATE membership SET role='viewer',active=false")
    repeated = service.initialize(VerifiedIdentity(identity.issuer, identity.subject, "Renamed"))
    assert repeated.user.user_id == first.user.user_id
    assert repeated.user.display_name == "Renamed"
    assert repeated.user.email == "one@example.test"
    assert repeated.personal_workspace.workspace_id == first.personal_workspace.workspace_id
    assert repeated.personal_workspace.name == "Custom name"
    assert repeated.personal_workspace.archived is True
    assert repeated.membership.role == Role.VIEWER
    assert repeated.membership.active is False
    for table in ("qtrade_user", "workspace", "membership"):
        assert (
            conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            == 1
        )
    conn.execute("DELETE FROM membership")
    assert service.initialize(identity).membership is None  # Do not recreate removed authorization.


def test_concurrent_logins_only_create_one_user_and_workspace(database, conn):
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_postgres.identity import PostgresIdentityStore

    store = PostgresIdentityStore(database[0], schema=database[1])
    identity = VerifiedIdentity("https://sso.test", "shared-subject", "Concurrent user")
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(
            executor.map(lambda _: store.initialize(identity, workspace_name="Personal"), range(6))
        )
    assert len({item.user.user_id for item in results}) == 1
    assert len({item.personal_workspace.workspace_id for item in results}) == 1
    assert conn.execute("SELECT count(*) FROM membership").fetchone()[0] == 1


def test_subject_is_scoped_to_issuer_and_disabled_user_stays_disabled(database, conn):
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.identity import PostgresIdentityStore

    store = PostgresIdentityStore(database[0], schema=database[1])
    first_identity = VerifiedIdentity("https://issuer-one.test", "same-subject", "One")
    first = store.initialize(first_identity, workspace_name="Personal")
    second = store.initialize(
        VerifiedIdentity("https://issuer-two.test", "same-subject", "Two"),
        workspace_name="Personal",
    )
    assert first.user.user_id != second.user.user_id
    conn.execute("UPDATE qtrade_user SET disabled=true WHERE user_id=%s", (first.user.user_id,))
    with pytest.raises(PortError) as caught:
        store.initialize(first_identity, workspace_name="Personal")
    assert caught.value.code == ErrorCode.UNAUTHORIZED
    assert conn.execute(
        "SELECT disabled FROM qtrade_user WHERE user_id=%s", (first.user.user_id,)
    ).fetchone() == (True,)


def test_first_login_transaction_rolls_back_all_rows_on_membership_failure(database, conn):
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.identity import PostgresIdentityStore

    conn.execute("""CREATE FUNCTION fail_membership() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'simulated persistence failure'; END; $$;
        CREATE TRIGGER fail_membership BEFORE INSERT ON membership
        FOR EACH ROW EXECUTE FUNCTION fail_membership();""")
    store = PostgresIdentityStore(database[0], schema=database[1])
    with pytest.raises(PortError) as caught:
        store.initialize(
            VerifiedIdentity("https://sso.test", "new-subject", "New"), workspace_name="Personal"
        )
    assert caught.value.code == ErrorCode.UNAVAILABLE
    for table in ("qtrade_user", "workspace", "membership"):
        assert (
            conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            == 0
        )


def test_session_storage_encrypts_tokens_survives_restart_and_checks_live_authorization(
    database, conn
):
    from datetime import UTC, datetime

    from qtrade_application.sessions import SessionService, TokenSet
    from qtrade_crypto.tokens import TokenCipher
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.ports import PortError
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.sessions import PostgresSessionStore

    identity = PostgresIdentityStore(database[0], schema=database[1]).initialize(
        VerifiedIdentity("https://sso.test", "session-subject", "Session user"),
        workspace_name="Personal",
    )
    store = PostgresSessionStore(database[0], schema=database[1])
    cipher = TokenCipher({"test-v1": b"a" * 32}, primary="test-v1")
    now = datetime.now(UTC)
    service = SessionService(store, cipher, clock=lambda: now)
    tokens = TokenSet("private-access", "private-refresh", "private-id")
    created = service.create(identity, tokens, sso_sid="provider-session")
    row = conn.execute(
        "SELECT session_digest,csrf_digest,token_ciphertext,encryption_key_id FROM session"
    ).fetchone()
    assert row[:2] == (created.session.session_digest, created.session.csrf_digest)
    assert row[3] == "test-v1"
    for secret in (
        created.cookie,
        created.csrf,
        tokens.access_token,
        tokens.refresh_token,
        tokens.id_token,
    ):
        assert secret.encode() not in row[2]
        assert secret not in row[:2]
    restarted = SessionService(
        PostgresSessionStore(database[0], schema=database[1]), cipher, clock=lambda: now
    )
    assert restarted.tokens(created.cookie) == tokens
    assert restarted.csrf(created.cookie) == created.csrf
    for table, field in (
        ("qtrade_user", "disabled"),
        ("workspace", "archived"),
        ("membership", "active"),
    ):
        blocked = field != "active"
        conn.execute(
            sql.SQL("UPDATE {} SET {}=%s").format(sql.Identifier(table), sql.Identifier(field)),
            (blocked,),
        )
        with pytest.raises(PortError):
            restarted.authenticate(created.cookie)
        with pytest.raises(PortError):
            restarted.tokens(created.cookie)
        conn.execute(
            sql.SQL("UPDATE {} SET {}=%s").format(sql.Identifier(table), sql.Identifier(field)),
            (not blocked,),
        )
    restarted.revoke(created.cookie)
    with pytest.raises(PortError):
        service.authenticate(created.cookie)


def test_session_absolute_expiry_and_user_wide_revocation(database, conn):
    from datetime import UTC, datetime, timedelta

    from qtrade_application.sessions import SessionService, TokenSet
    from qtrade_crypto.tokens import TokenCipher
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.ports import PortError
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.sessions import PostgresSessionStore

    mapper = PostgresIdentityStore(database[0], schema=database[1])
    identity = mapper.initialize(
        VerifiedIdentity("https://sso.test", "one", "One"), workspace_name="Personal"
    )
    other = mapper.initialize(
        VerifiedIdentity("https://sso.test", "two", "Two"), workspace_name="Personal"
    )
    store = PostgresSessionStore(database[0], schema=database[1])
    clock = [datetime.now(UTC)]
    service = SessionService(
        store, TokenCipher({"v1": b"a" * 32}, primary="v1"), clock=lambda: clock[0]
    )
    first, second = [service.create(identity, TokenSet("access")) for _ in range(2)]
    unrelated = service.create(other, TokenSet("other-access"))
    clock[0] = first.session.expires_at - timedelta(microseconds=1)
    service.authenticate(first.cookie)
    clock[0] = first.session.expires_at
    with pytest.raises(PortError):
        service.authenticate(first.cookie)
    clock[0] = second.session.created_at + timedelta(minutes=1)
    assert store.revoke_user(identity.user.user_id, clock[0]) == 2
    assert store.revoke_user(identity.user.user_id, clock[0]) == 0
    for revoked in (first, second):
        with pytest.raises(PortError):
            service.tokens(revoked.cookie)
    assert service.tokens(unrelated.cookie).access_token == "other-access"


def logout_setup(database):
    from datetime import UTC, datetime

    from qtrade_application.sessions import SessionService, TokenSet
    from qtrade_crypto.tokens import TokenCipher
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.sessions import PostgresSessionStore

    mapper = PostgresIdentityStore(database[0], schema=database[1])
    users = [
        mapper.initialize(VerifiedIdentity(issuer, sub, sub), workspace_name="Personal")
        for issuer, sub in [
            ("https://sso.test", "one"),
            ("https://sso.test", "two"),
            ("https://other.test", "one"),
        ]
    ]
    service = SessionService(
        PostgresSessionStore(database[0], schema=database[1]),
        TokenCipher({"v1": b"a" * 32}, primary="v1"),
    )
    sessions = [service.create(user, TokenSet("access"), sso_sid="same-sid") for user in users]
    second = service.create(users[0], TokenSet("access"), sso_sid="other-sid")
    return datetime.now(UTC), service, users, sessions, second


def test_logout_durable_replay_and_subject_scope(database, conn):
    from datetime import timedelta

    from qtrade_application.sessions import TokenSet
    from qtrade_domain.logout_port import VerifiedLogout
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.logout import PostgresLogoutStore

    now, service, users, sessions, second = logout_setup(database)
    event = VerifiedLogout("https://sso.test", "one-jti", now + timedelta(minutes=2), subject="one")
    store = PostgresLogoutStore(database[0], schema=database[1])
    assert store.consume_and_revoke(event, now) == 2
    for revoked in (sessions[0], second):
        with pytest.raises(PortError):
            service.authenticate(revoked.cookie)
    for unchanged in sessions[1:]:
        service.authenticate(unchanged.cookie)
    new_login = service.create(users[0], TokenSet("fresh"))
    with pytest.raises(PortError) as replay:
        PostgresLogoutStore(database[0], schema=database[1]).consume_and_revoke(event, now)
    assert replay.value.code == ErrorCode.CONFLICT
    service.authenticate(new_login.cookie)
    assert conn.execute("SELECT count(*) FROM logout_receipt").fetchone()[0] == 1


def test_logout_concurrent_reservation_sid_scope_and_expiry(database, conn):
    from datetime import timedelta

    from qtrade_domain.logout_port import VerifiedLogout
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.logout import PostgresLogoutStore

    now, service, _, sessions, second = logout_setup(database)
    event = VerifiedLogout(
        "https://sso.test", "sid-jti", now + timedelta(minutes=2), sid="same-sid"
    )

    def consume(_):
        try:
            return PostgresLogoutStore(database[0], schema=database[1]).consume_and_revoke(
                event, now
            )
        except PortError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume, range(8)))
    assert results.count(2) == 1
    assert results.count(ErrorCode.CONFLICT) == 7
    for revoked in sessions[:2]:
        with pytest.raises(PortError):
            service.authenticate(revoked.cookie)
    service.authenticate(sessions[2].cookie)
    service.authenticate(second.cookie)
    with pytest.raises(PortError):
        PostgresLogoutStore(database[0], schema=database[1]).consume_and_revoke(
            event, event.expires_at
        )
    assert conn.execute("SELECT count(*) FROM logout_receipt").fetchone()[0] == 1


@pytest.mark.parametrize("failure_target", ["session", "audit_event"])
def test_logout_failure_rolls_back_receipt_and_revocation(database, conn, failure_target):
    from datetime import timedelta

    from qtrade_domain.logout_port import VerifiedLogout
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.logout import PostgresLogoutStore

    now, service, _, sessions, second = logout_setup(database)
    conn.execute("""CREATE FUNCTION fail_revoke() RETURNS trigger LANGUAGE plpgsql AS $$
                  BEGIN RAISE EXCEPTION 'test update failure'; END; $$""")
    operation = "UPDATE" if failure_target == "session" else "INSERT"
    conn.execute(
        sql.SQL(
            "CREATE TRIGGER fail_revoke BEFORE {} ON {} FOR EACH ROW EXECUTE FUNCTION fail_revoke()"
        ).format(sql.SQL(operation), sql.Identifier(failure_target))
    )
    event = VerifiedLogout(
        "https://sso.test", "retry-jti", now + timedelta(minutes=2), subject="one"
    )
    store = PostgresLogoutStore(database[0], schema=database[1])
    with pytest.raises(PortError) as failure:
        store.consume_and_revoke(event, now)
    assert failure.value.code == ErrorCode.UNAVAILABLE
    assert conn.execute("SELECT count(*) FROM logout_receipt").fetchone()[0] == 0
    for session in [*sessions, second]:
        service.authenticate(session.cookie)
    conn.execute(sql.SQL("DROP TRIGGER fail_revoke ON {}").format(sql.Identifier(failure_target)))
    assert store.consume_and_revoke(event, now) == 2


def test_workspace_live_roles_and_atomic_admin_management(database, conn):
    from qtrade_domain.authorization import Permission
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.workspaces import PostgresWorkspaceStore

    mapper = PostgresIdentityStore(database[0], schema=database[1])
    admin, viewer, unrelated = [
        mapper.initialize(
            VerifiedIdentity("https://sso.test", subject, subject), workspace_name=subject
        )
        for subject in ("admin", "viewer", "unrelated")
    ]
    workspace = admin.personal_workspace.workspace_id
    store = PostgresWorkspaceStore(database[0], schema=database[1])
    assert store.access(viewer.user.user_id, workspace) is None
    assert [a.workspace.workspace_id for a in store.accessible(viewer.user.user_id)] == [
        viewer.personal_workspace.workspace_id
    ]
    member = store.set_member(
        admin.user.user_id, workspace, viewer.user.user_id, role=Role.VIEWER, active=True
    )
    assert member.created_by == admin.user.user_id
    access = store.access(viewer.user.user_id, workspace)
    assert access is not None
    access.require(Permission.READ)
    with pytest.raises(PortError) as denied:
        access.require(Permission.STRATEGY_WRITE)
    assert denied.value.code == ErrorCode.REJECTED
    with pytest.raises(PortError):
        store.set_member(
            viewer.user.user_id, workspace, viewer.user.user_id, role=Role.ADMIN, active=True
        )
    with pytest.raises(PortError) as cross:
        store.members(unrelated.user.user_id, workspace)
    assert cross.value.code == ErrorCode.NOT_FOUND
    assert len(store.members(admin.user.user_id, workspace)) == 2
    store.set_member(
        admin.user.user_id, workspace, viewer.user.user_id, role=Role.ANALYST, active=True
    )
    assert store.access(viewer.user.user_id, workspace).role == Role.ANALYST
    store.set_member(
        admin.user.user_id, workspace, viewer.user.user_id, role=Role.ANALYST, active=False
    )
    assert store.access(viewer.user.user_id, workspace) is None
    assert conn.execute(
        "SELECT role,active FROM membership WHERE workspace_id=%s AND user_id=%s",
        (workspace, viewer.user.user_id),
    ).fetchone() == ("analyst", False)
    with pytest.raises(PortError) as orphan:
        store.set_member(
            admin.user.user_id, workspace, admin.user.user_id, role=Role.VIEWER, active=True
        )
    assert orphan.value.code == ErrorCode.CONFLICT
    assert store.access(admin.user.user_id, workspace).role == Role.ADMIN
    conn.execute("UPDATE qtrade_user SET disabled=true WHERE user_id=%s", (viewer.user.user_id,))
    with pytest.raises(PortError):
        store.set_member(
            admin.user.user_id, workspace, viewer.user.user_id, role=Role.ADMIN, active=True
        )
    conn.execute("UPDATE workspace SET archived=true WHERE workspace_id=%s", (workspace,))
    assert store.access(admin.user.user_id, workspace) is None


def test_concurrent_admin_removal_preserves_one_authorized_admin(database, conn):
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.workspaces import PostgresWorkspaceStore

    mapper = PostgresIdentityStore(database[0], schema=database[1])
    first, second = [
        mapper.initialize(
            VerifiedIdentity("https://sso.test", subject, subject), workspace_name=subject
        )
        for subject in ("one", "two")
    ]
    workspace = first.personal_workspace.workspace_id
    store = PostgresWorkspaceStore(database[0], schema=database[1])
    store.set_member(
        first.user.user_id, workspace, second.user.user_id, role=Role.ADMIN, active=True
    )

    def remove(user):
        try:
            store.set_member(user, workspace, user, role=Role.VIEWER, active=True)
            return "changed"
        except PortError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(remove, [first.user.user_id, second.user.user_id]))
    assert results.count("changed") == 1
    assert results.count(ErrorCode.CONFLICT) == 1
    assert (
        conn.execute(
            "SELECT count(*) FROM membership WHERE workspace_id=%s AND role='admin' AND active",
            (workspace,),
        ).fetchone()[0]
        == 1
    )


def test_workspace_http_authorization_uses_target_workspace_role(database, conn):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from qtrade_api.security import ApiSecurity
    from qtrade_api.workspaces import workspace_router
    from qtrade_application.sessions import SessionService, TokenSet
    from qtrade_application.workspaces import WorkspaceService
    from qtrade_crypto.tokens import TokenCipher
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_postgres.audit import PostgresAuditStore
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.sessions import PostgresSessionStore
    from qtrade_postgres.workspaces import PostgresWorkspaceStore
    from qtrade_runtime.cookies import SessionCookie

    mapper = PostgresIdentityStore(database[0], schema=database[1])
    owner, viewer, outsider = [
        mapper.initialize(
            VerifiedIdentity("https://sso.test", subject, subject), workspace_name=subject
        )
        for subject in ("owner", "viewer", "outsider")
    ]
    service = WorkspaceService(PostgresWorkspaceStore(database[0], schema=database[1]))
    sessions = SessionService(
        PostgresSessionStore(database[0], schema=database[1]),
        TokenCipher({"test": b"a" * 32}, primary="test"),
    )
    owner_session, viewer_session, outsider_session = [
        sessions.create(identity, TokenSet("access")) for identity in (owner, viewer, outsider)
    ]
    app = FastAPI()
    app.include_router(
        workspace_router(
            service,
            ApiSecurity(
                sessions,
                service,
                SessionCookie("test", insecure_local=True),
                PostgresAuditStore(database[0], schema=database[1]),
            ),
        )
    )
    client = TestClient(app)
    root = f"/api/v1/workspaces/{owner.personal_workspace.workspace_id}"
    target = root + f"/members/{viewer.user.user_id}"
    client.cookies.set("qtrade-session", owner_session.cookie)
    payload = {"role": "viewer", "active": True}
    assert client.put(target, json=payload).status_code == 403
    assert (
        client.put(target, json=payload, headers={"X-CSRF-Token": owner_session.csrf}).status_code
        == 200
    )
    client.cookies.set("qtrade-session", viewer_session.cookie)
    assert client.get(root).json()["role"] == "viewer"
    assert client.get(root + "/members").status_code == 403
    assert (
        client.put(
            target,
            json={"role": "admin", "active": True},
            headers={"X-CSRF-Token": viewer_session.csrf},
        ).status_code
        == 403
    )
    # Personal-workspace admin rights cannot carry into the target workspace.
    assert (
        client.get(f"/api/v1/workspaces/{viewer.personal_workspace.workspace_id}").json()["role"]
        == "admin"
    )
    client.cookies.set("qtrade-session", outsider_session.cookie)
    denied = client.get(root)
    assert denied.status_code == 404 and "owner" not in denied.text
    client.cookies.set("qtrade-session", owner_session.cookie)
    assert (
        client.put(
            target,
            json={"role": "analyst", "active": True},
            headers={"X-CSRF-Token": owner_session.csrf},
        ).status_code
        == 200
    )
    client.cookies.set("qtrade-session", viewer_session.cookie)
    assert client.get(root).json()["role"] == "analyst"
    client.cookies.set("qtrade-session", owner_session.cookie)
    assert (
        client.put(
            target,
            json={"role": "analyst", "active": False},
            headers={"X-CSRF-Token": owner_session.csrf},
        ).status_code
        == 200
    )
    client.cookies.set("qtrade-session", viewer_session.cookie)
    assert client.get(root).status_code == 404
    assert conn.execute(
        "SELECT role,active FROM membership WHERE workspace_id=%s AND user_id=%s",
        (owner.personal_workspace.workspace_id, viewer.user.user_id),
    ).fetchone() == ("analyst", False)

    denied_rows = conn.execute(
        "SELECT actor_type,actor_id,workspace_id,result,source,trace_id "
        "FROM audit_event WHERE action='permission.denied'"
    ).fetchall()
    assert len(denied_rows) == 5
    assert all(
        row[0] == "user"
        and row[1]
        and row[2]
        and row[3] == "denied"
        and row[4] == "api.permission"
        and row[5]
        for row in denied_rows
    )


def test_audit_adapter_context_and_transaction_rollback(database, conn):
    from datetime import UTC, datetime

    from qtrade_domain.audit_port import AuditRecord
    from qtrade_postgres.audit import PostgresAuditStore, append_in_transaction

    user, workspace = seed(conn)
    record = AuditRecord(
        workspace,
        "user",
        user,
        "auth.login",
        "success",
        datetime.now(UTC),
        "api",
        uuid4(),
        "127.0.0.1",
    )
    store = PostgresAuditStore(database[0], schema=database[1])
    event = store.append(record)
    row = conn.execute(
        "SELECT actor_id,workspace_id,action,result,occurred_at,source,trace_id "
        "FROM audit_event WHERE event_id=%s",
        (event,),
    ).fetchone()
    assert row == (
        user,
        workspace,
        "auth.login",
        "success",
        record.occurred_at,
        "api",
        record.trace_id,
    )
    with pytest.raises(RuntimeError):
        with conn.transaction():
            append_in_transaction(conn, record)
            raise RuntimeError("simulated business failure")
    assert conn.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 1


def test_identity_state_changes_have_atomic_safe_audit_records(database, conn):
    from qtrade_application.sessions import TokenSet
    from qtrade_domain.ports import PortError
    from qtrade_postgres.workspaces import PostgresWorkspaceStore

    _, sessions, users, created, _ = logout_setup(database)
    sessions.revoke(created[0].cookie)
    workspace = users[0].personal_workspace.workspace_id
    store = PostgresWorkspaceStore(database[0], schema=database[1])
    store.set_member(
        users[0].user.user_id, workspace, users[1].user.user_id, role=Role.VIEWER, active=True
    )
    rows = conn.execute(
        "SELECT actor_type,actor_id,workspace_id,action,result,occurred_at,source,trace_id,details "
        "FROM audit_event ORDER BY event_id"
    ).fetchall()
    assert [row[3] for row in rows] == ["auth.login"] * 4 + ["auth.logout", "membership.change"]
    assert all(
        row[0] == "user"
        and row[1]
        and row[2]
        and row[4] == "success"
        and row[5].tzinfo
        and row[6]
        and row[7]
        and isinstance(row[8], dict)
        for row in rows
    )
    assert rows[0][8]["session_digest"] == created[0].session.session_digest
    assert rows[-1][8] == {
        "target_id": str(users[1].user.user_id),
        "role_after": "viewer",
        "active_after": True,
    }
    conn.execute("""CREATE FUNCTION fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$
                   BEGIN RAISE EXCEPTION 'simulated audit failure'; END; $$""")
    conn.execute(
        "CREATE TRIGGER fail_audit BEFORE INSERT ON audit_event "
        "FOR EACH ROW EXECUTE FUNCTION fail_audit()"
    )
    before = conn.execute("SELECT count(*) FROM session").fetchone()[0]
    with pytest.raises(PortError):
        sessions.create(users[0], TokenSet("must-stay-private"))
    assert conn.execute("SELECT count(*) FROM session").fetchone()[0] == before
    with pytest.raises(PortError):
        sessions.revoke(created[1].cookie)
    sessions.authenticate(created[1].cookie)
    with pytest.raises(PortError):
        store.set_member(
            users[0].user.user_id, workspace, users[1].user.user_id, role=Role.ADMIN, active=True
        )
    assert store.access(users[1].user.user_id, workspace).role == Role.VIEWER
    assert conn.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 6


def test_audit_context_survives_session_and_global_logout_transactions(database, conn):
    from datetime import timedelta

    from qtrade_application.sessions import TokenSet
    from qtrade_domain.audit_port import AuditContext
    from qtrade_domain.logout_port import VerifiedLogout
    from qtrade_domain.ports import PortError
    from qtrade_postgres.logout import PostgresLogoutStore

    now, service, users, _, _ = logout_setup(database)
    login = AuditContext("api.auth.login", uuid4(), "203.0.113.7")
    created = service.create(users[0], TokenSet("never-audit-this-token"), audit_context=login)
    row = conn.execute(
        "SELECT source,source_ip::text,trace_id,details FROM audit_event WHERE trace_id=%s",
        (login.trace_id,),
    ).fetchone()
    assert row == (
        login.source,
        "203.0.113.7/32",
        login.trace_id,
        {"session_digest": created.session.session_digest},
    )
    logout = AuditContext("api.oidc.backchannel", uuid4(), "203.0.113.8")
    now = created.session.created_at + timedelta(seconds=1)
    event = VerifiedLogout(
        "https://sso.test", "context-event", now + timedelta(minutes=2), subject="one"
    )
    assert (
        PostgresLogoutStore(database[0], schema=database[1]).consume_and_revoke(
            event, now, audit_context=logout
        )
        == 3
    )
    with pytest.raises(PortError):
        service.authenticate(created.cookie)
    rows = conn.execute(
        "SELECT source,source_ip::text,trace_id,details FROM audit_event WHERE trace_id=%s",
        (logout.trace_id,),
    ).fetchall()
    assert rows == [
        (
            logout.source,
            "203.0.113.8/32",
            logout.trace_id,
            {"target_id": str(users[0].user.user_id)},
        )
    ]


def test_instrument_import_concurrency_conflicts_filters_and_immutable_specs(database, conn):
    from decimal import Decimal
    from pathlib import Path

    from qtrade_contracts import Instrument
    from qtrade_domain.instruments import InstrumentQuery
    from qtrade_postgres.instruments import PostgresInstrumentStore

    actor, workspace = seed(conn)
    item = Instrument.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Instrument.json").read_text()
    ).model_copy(update={"workspace_id": workspace})
    store = PostgresInstrumentStore(database[0], schema=database[1])
    with ThreadPoolExecutor(max_workers=8) as pool:
        imports = list(
            pool.map(lambda _: store.import_instruments(actor, workspace, (item,))[0], range(8))
        )
    assert [result.status for result in imports].count("created") == 1
    assert [result.status for result in imports].count("unchanged") == 7
    proposed = item.model_copy(update={"specification_version": 2, "tick_size": Decimal("0.020")})
    with ThreadPoolExecutor(max_workers=8) as pool:
        issues = list(
            pool.map(lambda _: store.import_instruments(actor, workspace, (proposed,))[0], range(8))
        )
    assert {result.status for result in issues} == {"conflict"}
    assert len({result.issue_id for result in issues}) == 1
    equivalent = proposed.model_copy(update={"tick_size": Decimal("0.02")})
    assert (
        store.import_instruments(actor, workspace, (equivalent,))[0].issue_id == issues[0].issue_id
    )
    assert store.query(InstrumentQuery(workspace)).instruments == (item,)
    (issue,) = store.conflicts(workspace)
    assert issue.existing == item and issue.proposed == proposed
    assert issue.status == "pending" and store.conflicts(uuid4()) == ()
    disabled = item.model_copy(update={"instrument_id": "disabled", "enabled": False})
    disabled = disabled.model_copy(update={"name": "Straße"})
    store.import_instruments(actor, workspace, (disabled,))
    assert store.query(InstrumentQuery(workspace, enabled=False)).instruments == (disabled,)
    assert store.query(InstrumentQuery(workspace, enabled=False, name="STRASSE")).instruments == (
        disabled,
    )
    assert store.query(
        InstrumentQuery(
            workspace, name=item.name.upper(), exchange=item.exchange, market=item.market
        )
    ).instruments == (item,)
    assert store.query(InstrumentQuery(uuid4(), enabled=None)).instruments == ()
    page = store.query(InstrumentQuery(workspace, enabled=None, limit=1))
    next_page = store.query(
        InstrumentQuery(workspace, enabled=None, after=page.next_after, limit=1)
    )
    assert len(page.instruments) == len(next_page.instruments) == 1
    assert page.instruments != next_page.instruments and next_page.next_after is None
    for statement in [
        "UPDATE instrument_specification SET imported_at=now()",
        "DELETE FROM instrument_specification",
        "TRUNCATE instrument_specification CASCADE",
    ]:
        with pytest.raises(psycopg.Error):
            conn.execute(statement)
    assert conn.execute("SELECT count(*) FROM instrument_specification").fetchone()[0] == 2


def test_instrument_import_storage_failure_rolls_back_whole_batch_and_rechecks_role(database, conn):
    from pathlib import Path

    from qtrade_contracts import Instrument
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.instruments import PostgresInstrumentStore

    actor, workspace = seed(conn)
    item = Instrument.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Instrument.json").read_text()
    ).model_copy(update={"workspace_id": workspace, "instrument_id": "a"})
    second = item.model_copy(update={"instrument_id": "z"})
    store = PostgresInstrumentStore(database[0], schema=database[1])
    conn.execute("""CREATE FUNCTION fail_instrument() RETURNS trigger LANGUAGE plpgsql AS $$
                   BEGIN IF NEW.instrument_id='z' THEN RAISE EXCEPTION 'simulated storage failure';
                   END IF; RETURN NEW; END; $$""")
    conn.execute(
        "CREATE TRIGGER fail_instrument BEFORE INSERT ON instrument_current "
        "FOR EACH ROW EXECUTE FUNCTION fail_instrument()"
    )
    with pytest.raises(PortError):
        store.import_instruments(actor, workspace, (item, second))
    assert conn.execute("SELECT count(*) FROM instrument_specification").fetchone()[0] == 0

    assert conn.execute("SELECT count(*) FROM instrument_current").fetchone()[0] == 0
    conn.execute("DROP TRIGGER fail_instrument ON instrument_current")
    conn.execute("UPDATE membership SET role='viewer' WHERE user_id=%s", (actor,))
    with pytest.raises(PortError) as denied:
        store.import_instruments(actor, workspace, (item,))
    assert denied.value.code == ErrorCode.REJECTED
    assert conn.execute("SELECT count(*) FROM instrument_specification").fetchone()[0] == 0


def test_instrument_http_uses_real_sessions_storage_and_current_workspace_roles(database, conn):
    import json
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from qtrade_api.instruments import instrument_router
    from qtrade_api.security import ApiSecurity
    from qtrade_application.instruments import InstrumentService
    from qtrade_application.sessions import SessionService, TokenSet
    from qtrade_application.workspaces import WorkspaceService
    from qtrade_crypto.tokens import TokenCipher
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_postgres.audit import PostgresAuditStore
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.instruments import PostgresInstrumentStore
    from qtrade_postgres.sessions import PostgresSessionStore
    from qtrade_postgres.workspaces import PostgresWorkspaceStore
    from qtrade_runtime.cookies import SessionCookie

    mapper = PostgresIdentityStore(database[0], schema=database[1])
    owner, other = [
        mapper.initialize(VerifiedIdentity("https://sso.test", name, name), workspace_name=name)
        for name in ("instrument-owner", "instrument-outsider")
    ]
    sessions = SessionService(
        PostgresSessionStore(database[0], schema=database[1]),
        TokenCipher({"test": b"a" * 32}, primary="test"),
    )
    session = sessions.create(owner, TokenSet("private-access-token"))
    workspace = owner.personal_workspace.workspace_id
    app = FastAPI()
    security = ApiSecurity(
        sessions,
        WorkspaceService(PostgresWorkspaceStore(database[0], schema=database[1])),
        SessionCookie("test", insecure_local=True),
        PostgresAuditStore(database[0], schema=database[1]),
    )
    app.include_router(
        instrument_router(
            InstrumentService(PostgresInstrumentStore(database[0], schema=database[1])), security
        )
    )
    client = TestClient(app)
    root = f"/api/v1/workspaces/{workspace}/instruments"
    assert client.get(root).status_code == 401
    client.cookies.set("qtrade-session", session.cookie)
    item = json.loads((Path(__file__).parent / "fixtures/contracts/Instrument.json").read_text())
    item["workspace_id"] = str(workspace)
    payload = {"instruments": [item]}
    assert client.post(root + "/import", json=payload).status_code == 403
    headers = {"X-CSRF-Token": session.csrf}
    response = client.post(root + "/import", json=payload, headers=headers)
    assert response.status_code == 200 and response.json()[0]["status"] == "created"
    assert client.get(root).json()["instruments"] == [item]
    proposed = {**item, "tick_size": "0.02", "specification_version": 2}
    assert (
        client.post(root + "/import", json={"instruments": [proposed]}, headers=headers).json()[0][
            "status"
        ]
        == "conflict"
    )
    assert client.get(root + "/conflicts").json()[0]["existing"] == item
    conn.execute(
        "UPDATE membership SET role='viewer' WHERE user_id=%s AND workspace_id=%s",
        (owner.user.user_id, workspace),
    )
    assert client.post(root + "/import", json=payload, headers=headers).status_code == 403
    assert client.get(root).status_code == 200
    assert (
        client.get(
            f"/api/v1/workspaces/{other.personal_workspace.workspace_id}/instruments"
        ).status_code
        == 404
    )
    assert conn.execute("SELECT count(*) FROM instrument_specification").fetchone()[0] == 1


def test_calendar_revisions_concurrency_permissions_and_immutability(database, conn):
    import json
    from dataclasses import replace
    from pathlib import Path

    from qtrade_contracts import CalendarSchedule
    from qtrade_domain.calendar_contracts import from_contract
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.calendars import PostgresCalendarStore

    actor, workspace = seed(conn)
    payload = json.loads(
        (Path(__file__).parent / "fixtures/contracts/CalendarSchedule.json").read_text()
    )
    payload["workspace_id"] = str(workspace)
    value = from_contract(CalendarSchedule.model_validate_json(json.dumps(payload)))
    store = PostgresCalendarStore(database[0], schema=database[1])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.put(actor, value), range(8)))
    assert conn.execute("SELECT count(*) FROM calendar_schedule").fetchone()[0] == 1
    assert store.get(workspace, value.calendar.calendar_id) == value
    next_value = replace(value, calendar=replace(value.calendar, version=2))
    store.put(actor, next_value)
    assert store.get(workspace, value.calendar.calendar_id) == next_value
    assert store.get(workspace, value.calendar.calendar_id, 1) == value
    with pytest.raises(PortError) as conflict:
        store.put(actor, replace(value, calendar=replace(value.calendar, weekdays=frozenset())))
    assert conflict.value.code == ErrorCode.CONFLICT
    with pytest.raises(PortError) as missing:
        store.get(uuid4(), value.calendar.calendar_id)
    assert missing.value.code == ErrorCode.NOT_FOUND
    for statement in (
        "UPDATE calendar_schedule SET calendar_version=3",
        "DELETE FROM calendar_schedule",
        "TRUNCATE calendar_schedule",
    ):
        with pytest.raises(psycopg.Error):
            conn.execute(statement)
    conn.execute("UPDATE membership SET role='viewer' WHERE user_id=%s", (actor,))
    with pytest.raises(PortError) as denied:
        store.put(actor, replace(value, calendar=replace(value.calendar, version=3)))
    assert denied.value.code == ErrorCode.REJECTED
    assert conn.execute("SELECT count(*) FROM calendar_schedule").fetchone()[0] == 2


def test_raw_batch_metadata_concurrent_retry_immutability_roles_and_scope(database, conn):
    from pathlib import Path

    from qtrade_contracts import DataBatch
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.batches import PostgresBatchRepository

    actor, workspace = seed(conn)
    original = DataBatch.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/DataBatch.json").read_text()
    )
    batch = original.model_copy(update={"workspace_id": workspace})
    store = PostgresBatchRepository(database[0], schema=database[1])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.put(actor, batch), range(8)))
    assert conn.execute("SELECT count(*) FROM raw_data_batch").fetchone()[0] == 1
    assert store.get(workspace, batch.batch_id) == batch
    with pytest.raises(PortError) as conflict:
        store.put(actor, batch.model_copy(update={"source": "changed"}))
    assert conflict.value.code == ErrorCode.CONFLICT
    with pytest.raises(PortError) as missing:
        store.get(uuid4(), batch.batch_id)
    assert missing.value.code == ErrorCode.NOT_FOUND
    for statement in (
        "UPDATE raw_data_batch SET source='changed'",
        "DELETE FROM raw_data_batch",
        "TRUNCATE raw_data_batch",
    ):
        with pytest.raises(psycopg.Error):
            conn.execute(statement)
    conn.execute("UPDATE membership SET role='viewer' WHERE user_id=%s", (actor,))
    with pytest.raises(PortError) as denied:
        store.put(actor, batch)
    assert denied.value.code == ErrorCode.REJECTED
    conn.execute("UPDATE workspace SET archived=true WHERE workspace_id=%s", (workspace,))
    with pytest.raises(PortError) as archived:
        store.put(actor, batch)
    assert archived.value.code == ErrorCode.NOT_FOUND


def test_raw_publication_postgres_rechecks_stale_role_before_metadata_commit(database, conn):
    from dataclasses import replace
    from datetime import UTC, datetime

    from qtrade_application.batches import RawBatchService
    from qtrade_domain.batches import BatchInput
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_domain.raw_records import RawRecord
    from qtrade_mock.ports import MockObjectStore
    from qtrade_parquet.raw_records import ParquetRawRecordCodec
    from qtrade_postgres.batches import PostgresBatchRepository
    from qtrade_postgres.workspaces import PostgresWorkspaceStore

    actor, workspace = seed(conn)
    access = PostgresWorkspaceStore(database[0], schema=database[1]).access(actor, workspace)
    assert access is not None
    repository = PostgresBatchRepository(database[0], schema=database[1])
    objects = MockObjectStore()
    service = RawBatchService(repository, objects, ParquetRawRecordCodec())
    draft = BatchInput(
        workspace,
        uuid4(),
        "provider",
        "license-ref",
        uuid4(),
        "TEST:ABC",
        "1m",
        datetime.now(UTC),
        (RawRecord(0, b'{"price":10.000000000000000001}', "application/json"),),
    )
    batch = service.publish(access, draft)
    assert service.read(access, batch.batch_id).records == draft.records
    assert service.publish(access, draft) == batch
    conn.execute("UPDATE membership SET role='viewer' WHERE user_id=%s", (actor,))
    new = replace(draft, batch_id=uuid4())
    with pytest.raises(PortError) as denied:
        service.publish(access, new)
    assert denied.value.code == ErrorCode.REJECTED
    with pytest.raises(PortError) as unpublished:
        repository.get(workspace, new.batch_id)
    assert unpublished.value.code == ErrorCode.NOT_FOUND
    assert conn.execute("SELECT count(*) FROM raw_data_batch").fetchone()[0] == 1
    assert service.read(access, batch.batch_id).metadata == batch


def test_raw_batches_use_actual_parquet_minio_and_postgres_immutable_publication(database, conn):
    from dataclasses import replace
    from datetime import UTC, datetime

    from qtrade_application.batches import RawBatchService
    from qtrade_domain.batches import BatchInput
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_domain.raw_records import RawRecord
    from qtrade_object_store.s3 import S3ObjectStore
    from qtrade_parquet.raw_records import ParquetRawRecordCodec
    from qtrade_postgres.batches import PostgresBatchRepository
    from qtrade_postgres.workspaces import PostgresWorkspaceStore

    endpoint = os.environ.get("QTRADE_TEST_OBJECTSTORE_ENDPOINT")
    if not endpoint:
        pytest.skip("requires explicit local MinIO target")
    bucket = "qtrade-raw-batch-test-" + uuid4().hex
    objects = S3ObjectStore.connect(
        bucket=bucket,
        endpoint_url=endpoint,
        allow_http=True,
        access_key=os.environ["QTRADE_TEST_OBJECTSTORE_ACCESS"],
        secret_key=os.environ["QTRADE_TEST_OBJECTSTORE_SECRET"],
    )
    client = objects._client
    client.create_bucket(Bucket=bucket)
    try:
        actor, workspace = seed(conn)
        access = PostgresWorkspaceStore(database[0], schema=database[1]).access(actor, workspace)
        assert access is not None
        repository = PostgresBatchRepository(database[0], schema=database[1])
        service = RawBatchService(repository, objects, ParquetRawRecordCodec())
        draft = BatchInput(
            workspace,
            uuid4(),
            "provider",
            "license-ref",
            uuid4(),
            "TEST:ABC",
            "1m",
            datetime.now(UTC),
            (
                RawRecord(0, b'{"price":12345678901234567890.000000000001}', "application/json"),
                RawRecord(1, b"original,csv\r\n10.00,20.00\r\n", "text/csv"),
            ),
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = tuple(pool.map(lambda _: service.publish(access, draft), range(8)))
        batch = results[0]
        assert all(item == batch for item in results)
        assert conn.execute("SELECT count(*) FROM raw_data_batch").fetchone()[0] == 1
        assert len(client.list_objects_v2(Bucket=bucket).get("Contents", [])) == 1
        restored = RawBatchService(
            PostgresBatchRepository(database[0], schema=database[1]),
            objects,
            ParquetRawRecordCodec(),
        ).read(access, batch.batch_id)
        assert restored.records == draft.records
        with pytest.raises(PortError) as conflict:
            service.publish(access, replace(draft, records=(RawRecord(0, b"changed"),)))
        assert conflict.value.code == ErrorCode.CONFLICT
        assert service.read(access, batch.batch_id) == restored
        client.put_object(
            Bucket=bucket,
            Key=f"workspaces/{workspace}/{batch.object_key}",
            Body=b"corrupt",
            ContentType="application/vnd.apache.parquet",
            Metadata={"sha256": batch.content_sha256},
        )
        with pytest.raises(PortError) as corruption:
            service.read(access, batch.batch_id)
        assert corruption.value.code == ErrorCode.CHECKSUM_MISMATCH
    finally:
        for item in client.list_objects_v2(Bucket=bucket).get("Contents", []):
            client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)


def test_dataset_publication_atomic_sources_lineage_immutability_and_current_role(database, conn):
    from dataclasses import replace
    from pathlib import Path

    from qtrade_contracts import DataBatch, MarketDataset
    from qtrade_domain.datasets import PublishedDataset
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.batches import PostgresBatchRepository
    from qtrade_postgres.datasets import PostgresDatasetRepository

    actor, workspace = seed(conn)
    fixtures = Path(__file__).parent / "fixtures/contracts"
    batch = DataBatch.model_validate_json((fixtures / "DataBatch.json").read_text()).model_copy(
        update={"workspace_id": workspace, "batch_id": uuid4()}
    )
    PostgresBatchRepository(database[0], schema=database[1]).put(actor, batch)
    metadata = MarketDataset.model_validate_json(
        (fixtures / "MarketDataset.json").read_text()
    ).model_copy(
        update={"workspace_id": workspace, "dataset_id": uuid4(), "batch_ids": (batch.batch_id,)}
    )
    item = PublishedDataset(metadata, "1" * 64, None, "2" * 64)
    repo = PostgresDatasetRepository(database[0], schema=database[1])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: repo.put(actor, item), range(8)))
    assert repo.get(workspace, metadata.dataset_id) == item
    assert repo.list(workspace) == (item,)
    assert repo.list(uuid4()) == ()
    assert conn.execute("SELECT count(*) FROM market_dataset").fetchone()[0] == 1
    assert conn.execute("SELECT batch_id,ordinal FROM market_dataset_batch").fetchall() == [
        (batch.batch_id, 0)
    ]
    with pytest.raises(PortError) as conflict:
        repo.put(actor, replace(item, request_sha256="3" * 64))
    assert conflict.value.code == ErrorCode.CONFLICT
    with pytest.raises(PortError) as cross:
        repo.get(uuid4(), metadata.dataset_id)
    assert cross.value.code == ErrorCode.NOT_FOUND
    child = replace(
        item,
        metadata=metadata.model_copy(update={"dataset_id": uuid4(), "version": 2}),
        parent_dataset_id=metadata.dataset_id,
    )
    repo.put(actor, child)
    assert repo.get(workspace, child.metadata.dataset_id) == child
    assert {item.metadata.dataset_id for item in repo.list(workspace)} == {
        metadata.dataset_id,
        child.metadata.dataset_id,
    }
    assert repo.get(workspace, metadata.dataset_id) == item
    first = repo.for_instrument(workspace, metadata.instrument_ids[0], limit=1)
    second = repo.for_instrument(
        workspace, metadata.instrument_ids[0], limit=1, before=first[0].metadata.dataset_id
    )
    assert {first[0].metadata.dataset_id, second[0].metadata.dataset_id} == {
        metadata.dataset_id,
        child.metadata.dataset_id,
    }
    assert repo.for_instrument(uuid4(), metadata.instrument_ids[0]) == ()
    assert repo.for_instrument(workspace, "UNKNOWN") == ()
    assert repo.for_instrument(workspace, metadata.instrument_ids[0], granularity="1d") == ()
    for table in ("market_dataset", "market_dataset_batch"):
        for operation in (
            f"UPDATE {table} SET workspace_id=workspace_id",
            f"DELETE FROM {table}",
            f"TRUNCATE {table} CASCADE",
        ):
            with pytest.raises(psycopg.Error):
                conn.execute(operation)
    with pytest.raises(psycopg.Error):
        conn.execute(
            "INSERT INTO market_dataset_batch VALUES (%s,%s,%s,1)",
            (workspace, metadata.dataset_id, uuid4()),
        )
    with pytest.raises(PortError) as invalid:
        repo.put(
            actor,
            replace(
                child,
                metadata=child.metadata.model_copy(update={"dataset_id": uuid4(), "version": 7}),
            ),
        )
    assert invalid.value.code == ErrorCode.INVALID_INPUT
    conn.execute("UPDATE membership SET role='viewer' WHERE user_id=%s", (actor,))
    with pytest.raises(PortError) as denied:
        repo.put(actor, item)
    assert denied.value.code == ErrorCode.REJECTED
    assert repo.get(workspace, metadata.dataset_id) == item


def test_dataset_missing_source_rolls_back_metadata_and_partial_sql_publication_is_rejected(
    database, conn
):
    from pathlib import Path

    from qtrade_contracts import DataBatch, MarketDataset
    from qtrade_domain.datasets import PublishedDataset
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.batches import PostgresBatchRepository
    from qtrade_postgres.datasets import PostgresDatasetRepository

    actor, workspace = seed(conn)
    fixtures = Path(__file__).parent / "fixtures/contracts"
    batch = DataBatch.model_validate_json((fixtures / "DataBatch.json").read_text()).model_copy(
        update={"workspace_id": workspace, "batch_id": uuid4()}
    )
    PostgresBatchRepository(database[0], schema=database[1]).put(actor, batch)
    metadata = MarketDataset.model_validate_json(
        (fixtures / "MarketDataset.json").read_text()
    ).model_copy(
        update={
            "workspace_id": workspace,
            "dataset_id": uuid4(),
            "batch_ids": (batch.batch_id, uuid4()),
        }
    )
    item = PublishedDataset(metadata, "1" * 64, None, "2" * 64)
    repo = PostgresDatasetRepository(database[0], schema=database[1])
    with pytest.raises(PortError) as missing:
        repo.put(actor, item)
    assert missing.value.code == ErrorCode.INVALID_INPUT
    assert conn.execute("SELECT count(*) FROM market_dataset").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM market_dataset_batch").fetchone()[0] == 0
    # Even privileged SQL cannot commit metadata without its complete source links.
    with pytest.raises(psycopg.Error):
        conn.execute(
            """INSERT INTO market_dataset(workspace_id,dataset_id,version,request_sha256,
                        quality_report_sha256,payload,published_by)
                        VALUES (%s,%s,1,%s,%s,%s::jsonb,%s)""",
            (workspace, metadata.dataset_id, "1" * 64, "2" * 64, metadata.model_dump_json(), actor),
        )
    assert conn.execute("SELECT count(*) FROM market_dataset").fetchone()[0] == 0


def test_ingestion_postgres_restart_persists_staged_pages_leases_checkpoints_and_throttle(
    conn, database
):
    from datetime import UTC, datetime, timedelta

    from qtrade_domain.ingestion import CollectionRequest, ProviderPage, StagedPage
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_domain.raw_records import RawRecord
    from qtrade_postgres.ingestion import PostgresIngestionRepository

    actor, workspace = seed(conn)
    now, task_id, worker_id = datetime(2026, 10, 1, tzinfo=UTC), uuid4(), uuid4()
    request = CollectionRequest(
        workspace,
        task_id,
        "provider",
        "license-ref",
        "TEST:ABC",
        "1m",
        now,
        now + timedelta(days=1),
        now,
        "test.transform.v1",
        page_size=100,
    )
    repository = PostgresIngestionRepository(database[0], schema=database[1])
    created = repository.create(actor, request)
    assert created == repository.create(actor, request)
    assert repository.ready_tasks(now) == (created,)
    next_request = replace(request, task_id=uuid4())
    assert repository.reserve_rate_slot(request, now, timedelta(seconds=5)) == now
    assert repository.reserve_rate_slot(next_request, now, timedelta(seconds=5)) == now + timedelta(
        seconds=5
    )
    repository.defer_source(request, now + timedelta(seconds=30))
    assert repository.reserve_rate_slot(
        next_request, now + timedelta(seconds=10), timedelta(seconds=1)
    ) == now + timedelta(seconds=30)

    first = repository.claim(actor, worker_id, workspace, task_id, now, now + timedelta(seconds=5))
    assert first is not None
    records = (
        RawRecord(0, b'{"price":12345678901234567890.000000000001}'),
        RawRecord(1, b"line,with,bytes\r\n"),
    )
    pending = StagedPage(ProviderPage(records, "continuation-token"), now)
    staged = repository.stage(first, pending, now)
    assert staged.task.pending == pending
    assert repository.ready_tasks(now) == ()  # Active lease is not claimable by another worker.
    assert (
        PostgresIngestionRepository(database[0], schema=database[1]).get(workspace, task_id).pending
        == pending
    )
    held = repository.claim(actor, uuid4(), workspace, task_id, now, now + timedelta(seconds=8))
    assert held is None
    # An expired lease may be replaced; the old worker is fenced from committing a checkpoint.
    replacement = repository.claim(
        actor, uuid4(), workspace, task_id, now + timedelta(seconds=5), now + timedelta(seconds=10)
    )
    assert replacement is not None and replacement.token != first.token
    with pytest.raises(PortError) as stale:
        repository.checkpoint(staged, request.batch_id(0), now + timedelta(seconds=5))
    assert stale.value.code == ErrorCode.CONFLICT
    checkpointed = repository.checkpoint(
        replacement, request.batch_id(0), now + timedelta(seconds=5)
    )
    assert checkpointed.cursor == "continuation-token" and checkpointed.batch_ids == (
        request.batch_id(0),
    )
    assert checkpointed.pending is None and not checkpointed.collection_complete
    assert repository.ready_tasks(now + timedelta(seconds=5)) == (checkpointed,)
    assert (
        PostgresIngestionRepository(database[0], schema=database[1]).get(workspace, task_id)
        == checkpointed
    )
    current = repository.claim(
        actor, uuid4(), workspace, task_id, now + timedelta(seconds=5), now + timedelta(seconds=10)
    )
    assert current is not None
    delayed = repository.schedule(
        current,
        ErrorCode.TIMEOUT,
        now + timedelta(seconds=20),
        now + timedelta(seconds=5),
        terminal=True,
    )
    assert (
        delayed.status == "failed"
        and delayed.pending is None
        and delayed.cursor == "continuation-token"
    )
    resumed = repository.requeue(actor, workspace, task_id, now + timedelta(seconds=21))
    assert (
        resumed.status == "queued"
        and resumed.failures == 0
        and resumed.cursor == "continuation-token"
    )
    assert resumed.batch_ids == (request.batch_id(0),)
    saved = conn.execute(
        """SELECT cardinality(batch_ids),pending_page,lease_token
        FROM ingestion_task WHERE task_id=%s""",
        (task_id,),
    ).fetchone()
    assert saved == (1, None, None)


def test_ingestion_postgres_serializes_claims_and_rechecks_actor_permission(conn, database):
    from datetime import UTC, datetime, timedelta

    from qtrade_domain.ingestion import CollectionRequest
    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_postgres.ingestion import PostgresIngestionRepository

    actor, workspace = seed(conn)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    request = CollectionRequest(
        workspace,
        uuid4(),
        "provider",
        "license-ref",
        "TEST:ABC",
        "1m",
        now,
        now + timedelta(days=1),
        now,
        "test.transform.v1",
    )
    repository = PostgresIngestionRepository(database[0], schema=database[1])
    repository.create(actor, request)

    def claim(_):
        return repository.claim(
            actor, uuid4(), workspace, request.task_id, now, now + timedelta(minutes=1)
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = tuple(pool.map(claim, range(8)))
    assert sum(lease is not None for lease in leases) == 1
    lease = next(item for item in leases if item is not None)
    conn.execute(
        "UPDATE membership SET role='viewer' WHERE workspace_id=%s AND user_id=%s",
        (workspace, actor),
    )
    assert repository.ready_tasks(now + timedelta(minutes=2)) == ()
    with pytest.raises(PortError) as denied:
        repository.schedule(
            lease, ErrorCode.UNAVAILABLE, now + timedelta(seconds=1), now, terminal=False
        )
    assert denied.value.code == ErrorCode.REJECTED
    conn.execute(
        "UPDATE membership SET role='admin' WHERE workspace_id=%s AND user_id=%s",
        (workspace, actor),
    )
    replacement = repository.claim(
        actor,
        uuid4(),
        workspace,
        request.task_id,
        now + timedelta(minutes=1),
        now + timedelta(minutes=2),
    )
    assert replacement is not None and replacement.token != lease.token
    assert repository.get(workspace, request.task_id).status == "running"


def test_quality_issue_persists_evidence_and_fences_lifecycle_mutations(conn, database):
    from datetime import UTC, datetime, timedelta

    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_domain.quality import QualityIssue
    from qtrade_postgres.quality import PostgresQualityIssueStore

    actor, workspace = seed(conn)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    issue = QualityIssue(
        uuid4(),
        workspace,
        uuid4(),
        "price_jump",
        "critical",
        "TEST:ABC",
        "1m",
        now,
        now + timedelta(minutes=1),
        (uuid4(),),
        '{"change_ratio":"1"}',
        now,
    )
    store = PostgresQualityIssueStore(database[0], schema=database[1])
    store.record_scan(actor, (issue,))
    store.record_scan(actor, (issue,))
    assert store.list_issues(workspace, issue.dataset_id) == (issue,)
    claimed = store.claim_issue(actor, workspace, issue.issue_id, now)
    assert claimed.status == "claimed" and claimed.claimed_by == actor
    closed = store.close_issue(actor, workspace, issue.issue_id, now + timedelta(minutes=1))
    assert closed.status == "closed" and closed.closed_by == actor
    with pytest.raises(PortError) as duplicate:
        store.close_issue(actor, workspace, issue.issue_id, now + timedelta(minutes=2))
    assert duplicate.value.code == ErrorCode.CONFLICT
    with pytest.raises(psycopg.Error, match="evidence is immutable"):
        conn.execute(
            "UPDATE data_quality_issue SET evidence='{}'::jsonb WHERE issue_id=%s",
            (issue.issue_id,),
        )
    with pytest.raises(psycopg.Error, match="evidence cannot be deleted"):
        conn.execute("DELETE FROM data_quality_issue WHERE issue_id=%s", (issue.issue_id,))


def test_realtime_health_snapshot_round_trips_and_rechecks_writer_role(conn, database):
    from datetime import UTC, datetime

    from qtrade_domain.ports import ErrorCode, PortError
    from qtrade_domain.realtime import MarketStreamHealth, MarketStreamKey
    from qtrade_postgres.realtime import PostgresRealtimeHealthStore

    actor, workspace = seed(conn)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    key = MarketStreamKey(workspace, "mock-feed", "TEST:ABC")
    health = MarketStreamHealth(
        key,
        "healthy",
        now,
        heartbeat_at=now,
        last_event_at=now,
        last_sequence=10,
        latest_lag_ms=15,
        max_lag_ms=200,
        out_of_order_count=2,
        reconnect_count=1,
    )
    store = PostgresRealtimeHealthStore(database[0], schema=database[1])
    store.put(actor, health)
    assert store.get(key) == health
    assert store.list(workspace) == (health,)
    conn.execute(
        "UPDATE membership SET role='viewer' WHERE workspace_id=%s AND user_id=%s",
        (workspace, actor),
    )
    with pytest.raises(PortError) as denied:
        store.put(actor, health)
    assert denied.value.code == ErrorCode.REJECTED


def test_alert_escalation_bypasses_cooldown_and_resets_after_recovery(conn, database):
    from datetime import UTC, datetime, timedelta

    _, workspace = seed(conn)
    rule = uuid4()
    now = datetime(2026, 10, 1, tzinfo=UTC)
    store = PostgresAlertEvaluationStore(database[0], schema=database[1])
    assert (
        store.decision(workspace, rule, True, now, 3600, AlertSeverity.WARNING, 60, False)
        == AlertSeverity.WARNING
    )
    assert (
        store.decision(
            workspace,
            rule,
            True,
            now + timedelta(seconds=60),
            3600,
            AlertSeverity.WARNING,
            60,
            True,
        )
        == AlertSeverity.CRITICAL
    )
    assert (
        store.decision(
            workspace,
            rule,
            True,
            now + timedelta(seconds=70),
            3600,
            AlertSeverity.WARNING,
            60,
            False,
        )
        is None
    )
    assert (
        store.decision(
            workspace,
            rule,
            False,
            now + timedelta(seconds=80),
            3600,
            AlertSeverity.WARNING,
            60,
            False,
        )
        is None
    )
    assert (
        store.decision(
            workspace,
            rule,
            True,
            now + timedelta(seconds=90),
            3600,
            AlertSeverity.WARNING,
            60,
            False,
        )
        == AlertSeverity.WARNING
    )


def test_alert_evaluation_queue_leases_expire_and_fence_stale_workers(conn, database):
    from datetime import UTC, datetime, timedelta

    actor, workspace = seed(conn)
    now = datetime.now(UTC)
    context = json.dumps({"market_summary": {"price": 101}})
    job = AlertEvaluationJob(
        uuid4(),
        workspace,
        actor,
        AlertSource.MARKET,
        (("price", 101),),
        now,
        context_snapshot_json=context,
    )
    queue = PostgresAlertEvaluationQueue(database[0], schema=database[1])
    assert queue.enqueue(job) == job
    first = queue.ready(now + timedelta(seconds=1), 10, 30)[0]
    assert first.job == job and first.attempts == 1
    assert queue.ready(now + timedelta(seconds=2), 10, 30) == ()
    second = queue.ready(now + timedelta(seconds=32), 10, 30)[0]
    assert second.job == job and second.attempts == 2 and second.token != first.token
    assert not queue.finish(first, now + timedelta(seconds=33), succeeded=True)
    assert queue.finish(second, now + timedelta(seconds=33), succeeded=True)
    assert queue.ready(now + timedelta(seconds=34), 10, 30) == ()


def test_notification_delivery_retries_record_safe_attempts_and_in_app_inbox(conn, database):
    from datetime import UTC, datetime, timedelta

    _, workspace = seed(conn)
    now = datetime.now(UTC)
    notification = Notification(
        workspace, uuid4(), "in_app", str(workspace), "Alert", "Private details remain scoped"
    )
    queue = PostgresNotificationDeliveryQueue(database[0], schema=database[1])
    queue.enqueue(notification, now)
    lease = queue.ready(now + timedelta(seconds=1), 10, 30)[0]
    stale = type(lease)(lease.notification, uuid4(), lease.attempt)
    retrying = NotificationAttempt(
        workspace,
        notification.notification_id,
        "in_app",
        str(workspace),
        "retrying",
        "unavailable",
        now + timedelta(seconds=1),
        1,
    )
    assert not queue.finish(stale, retrying, retry_after_seconds=10)
    assert queue.finish(lease, retrying, retry_after_seconds=10)
    second = queue.ready(now + timedelta(seconds=12), 10, 30)[0]
    delivered = NotificationAttempt(
        workspace,
        notification.notification_id,
        "in_app",
        str(workspace),
        "delivered",
        "delivered:receipt-id",
        now + timedelta(seconds=12),
        2,
    )
    assert queue.finish(second, delivered, retry_after_seconds=None)
    attempts = queue.attempts(workspace, notification.notification_id)
    assert [item.status for item in attempts] == ["retrying", "delivered"]
    assert all("Private details" not in item.response_summary for item in attempts)

    inbox = PostgresNotificationInbox(database[0], schema=database[1])
    assert inbox.record(notification, now).status == "delivered"
    assert inbox.list(workspace, str(workspace)) == (notification,)
    assert inbox.list(uuid4(), str(workspace)) == ()


def test_alert_rule_notification_targets_round_trip_as_immutable_version(conn, database):
    from datetime import UTC, datetime

    _, workspace = seed(conn)
    rule = AlertRuleVersion(
        uuid4(),
        workspace,
        1,
        "Price alert",
        "market",
        "price > 100",
        "warning",
        60,
        True,
        datetime.now(UTC),
        (("email", "alerts@example.com"), ("webhook", "risk-endpoint")),
    )
    store = PostgresAlertRuleStore(database[0], schema=database[1])
    assert store.create(rule) == rule
    assert store.current(workspace, rule.rule_id) == rule
    assert store.versions(workspace, rule.rule_id) == (rule,)


def test_alert_event_context_snapshot_round_trips_immutably(conn, database):
    from datetime import UTC, datetime

    _, workspace = seed(conn)
    now = datetime.now(UTC)
    event = AlertEvent(
        uuid4(),
        workspace,
        AlertSource.DATA,
        "quality-event-1",
        "rule.triggered",
        AlertSeverity.WARNING,
        AlertStatus.OPEN,
        "Missing bars",
        "A data gap was detected",
        "600519.SH",
        now,
        now,
        json.dumps({"rule": {"version": 3}, "data_quality": {"missing_count": 12}}),
    )
    store = PostgresAlertEventStore(database[0], schema=database[1])
    assert store.record(event) == event
    assert store.list(workspace, 10) == (event,)
    assert store.record(event) == event


def test_alert_event_postgres_disposition_transaction_and_immutable_history(conn, database):
    from datetime import UTC, datetime, timedelta

    actor, workspace = seed(conn)
    now = datetime.now(UTC)
    event = AlertEvent(
        uuid4(),
        workspace,
        AlertSource.RISK,
        "risk-event-claim",
        "risk.limit",
        AlertSeverity.CRITICAL,
        AlertStatus.OPEN,
        "Exposure limit",
        "Exposure exceeded",
        "600519.SH",
        now,
        now,
    )
    store = PostgresAlertEventStore(database[0], schema=database[1])
    store.record(event)
    assert store.get(workspace, event.event_id) == event
    assert (
        store.handle(
            actor, workspace, event.event_id, "claim", now + timedelta(seconds=1)
        ).assignee_id
        == actor
    )
    assert (
        store.handle(
            actor, workspace, event.event_id, "acknowledge", now + timedelta(seconds=2)
        ).status
        == AlertStatus.ACKNOWLEDGED
    )
    expiry = now + timedelta(hours=2)
    assert (
        store.handle(
            actor,
            workspace,
            event.event_id,
            "silence",
            now + timedelta(seconds=3),
            silenced_until=expiry,
        ).silenced_until
        == expiry
    )
    store.handle(
        actor, workspace, event.event_id, "note", now + timedelta(seconds=4), note="Reviewed"
    )
    assert (
        store.handle(actor, workspace, event.event_id, "close", now + timedelta(seconds=5)).status
        == AlertStatus.CLOSED
    )
    assert [row.action for row in store.history(workspace, event.event_id)] == [
        "claim",
        "acknowledge",
        "silence",
        "note",
        "close",
    ]
    with pytest.raises(PortError) as conflict:
        store.handle(actor, workspace, event.event_id, "acknowledge", now + timedelta(seconds=6))
    assert conflict.value.code == ErrorCode.CONFLICT
    with pytest.raises(psycopg.Error, match="immutable"):
        conn.execute("DELETE FROM alert_disposition_history WHERE event_id=%s", (event.event_id,))


def test_strategy_postgres_versions_are_workspace_scoped_and_immutable(conn, database):
    from datetime import UTC, datetime, timedelta
    from hashlib import sha256

    from qtrade_domain.strategies import (
        StrategyStateTransition,
        StrategyVersionApproval,
        StrategyVersionLifecycle,
        StrategyVersionState,
    )

    actor, workspace = seed(conn)
    now = datetime.now(UTC)
    strategy_id = uuid4()
    strategy = Strategy(strategy_id, workspace, "Test Strategy", "", actor, now, now, 1)
    code_hash = sha256(b"pass\n").hexdigest()
    deps_hash = sha256(b"{}").hexdigest()
    first = StrategyVersion(
        schema_version=1,
        workspace_id=workspace,
        strategy_id=strategy_id,
        strategy_version_id=uuid4(),
        version=1,
        created_by=actor,
        created_at=now,
        code_object_key=f"strategies/{strategy_id}/versions/1/source.py",
        code_sha256=code_hash,
        parameter_schema_json='{"additionalProperties":false,"properties":{},"type":"object"}',
        dependencies_sha256=deps_hash,
        dependencies_object_key=f"strategies/{strategy_id}/versions/1/dependencies.json",
    )
    store = PostgresStrategyStore(database[0], schema=database[1])
    store.create(strategy, first)
    assert store.get(workspace, strategy_id) == strategy
    assert store.versions(workspace, strategy_id) == (first,)
    next_strategy = replace(strategy, current_version=2, updated_at=now + timedelta(seconds=1))
    second = first.model_copy(
        update={
            "strategy_version_id": uuid4(),
            "version": 2,
            "created_at": next_strategy.updated_at,
            "code_object_key": f"strategies/{strategy_id}/versions/2/source.py",
        }
    )
    store.append_version(workspace, strategy_id, 1, next_strategy, second)
    assert [row.version for row in store.versions(workspace, strategy_id)] == [1, 2]
    with pytest.raises(psycopg.Error, match="immutable"):
        conn.execute(
            "UPDATE strategy_version SET code_sha256=%s WHERE strategy_id=%s",
            ("0" * 64, strategy_id),
        )
    lifecycle = store.state(workspace, strategy_id, 1)
    for target in (
        StrategyVersionState.VALIDATED,
        StrategyVersionState.BACKTESTED,
        StrategyVersionState.PAPER,
    ):
        now += timedelta(seconds=1)
        updated = StrategyVersionLifecycle(
            workspace, strategy_id, first.strategy_version_id, 1, target, now
        )
        event = StrategyStateTransition(
            uuid4(),
            workspace,
            strategy_id,
            first.strategy_version_id,
            1,
            lifecycle.state,
            target,
            actor,
            now,
        )
        lifecycle = store.transition(lifecycle.state, updated, event)
    approval = StrategyVersionApproval(
        uuid4(),
        workspace,
        strategy_id,
        first.strategy_version_id,
        1,
        actor,
        now + timedelta(seconds=1),
        uuid4(),
        20,
        "integration approval",
    )
    approved = StrategyVersionLifecycle(
        workspace,
        strategy_id,
        first.strategy_version_id,
        1,
        StrategyVersionState.APPROVED,
        approval.approved_at,
    )
    approval_event = StrategyStateTransition(
        uuid4(),
        workspace,
        strategy_id,
        first.strategy_version_id,
        1,
        StrategyVersionState.PAPER,
        StrategyVersionState.APPROVED,
        actor,
        approval.approved_at,
        "integration approval",
    )
    store.approve(StrategyVersionState.PAPER, approved, approval_event, approval)
    assert store.approval(workspace, strategy_id, 1) == approval
    assert [row.to_state for row in store.history(workspace, strategy_id, 1)] == [
        StrategyVersionState.VALIDATED,
        StrategyVersionState.BACKTESTED,
        StrategyVersionState.PAPER,
        StrategyVersionState.APPROVED,
    ]
    archived = store.archive_strategy(workspace, strategy_id, actor, now + timedelta(seconds=2))
    assert archived.archived
    assert store.state(workspace, strategy_id, 1).state == StrategyVersionState.ARCHIVED
    assert store.state(workspace, strategy_id, 2).state == StrategyVersionState.ARCHIVED
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="append-only"):
        conn.execute("DELETE FROM strategy_state_transition WHERE strategy_id=%s", (strategy_id,))


def test_backtest_run_inputs_progress_and_completed_result_are_immutable(conn, database):
    from datetime import UTC, datetime
    from pathlib import Path

    from qtrade_contracts import DataBatch, MarketDataset
    from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunRecord
    from qtrade_domain.datasets import PublishedDataset
    from qtrade_postgres.backtesting import PostgresBacktestRunStore
    from qtrade_postgres.batches import PostgresBatchRepository
    from qtrade_postgres.datasets import PostgresDatasetRepository

    actor, workspace = seed(conn)
    now = datetime.now(UTC)
    strategy_id = uuid4()
    version = StrategyVersion(
        schema_version=1,
        workspace_id=workspace,
        strategy_id=strategy_id,
        strategy_version_id=uuid4(),
        version=1,
        created_by=actor,
        created_at=now,
        code_object_key=f"strategies/{strategy_id}/v1.py",
        code_sha256="a" * 64,
        parameter_schema_json='{"type":"object","properties":{}}',
        dependencies_sha256="b" * 64,
        dependencies_object_key=f"strategies/{strategy_id}/v1-dependencies.json",
    )
    strategy = Strategy(strategy_id, workspace, "BT", "", actor, now, now, 1)
    strategies = PostgresStrategyStore(database[0], schema=database[1])
    strategies.create(strategy, version)
    fixtures = Path(__file__).parent / "fixtures/contracts"
    batch = DataBatch.model_validate_json((fixtures / "DataBatch.json").read_text()).model_copy(
        update={"workspace_id": workspace, "batch_id": uuid4()}
    )
    PostgresBatchRepository(database[0], schema=database[1]).put(actor, batch)
    dataset = MarketDataset.model_validate_json(
        (fixtures / "MarketDataset.json").read_text()
    ).model_copy(
        update={"workspace_id": workspace, "dataset_id": uuid4(), "batch_ids": (batch.batch_id,)}
    )
    PostgresDatasetRepository(database[0], schema=database[1]).put(
        actor, PublishedDataset(dataset, "c" * 64, None, "d" * 64)
    )
    manifest_id, run_id = uuid4(), uuid4()
    from qtrade_contracts import BacktestRun

    run = BacktestRun(
        schema_version=1,
        workspace_id=workspace,
        run_id=run_id,
        strategy_version_id=version.strategy_version_id,
        dataset_id=dataset.dataset_id,
        manifest_id=manifest_id,
        created_by=actor,
        created_at=now,
        start=dataset.start,
        end=dataset.end,
        initial_cash="10000",
        currency="USD",
        benchmark_id=None,
        cost_model_version="cost.v1",
        status="queued",
        progress=0,
    )
    store = PostgresBacktestRunStore(database[0], schema=database[1])
    event = BacktestRunEvent(uuid4(), workspace, run_id, "queued", 0, now)
    store.create(BacktestRunRecord(run, None, 1), event)
    assert store.get(workspace, run_id).run == run
    attached = store.update(1, BacktestRunRecord(run, run_id, 2), None)
    running = run.model_copy(update={"status": "running", "progress": 45})
    running_event = BacktestRunEvent(
        uuid4(), workspace, run_id, "running", 45, now, revision=attached.revision + 1
    )
    attached = store.update(
        attached.revision,
        BacktestRunRecord(running, run_id, attached.revision + 1),
        running_event,
    )
    completed = BacktestRun.model_validate(
        {
            **running.model_dump(),
            "status": "completed",
            "progress": 100,
            "result_object_key": f"runs/{run_id}/result.v1.json",
        }
    )
    completed_event = BacktestRunEvent(
        uuid4(), workspace, run_id, "completed", 100, now, revision=attached.revision + 1
    )
    store.update(
        attached.revision,
        BacktestRunRecord(completed, run_id, attached.revision + 1),
        completed_event,
    )
    assert [event.status for event in store.history(workspace, run_id)] == [
        "queued",
        "running",
        "completed",
    ]
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable"):
        conn.execute(
            "UPDATE backtest_run SET payload=jsonb_set(payload,'{initial_cash}','\"20000\"') "
            "WHERE workspace_id=%s AND run_id=%s",
            (workspace, run_id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="append-only"):
        conn.execute("DELETE FROM backtest_run_event WHERE run_id=%s", (run_id,))


def test_collection_overlap_query_is_unpaginated_scoped_and_create_is_serialized(conn, database):
    from qtrade_domain.ingestion import CollectionRequest
    from qtrade_postgres.ingestion import PostgresIngestionRepository

    actor, workspace = seed(conn)
    now = datetime(2026, 9, 28, tzinfo=UTC)
    req = CollectionRequest(
        workspace,
        uuid4(),
        "mairui.a-share.tick",
        "mairui-default",
        "600519.SH",
        "tick",
        now,
        now + timedelta(hours=6),
        now,
        "raw-tick.v1",
    )
    repo = PostgresIngestionRepository(database[0], schema=database[1])

    from qtrade_domain.collection_parts import split_collection

    parts = split_collection(replace(req, end=now + timedelta(days=9)))
    repo.create(actor, replace(parts[-1], instrument_id="OTHER"))
    with pytest.raises(PortError):
        repo.create_many(actor, parts)
    assert (
        conn.execute(
            "SELECT count(*) FROM ingestion_task WHERE task_id=%s", (parts[0].task_id,)
        ).fetchone()[0]
        == 0
    )
    fresh = split_collection(
        replace(req, task_id=uuid4(), instrument_id="601288.SH", end=now + timedelta(days=9))
    )
    group = repo.create_many(actor, fresh)
    assert repo.create_many(actor, fresh) == group
    restarted = PostgresIngestionRepository(database[0], schema=database[1])
    assert restarted.get(workspace, fresh[0].task_id).request == fresh[0]

    def create(_):
        try:
            return repo.create(actor, replace(req, task_id=uuid4()), avoid_overlap=True)
        except PortError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(create, range(2)))
    assert results.count(ErrorCode.CONFLICT) == 1
    assert len(repo.overlapping_tasks(replace(req, instrument_id="CN.600519"))) == 1
    repo.create(actor, replace(req, task_id=uuid4(), instrument_id="600000.SH"))
    assert len(repo.list_tasks(workspace, 1)) == 1
    assert len(repo.overlapping_tasks(req)) == 1
    assert repo.overlapping_tasks(replace(req, authorization_id="other")) == ()
    assert repo.overlapping_tasks(replace(req, workspace_id=uuid4())) == ()
    # An explicit full redownload remains allowed and does not overwrite history.
    repo.create(actor, replace(req, task_id=uuid4()))
    assert len(repo.overlapping_tasks(req)) == 2


def test_recovery_approval_is_atomic_and_bound_to_switch_revision(conn, database):
    user_id, workspace_id = seed(conn)
    approver_id, _ = seed(conn)
    now = datetime.now(UTC)
    store = PostgresKillSwitchStore(database[0], schema=database[1])
    active = store.set_state(
        KillSwitch(workspace_id, None, True, "stop", user_id, now, 1), expected_revision=None
    )
    stale = RecoveryRequest(uuid4(), workspace_id, None, user_id, now, active.revision)
    store.create(stale)
    store.set_state(
        KillSwitch(workspace_id, None, False, "recovered", approver_id, now, 2), expected_revision=1
    )
    store.set_state(
        KillSwitch(workspace_id, None, True, "new safety stop", user_id, now, 3),
        expected_revision=2,
    )
    with pytest.raises(PortError, match="stale switch revision"):
        store.approve_recovery(stale, approver_id=approver_id, reason="old request", now=now)
    assert store.is_active(workspace_id)
    assert store.get_request(stale.request_id) == stale
    request = RecoveryRequest(uuid4(), workspace_id, None, user_id, now, 3)
    store.create(request)
    conn.execute("""CREATE FUNCTION block_recovery_approval() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'approval write failed'; END; $$""")
    conn.execute("""CREATE TRIGGER block_recovery BEFORE UPDATE ON kill_switch_recovery_request
        FOR EACH ROW EXECUTE FUNCTION block_recovery_approval()""")
    with pytest.raises(PortError, match="persistence unavailable"):
        store.approve_recovery(request, approver_id=approver_id, reason="must roll back", now=now)
    assert store.is_active(workspace_id)
    assert store.get_state(workspace_id).revision == 3
    assert store.get_request(request.request_id) == request
    conn.execute("DROP TRIGGER block_recovery ON kill_switch_recovery_request")

    def approve(_):
        try:
            return store.approve_recovery(
                request, approver_id=approver_id, reason="review complete", now=now
            )
        except PortError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(approve, range(2)))
    assert results.count(ErrorCode.CONFLICT) == 1
    assert not store.is_active(workspace_id)
    assert store.get_state(workspace_id).revision == 4
    assert store.get_request(request.request_id) is None


def test_audit_queries_filter_tenant_permissions_and_hide_private_payload(conn, database):
    from qtrade_domain.audit_port import AuditDetails, AuditRecord
    from qtrade_domain.audit_query import AuditQuery
    from qtrade_domain.authorization import WorkspaceAccess
    from qtrade_domain.identity import Workspace
    from qtrade_postgres.audit import PostgresAuditStore
    from qtrade_postgres.audit_query import PostgresAuditQuery

    url, schema = database
    user, workspace = seed(conn)
    other_user, other_workspace = seed(conn)
    now = datetime.now(UTC)
    access = WorkspaceAccess(
        Workspace(workspace, "Audit", "personal", user, False, False, now), user, Role.ADMIN
    )
    trace, target = uuid4(), uuid4()
    store = PostgresAuditStore(url, schema=schema)
    event = store.append(
        AuditRecord(
            workspace,
            "user",
            user,
            "audit.test",
            "success",
            now,
            "test",
            trace,
            "203.0.113.8",
            AuditDetails(target_id=target, session_digest="a" * 64),
        )
    )
    store.append(
        AuditRecord(
            other_workspace, "user", other_user, "audit.test", "success", now, "test", trace
        )
    )
    reader = PostgresAuditQuery(url, schema=schema)
    query = AuditQuery(
        now - timedelta(seconds=1),
        now + timedelta(seconds=1),
        actor_id=user,
        action="audit.test",
        target_id=target,
        result="success",
        trace_id=trace,
    )
    rows = reader.query(access, query, 10)
    assert len(rows) == 1 and rows[0]["event_id"] == event
    assert (
        rows[0]["details"] == {"target_id": str(target)}
        and rows[0]["source_network"] == "203.0.113.0/24"
    )
    assert reader.query(access, replace(query, actor_id=other_user), 10) == ()
    assert reader.query(access, replace(query, before=event), 10) == ()
    conn.execute(
        "UPDATE membership SET role='viewer' WHERE workspace_id=%s AND user_id=%s",
        (workspace, user),
    )
    with pytest.raises(PortError) as denied:
        reader.query(access, query, 10)
    assert denied.value.code == ErrorCode.REJECTED


def test_deployment_schema_verification_is_read_only_and_rejects_incompatible_schema(database):
    url, schema = database
    with pytest.raises(MigrationError):
        verify(url, schema=schema)
    with psycopg.connect(url) as conn:
        assert not conn.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)", (schema,)
        ).fetchone()[0]
    apply(url, schema=schema)
    assert verify(url, schema=schema) == discover()
    with psycopg.connect(url) as conn:
        conn.execute(
            sql.SQL("UPDATE {}.schema_migration SET checksum=%s WHERE version=1").format(
                sql.Identifier(schema)
            ),
            ("f" * 64,),
        )
    with pytest.raises(MigrationError):
        verify(url, schema=schema)
    with psycopg.connect(url) as conn:
        assert (
            conn.execute(
                sql.SQL("SELECT checksum FROM {}.schema_migration WHERE version=1").format(
                    sql.Identifier(schema)
                )
            ).fetchone()[0]
            == "f" * 64
        )


def paper_fixture(conn, database, *, cash=Decimal("1000"), quantity=Decimal("2")):
    from qtrade_postgres.paper_broker import PostgresPaperBroker

    actor, workspace = seed(conn)
    now = datetime.now(UTC)
    strategy_id, version_id, account_id, runtime_id = (uuid4() for _ in range(4))
    strategy = Strategy(strategy_id, workspace, "Paper test", "", actor, now, now, 1)
    version = StrategyVersion(
        schema_version=1,
        workspace_id=workspace,
        strategy_id=strategy_id,
        strategy_version_id=version_id,
        version=1,
        created_by=actor,
        created_at=now,
        code_object_key="strategies/source.py",
        code_sha256="a" * 64,
        parameter_schema_json='{"type":"object"}',
        dependencies_sha256="b" * 64,
        dependencies_object_key="strategies/dependencies.json",
    )
    PostgresStrategyStore(database[0], schema=database[1]).create(strategy, version)
    conn.execute(
        "INSERT INTO execution_runtime VALUES(%s,%s,%s,%s,'paper','running',1,%s)",
        (workspace, runtime_id, account_id, version_id, now),
    )
    command = OrderCommand.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/OrderCommand.json").read_text()
    )
    intent = command.intent.model_copy(
        update={
            "workspace_id": workspace,
            "account_id": account_id,
            "runtime_id": runtime_id,
            "strategy_version_id": version_id,
            "created_by": actor,
            "quantity": quantity,
            "order_type": "market",
            "limit_price": None,
            "intent_id": uuid4(),
            "trace_id": uuid4(),
        }
    )
    decision = command.decision.model_copy(
        update={
            "workspace_id": workspace,
            "decision_id": uuid4(),
            "intent_id": intent.intent_id,
            "trace_id": intent.trace_id,
        }
    )
    command = command.model_copy(
        update={
            "workspace_id": workspace,
            "intent": intent,
            "decision": decision,
            "command_id": uuid4(),
            "idempotency_key": intent.intent_id,
        }
    )
    prepare_paper_command(database, command)
    broker = PostgresPaperBroker(database[0], lambda command: Decimal("10"), schema=database[1])
    broker.create_account(workspace, account_id, cash)
    return broker, command


def prepare_paper_command(database, command):
    intent = command.intent
    PostgresRiskDecisionStore(database[0], schema=database[1]).record(
        RiskDecisionRecord(command.decision, "{}")
    )
    order = Order(
        schema_version=1,
        workspace_id=command.workspace_id,
        order_id=uuid4(),
        account_id=intent.account_id,
        runtime_id=intent.runtime_id,
        intent_id=intent.intent_id,
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        instrument_id=intent.instrument_id,
        side=intent.side,
        status="created",
        quantity=intent.quantity,
        created_at=intent.created_at,
        updated_at=intent.created_at,
        revision=1,
    )
    event = OrderEvent(
        schema_version=1,
        workspace_id=command.workspace_id,
        event_id=uuid4(),
        order_id=order.order_id,
        revision=1,
        from_status=None,
        to_status="created",
        occurred_at=intent.created_at,
        source="system",
        filled_quantity=Decimal(0),
    )
    events = [event]
    for status, source in (("risk_approved", "risk"), ("submitted", "system")):
        order, event = transition_order(
            order, status, occurred_at=command.authorized_at, source=source, event_id=uuid4()
        )
        events.append(event)
    PostgresOrderExecutionStore(database[0], schema=database[1]).prepare(
        OrderExecutionRecord(command, order, tuple(events))
    )


def test_paper_ledger_concurrent_retry_restart_and_immutable_receipt(conn, database):
    from qtrade_postgres.paper_broker import PostgresPaperBroker

    broker, command = paper_fixture(conn, database)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: broker.submit(command), range(4)))
    assert all(r == receipts[0] for r in receipts)
    assert receipts[0].status == "filled"
    restored = PostgresPaperBroker(database[0], lambda c: Decimal("900"), schema=database[1])
    assert restored.submit(command) == receipts[0]  # Original price and receipt survive restart.
    snapshot = restored.snapshot(command.workspace_id, command.intent.account_id)
    assert snapshot.cash == Decimal("979.983997")
    assert snapshot.positions[0].quantity == Decimal(2)
    assert snapshot.positions[0].average_price == Decimal("10.005")
    assert len(snapshot.orders) == 1
    assert (
        conn.execute(
            "SELECT count(*) FROM audit_event WHERE action='paper.order.filled'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE paper_broker_receipt SET market_price=20")
    with pytest.raises(PortError) as error:
        restored.snapshot(uuid4(), command.intent.account_id)
    assert error.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(PortError) as error:
        restored.create_account(command.workspace_id, command.intent.account_id, Decimal("2000"))
    assert error.value.code == ErrorCode.CONFLICT


def test_paper_ledger_audit_failure_rolls_back_cash_position_and_receipt(
    conn, database, monkeypatch
):
    import qtrade_postgres.paper_broker as module

    broker, command = paper_fixture(conn, database)

    def fail(*args):
        raise PortError(ErrorCode.UNAVAILABLE, "audit unavailable")

    monkeypatch.setattr(module, "append_in_transaction", fail)
    with pytest.raises(PortError):
        broker.submit(command)
    snapshot = broker.snapshot(command.workspace_id, command.intent.account_id)
    assert snapshot.cash == Decimal("1000")
    assert snapshot.positions == snapshot.orders == ()
    monkeypatch.undo()
    assert broker.submit(command).status == "filled"


@pytest.mark.parametrize("state", ["paused", "stopped", "live", "switch", "missing_risk"])
def test_paper_ledger_rejects_unavailable_runtime_and_authorization(conn, database, state):
    broker, command = paper_fixture(conn, database)
    if state == "live":
        conn.execute("UPDATE execution_runtime SET mode='live'")
    elif state == "switch":
        PostgresKillSwitchStore(database[0], schema=database[1]).set_state(
            KillSwitch(
                command.workspace_id,
                None,
                True,
                "Test",
                command.intent.created_by,
                datetime.now(UTC),
                1,
            ),
            expected_revision=None,
        )
    elif state == "missing_risk":
        # A valid in-memory allow decision is insufficient without its durable record.
        command = command.model_copy(
            update={"decision": command.decision.model_copy(update={"decision_id": uuid4()})}
        )
    else:
        conn.execute("UPDATE execution_runtime SET state=%s", (state,))
    with pytest.raises(PortError) as error:
        broker.submit(command)
    assert error.value.code == ErrorCode.REJECTED
    snapshot = broker.snapshot(command.workspace_id, command.intent.account_id)
    assert snapshot.cash == Decimal("1000")
    assert snapshot.positions == snapshot.orders == ()


def test_paper_ledger_rejection_and_sell_settlement_are_persistent(conn, database):
    broker, command = paper_fixture(conn, database, cash=Decimal("10"))
    rejected = broker.submit(command)
    assert rejected.status == "rejected"
    assert rejected.rejection_code == "insufficient_paper_cash"
    assert broker.snapshot(command.workspace_id, command.intent.account_id).cash == Decimal(10)
    assert broker.submit(command) == rejected
    assert (
        conn.execute(
            "SELECT count(*) FROM audit_event WHERE action='paper.order.rejected'"
        ).fetchone()[0]
        == 1
    )

    broker, command = paper_fixture(conn, database)
    assert broker.submit(command).status == "filled"
    intent = command.intent.model_copy(
        update={"intent_id": uuid4(), "side": "sell", "trace_id": uuid4()}
    )
    decision = command.decision.model_copy(
        update={"decision_id": uuid4(), "intent_id": intent.intent_id, "trace_id": intent.trace_id}
    )
    sell = command.model_copy(
        update={
            "command_id": uuid4(),
            "idempotency_key": intent.intent_id,
            "intent": intent,
            "decision": decision,
        }
    )
    prepare_paper_command(database, sell)
    assert broker.submit(sell).status == "filled"
    snapshot = broker.snapshot(sell.workspace_id, sell.intent.account_id)
    assert snapshot.positions == ()
    assert snapshot.cash == Decimal("999.968")
    assert len(snapshot.orders) == 2


def test_paper_ledger_concurrent_distinct_orders_cannot_overspend(conn, database):
    broker, command = paper_fixture(conn, database, cash=Decimal("30"))
    intent = command.intent.model_copy(update={"intent_id": uuid4(), "trace_id": uuid4()})
    decision = command.decision.model_copy(
        update={
            "decision_id": uuid4(),
            "intent_id": intent.intent_id,
            "trace_id": intent.trace_id,
        }
    )
    second = command.model_copy(
        update={
            "command_id": uuid4(),
            "idempotency_key": intent.intent_id,
            "intent": intent,
            "decision": decision,
        }
    )
    prepare_paper_command(database, second)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(broker.submit, [command, second]))
    assert sorted(r.status for r in receipts) == ["filled", "rejected"]
    snapshot = broker.snapshot(command.workspace_id, command.intent.account_id)
    assert snapshot.cash == Decimal("9.983997")
    assert snapshot.positions[0].quantity == Decimal(2)
    assert len(snapshot.orders) == 2


def test_paper_ledger_reused_idempotency_and_price_failure_are_safe(conn, database):
    from qtrade_postgres.paper_broker import PostgresPaperBroker

    broker, command = paper_fixture(conn, database)
    unavailable = PostgresPaperBroker(database[0], lambda c: Decimal("NaN"), schema=database[1])
    with pytest.raises(PortError):
        unavailable.submit(command)
    assert broker.snapshot(command.workspace_id, command.intent.account_id).orders == ()
    assert broker.submit(command).status == "filled"
    conflicting = command.model_copy(update={"command_id": uuid4()})
    with pytest.raises(PortError) as error:
        broker.submit(conflicting)
    assert error.value.code == ErrorCode.CONFLICT
    assert len(broker.snapshot(command.workspace_id, command.intent.account_id).orders) == 1


def test_runtime_transition_audit_failure_rolls_back_and_conflict_has_no_event(
    conn, database, monkeypatch
):
    import qtrade_postgres.runtime_control as module
    from qtrade_domain.audit_port import AuditDetails, AuditRecord
    from qtrade_postgres.runtime_control import PostgresExecutionRuntimeStore

    broker, command = paper_fixture(conn, database)
    store = PostgresExecutionRuntimeStore(database[0], schema=database[1])
    runtime = store.get(command.workspace_id, command.intent.runtime_id)
    now = datetime.now(UTC)
    audit = AuditRecord(
        command.workspace_id,
        "user",
        command.intent.created_by,
        "runtime.pause",
        "success",
        now,
        "api.runtime.control",
        command.intent.trace_id,
        details=AuditDetails(
            target_id=runtime.runtime_id, target_workspace_id=runtime.workspace_id
        ),
    )

    def fail(*args):
        raise PortError(ErrorCode.UNAVAILABLE, "audit unavailable")

    monkeypatch.setattr(module, "append_in_transaction", fail)
    with pytest.raises(PortError):
        store.transition(runtime, expected_revision=1, state="paused", updated_at=now, audit=audit)
    assert store.get(runtime.workspace_id, runtime.runtime_id) == runtime
    monkeypatch.undo()
    updated = store.transition(
        runtime, expected_revision=1, state="paused", updated_at=now, audit=audit
    )
    assert updated.state == "paused" and updated.revision == 2
    assert (
        conn.execute("SELECT count(*) FROM audit_event WHERE action='runtime.pause'").fetchone()[0]
        == 1
    )
    with pytest.raises(PortError) as error:
        store.transition(runtime, expected_revision=1, state="paused", updated_at=now, audit=audit)
    assert error.value.code == ErrorCode.CONFLICT
    assert (
        conn.execute("SELECT count(*) FROM audit_event WHERE action='runtime.pause'").fetchone()[0]
        == 1
    )
    with pytest.raises(PortError):
        broker.submit(command)


def test_runtime_transition_rejects_mismatched_audit_before_update(conn, database):
    from qtrade_domain.audit_port import AuditDetails, AuditRecord
    from qtrade_postgres.runtime_control import PostgresExecutionRuntimeStore

    _, command = paper_fixture(conn, database)
    store = PostgresExecutionRuntimeStore(database[0], schema=database[1])
    runtime = store.get(command.workspace_id, command.intent.runtime_id)
    now = datetime.now(UTC)
    audit = AuditRecord(
        command.workspace_id,
        "user",
        command.intent.created_by,
        "runtime.pause",
        "success",
        now,
        "api.runtime.control",
        command.intent.trace_id,
        details=AuditDetails(target_id=uuid4(), target_workspace_id=runtime.workspace_id),
    )
    with pytest.raises(PortError) as error:
        store.transition(runtime, expected_revision=1, state="paused", updated_at=now, audit=audit)
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert store.get(runtime.workspace_id, runtime.runtime_id) == runtime
    assert (
        conn.execute("SELECT count(*) FROM audit_event WHERE action='runtime.pause'").fetchone()[0]
        == 0
    )

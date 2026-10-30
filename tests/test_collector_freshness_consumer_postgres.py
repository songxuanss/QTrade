"""Real QTrade alert/queue persistence with controlled collector events and transport."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from test_collection_freshness import key
from test_freshness_notice import result
from test_research_evidence_postgres import database as isolated_database

from qtrade_application.alerts import AlertEventService
from qtrade_composition.collector_freshness import CollectorFreshnessConsumer
from qtrade_data_contracts.freshness_delivery import FreshnessNoticeLease
from qtrade_data_core.freshness_notice import transition_notice
from qtrade_data_core.ports import DataFailure
from qtrade_domain.ports import PortError
from qtrade_postgres.alert_events import PostgresAlertEventStore
from qtrade_postgres.migrate import apply
from qtrade_postgres.notification_delivery import PostgresNotificationDeliveryQueue
from qtrade_postgres.workspaces import PostgresWorkspaceStore

database = isolated_database


@pytest.fixture
def consumer(database):
    schema = "consumer_test_" + uuid4().hex
    apply(database, schema=schema)
    actor, k = uuid4(), key()
    with psycopg.connect(database) as conn:
        conn.execute(sql.SQL("SET search_path TO {},pg_catalog").format(sql.Identifier(schema)))
        conn.execute(
            "INSERT INTO qtrade_user(user_id,issuer,subject,display_name) VALUES(%s,%s,%s,%s)",
            (actor, "https://fixture.test", str(actor), "Fixture manager"),
        )
        conn.execute(
            "INSERT INTO workspace(workspace_id,name,kind,owner_id) "
            "VALUES(%s,'Fixture','personal',%s)",
            (k.scope_id, actor),
        )
        conn.execute(
            "INSERT INTO membership(workspace_id,user_id,role) VALUES(%s,%s,'admin')",
            (k.scope_id, actor),
        )
    notice = transition_notice(actor, result(k), None)
    lease = FreshnessNoticeLease(
        schema_version=1,
        scope_id=k.scope_id,
        consumer_id="qtrade-alerts",
        notice=notice,
        token=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    value = CollectorFreshnessConsumer.__new__(CollectorFreshnessConsumer)
    value.consumer = "qtrade-alerts"
    value.workspaces = PostgresWorkspaceStore(database, schema=schema)
    value.events = AlertEventService(PostgresAlertEventStore(database, schema=schema))
    value.notifications = PostgresNotificationDeliveryQueue(database, schema=schema)
    calls = []

    def ack(*args, **kwargs):
        calls.append(kwargs)
        return {"accepted": True}

    value.client = SimpleNamespace(request=ack)
    try:
        yield value, lease, calls, schema
    finally:
        with psycopg.connect(database) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def count(database, schema, table):
    with psycopg.connect(database) as conn:
        return conn.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
        ).fetchone()[0]


def test_crash_before_remote_ack_replays_local_commits_without_duplicate_event_or_notification(
    database, consumer
):
    value, lease, calls, schema = consumer
    original = value.client.request

    def disconnected(*args, **kwargs):
        raise DataFailure("unavailable")

    value.client.request = disconnected
    with pytest.raises(DataFailure):
        value.consume_one(lease)
    assert count(database, schema, "alert_event") == 1
    assert count(database, schema, "notification_delivery") == 1
    value.client.request = original
    assert value.consume_one(lease)
    assert count(database, schema, "alert_event") == 1
    assert count(database, schema, "notification_delivery") == 1
    assert len(calls) == 1 and calls[0]["actor_id"] == lease.notice.actor_id
    assert calls[0]["scope_id"] == lease.scope_id


def test_current_read_only_role_or_expired_lease_has_no_local_side_effect(database, consumer):
    value, lease, calls, schema = consumer
    with psycopg.connect(database) as conn:
        conn.execute(
            sql.SQL("UPDATE {}.membership SET role='viewer'").format(sql.Identifier(schema))
        )
    with pytest.raises(PortError):
        value.consume_one(lease)
    expired = lease.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    assert not value.consume_one(expired)
    assert not calls and count(database, schema, "alert_event") == 0
    assert count(database, schema, "notification_delivery") == 0


def test_failure_between_event_and_queue_does_not_ack_and_retries_idempotently(database, consumer):
    from qtrade_domain.ports import ErrorCode

    value, lease, calls, schema = consumer
    enqueue = value.notifications.enqueue

    def fail_enqueue(*args):
        raise PortError(ErrorCode.UNAVAILABLE, "controlled queue failure")

    value.notifications.enqueue = fail_enqueue
    with pytest.raises(PortError):
        value.consume_one(lease)
    assert count(database, schema, "alert_event") == 1
    assert count(database, schema, "notification_delivery") == 0 and not calls
    value.notifications.enqueue = enqueue
    assert value.consume_one(lease)
    assert count(database, schema, "alert_event") == 1
    assert count(database, schema, "notification_delivery") == 1 and len(calls) == 1


def test_forged_scope_is_rejected_before_persistence(database, consumer):
    value, lease, calls, schema = consumer
    forged = lease.model_copy(update={"scope_id": uuid4()})
    with pytest.raises(DataFailure, match="unverified"):
        value.consume_one(forged)
    assert count(database, schema, "alert_event") == 0
    assert count(database, schema, "notification_delivery") == 0 and not calls

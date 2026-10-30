"""Isolated real PG snapshots; controlled evidence does not prove live freshness."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from test_collection_freshness import NOW, evidence, expected, key, point
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_contracts.collection import digest
from qtrade_data_core.freshness import evaluate
from qtrade_data_core.freshness_store import FreshnessSnapshots
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.freshness import PostgresFreshness

database = isolated_database


def decision(k=None, **changes):
    k = k or key()
    return evaluate(k, expected(), evidence(k, **changes), NOW)


def test_restart_history_retains_outage_and_recovery(database):
    repo = PostgresFreshness(database)
    initial = decision(source=point())
    outage = evaluate(
        initial.key,
        expected(),
        evidence(initial.key, source_health="unavailable"),
        NOW + timedelta(seconds=1),
    )
    recovery = evaluate(
        initial.key,
        expected(),
        evidence(initial.key, published=point()),
        NOW + timedelta(seconds=2),
    )
    for value in (initial, outage, recovery):
        assert repo.append(value) == value
    restarted = PostgresFreshness(database)
    assert restarted.latest(initial.key) == recovery
    assert restarted.history(initial.key) == (recovery, outage, initial)
    assert restarted.history(initial.key, limit=1) == (recovery,)
    assert restarted.history(initial.key, before=recovery.checked_at) == (outage, initial)


def test_late_finishing_old_probe_does_not_replace_new_snapshot(database):
    repo = PostgresFreshness(database)
    old = decision(source=point())
    newer = evaluate(
        old.key, expected(), evidence(old.key, published=point()), NOW + timedelta(seconds=10)
    )
    repo.append(newer)
    repo.append(old)
    assert repo.latest(old.key) == newer


def test_same_instant_offset_idempotency_and_concurrent_writers(database):
    original = decision(source=point())
    offset = timezone(timedelta(hours=8))
    alternate = original.model_copy(
        update={
            "checked_at": NOW.astimezone(offset),
            "expectation": original.expectation.model_copy(
                update={"available_at": original.expectation.available_at.astimezone(offset)}
            ),
            "evidence": original.evidence.model_copy(
                update={
                    "source": original.evidence.source.model_copy(
                        update={"observed_at": NOW.astimezone(offset)}
                    )
                }
            ),
        }
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda i: PostgresFreshness(database).append(original if i % 2 else alternate),
                range(8),
            )
        )
    assert all(value == original for value in results)
    assert PostgresFreshness(database).history(original.key) == (original,)


def test_same_identity_clock_conflicting_decision_refused(database):
    repo = PostgresFreshness(database)
    original = decision(source=point())
    repo.append(original)
    different = evaluate(
        original.key, expected(), evidence(original.key, source_health="unavailable"), NOW
    )
    with pytest.raises(DataFailure, match="conflict"):
        repo.append(different)
    assert repo.latest(original.key) == original


@pytest.mark.parametrize(
    "change",
    [
        {"scope_id": uuid4()},
        {"source": "other"},
        {"instrument_id": "600519.SH"},
        {"adjustment": "forward"},
        {"mapping_version": "other.v2"},
    ],
)
def test_different_identity_not_read_as_same_dataset(database, change):
    value = decision(source=point())
    repo = PostgresFreshness(database)
    repo.append(value)
    other = value.key.model_copy(update=change)
    assert repo.history(other) == ()
    with pytest.raises(DataFailure, match="not_found"):
        repo.latest(other)


@pytest.mark.parametrize(
    "command",
    [
        "UPDATE qtrade_data.collection_freshness_snapshot SET stored_at=now() WHERE scope_id=%s",
        "DELETE FROM qtrade_data.collection_freshness_snapshot WHERE scope_id=%s",
        "TRUNCATE qtrade_data.collection_freshness_snapshot",
    ],
)
def test_sql_cannot_rewrite_observations(database, command):
    value = decision(source=point())
    PostgresFreshness(database).append(value)
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute(command, (value.scope_id,) if "%s" in command else None)
        conn.rollback()
    assert PostgresFreshness(database).latest(value.key) == value


def test_bad_checksum_or_snapshot_identity_fails_closed(database):
    value = decision(source=point())
    with psycopg.connect(database) as conn:
        conn.execute(
            """INSERT INTO qtrade_data.collection_freshness_snapshot
            (scope_id,snapshot_id,key_sha256,checked_at,result_sha256,payload)
            VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                value.scope_id,
                uuid4(),
                digest(value.key.model_dump(mode="json")),
                value.checked_at,
                "b" * 64,
                value.model_dump_json(),
            ),
        )
    with pytest.raises(DataFailure, match="unverified"):
        PostgresFreshness(database).latest(value.key)


def test_authorization_rechecked_and_reader_cannot_record(database):
    actor = uuid4()
    value = decision(source=point())
    repo = PostgresFreshness(database)
    repo.append(value)
    permission = {f"{actor}:{value.scope_id}": "read"}
    auth = StaticScopeAuthorizer(permission)
    service = FreshnessSnapshots(auth, repo)
    assert service.latest(actor, value.key) == value
    assert service.history(actor, value.key) == (value,)
    with pytest.raises(DataFailure, match="forbidden"):
        service.record(actor, value)
    with pytest.raises(DataFailure, match="forbidden"):
        service.latest(actor, value.key.model_copy(update={"scope_id": uuid4()}))
    permission.clear()
    with pytest.raises(DataFailure, match="forbidden"):
        service.latest(actor, value.key)
    with pytest.raises(DataFailure, match="forbidden"):
        service.history(actor, value.key)


@pytest.mark.parametrize("limit", [True, 0, 101, "1"])
def test_history_bounds_strict(database, limit):
    with pytest.raises(DataFailure, match="invalid_input"):
        PostgresFreshness(database).history(key(), limit=limit)
    with pytest.raises(DataFailure, match="invalid_input"):
        PostgresFreshness(database).history(key(), before=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("case", ["scope_missing", "key_scope", "evidence_key", "schema_boolean"])
def test_sql_rejects_missing_or_mismatched_identity(database, case):
    value = decision(source=point())
    payload = value.model_dump(mode="json")
    if case == "scope_missing":
        payload.pop("scope_id")
    elif case == "key_scope":
        payload["key"]["scope_id"] = str(uuid4())
    elif case == "evidence_key":
        payload["evidence"]["key"]["source"] = "other"
    else:
        payload["schema_version"] = True
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute(
                """INSERT INTO qtrade_data.collection_freshness_snapshot
                (scope_id,snapshot_id,key_sha256,checked_at,result_sha256,payload)
                VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    value.scope_id,
                    uuid4(),
                    "a" * 64,
                    value.checked_at,
                    "b" * 64,
                    json.dumps(payload),
                ),
            )
        conn.rollback()

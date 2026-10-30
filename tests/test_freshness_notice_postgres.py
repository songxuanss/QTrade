"""Real isolated PG atomic outbox; fixture facts do not prove live outage readiness."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from test_collection_freshness import key
from test_freshness_notice import result
from test_freshness_refresh_postgres import setup
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_core.freshness_refresh import FreshnessRefresh
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.freshness import PostgresFreshness

database = isolated_database


def test_repeated_failures_unknown_restart_recovery_and_new_episode(database):
    actor, k, auth, repo, service = setup(database)
    service.commit(actor, result(k))
    first = repo.notices(k)[0]
    for seconds, status in [(1, "unavailable"), (2, "unknown"), (3, "within_grace"), (4, "late")]:
        FreshnessRefresh(auth, PostgresFreshness(database)).commit(
            actor, result(k, seconds, status=status)
        )
    assert repo.notices(k) == (first,)
    recovered = service.commit(actor, result(k, 5, status="current"))
    notices = PostgresFreshness(database).notices(k)
    assert [n.kind for n in notices] == ["recovered", "opened"]
    assert notices[0].episode_id == first.episode_id
    assert notices[0].result == recovered.result
    # Repeated current checks do not notify. A later outage starts a new episode
    # even when stronger historical published progress remains in the merge.
    service.commit(actor, result(k, 6, status="current"))
    service.commit(actor, result(k, 7))
    notices = repo.notices(k)
    assert [n.kind for n in notices] == ["opened", "recovered", "opened"]
    assert notices[0].episode_id != first.episode_id
    assert repo.notices(key()) == ()


def test_same_check_replay_and_concurrent_failure_writers_do_not_duplicate(database):
    actor, k, auth, repo, _ = setup(database)
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(
            pool.map(
                lambda _: FreshnessRefresh(auth, PostgresFreshness(database)).commit(
                    actor, result(k)
                ),
                range(12),
            )
        )
    assert len(repo.history(k)) == len(repo.notices(k)) == 1
    assert sum(v.state == "recorded" for v in outcomes) == 1
    assert sum(v.state == "replayed" for v in outcomes) == 11


def test_notification_failure_rolls_back_snapshot_and_outbox(database, monkeypatch):
    actor, k, _, repo, service = setup(database)
    notify = PostgresFreshness.notify

    def fail_after_insert(self, actor, value):
        notify(self, actor, value)
        raise DataFailure("unavailable")

    monkeypatch.setattr(PostgresFreshness, "notify", fail_after_insert)
    with pytest.raises(DataFailure, match="unavailable"):
        service.commit(actor, result(k))
    assert repo.history(k) == () and repo.notices(k) == ()


def test_archival_append_and_non_transactional_notify_never_emit(database):
    actor, k, _, repo, _ = setup(database)
    value = result(k)
    repo.append(value)
    assert repo.notices(k) == ()
    with pytest.raises(DataFailure, match="conflict"):
        repo.notify(actor, value)
    with repo.atomic(k) as transaction:
        with pytest.raises(DataFailure, match="conflict"):
            transaction.notify(actor, result(key()))


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_outbox_facts_cannot_be_rewritten_or_removed(database, operation):
    actor, k, _, repo, service = setup(database)
    service.commit(actor, result(k))
    if operation == "UPDATE":
        statement = "UPDATE qtrade_data.collection_freshness_notice SET stored_at=now()"
    elif operation == "DELETE":
        statement = "DELETE FROM qtrade_data.collection_freshness_notice"
    else:
        statement = "TRUNCATE qtrade_data.collection_freshness_notice"
    with pytest.raises(psycopg.Error):
        with psycopg.connect(database) as conn:
            conn.execute(statement)
    assert len(repo.notices(k)) == 1


def test_read_bounds_scope_and_corrupt_payload_fail_closed(database):
    actor, k, _, repo, service = setup(database)
    service.commit(actor, result(k))
    assert repo.notices(k.model_copy(update={"scope_id": uuid4()})) == ()
    for limit in (True, 0, 101):
        with pytest.raises(DataFailure, match="invalid_input"):
            repo.notices(k, limit=limit)
    # Immutable rows cannot normally change. Exercise the same decoding seam
    # with a row whose DB identity/hash no longer matches its valid payload.
    with psycopg.connect(database) as conn:
        row = conn.execute(
            """SELECT notice_id,key_sha256,episode_id,kind,occurred_at,
            snapshot_id,notice_sha256,payload FROM qtrade_data.collection_freshness_notice
            WHERE scope_id=%s""",
            (k.scope_id,),
        ).fetchone()
    bad = list(row)
    bad[6] = "0" * 64
    with pytest.raises(DataFailure, match="unverified"):
        repo._read_notice(tuple(bad), k)

"""Real isolated transactions; controlled checkpoints are not live supplier evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from test_collection_freshness import DAY, NOW, evidence, expected, key, point
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_core.freshness import evaluate
from qtrade_data_core.freshness_refresh import FreshnessRefresh
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.freshness import PostgresFreshness

database = isolated_database


def setup(database):
    actor, k = uuid4(), key()
    auth = StaticScopeAuthorizer({f"{actor}:{k.scope_id}": "manage"})
    repo = PostgresFreshness(database)
    return actor, k, auth, repo, FreshnessRefresh(auth, repo)


def candidate(k, seconds=0, *, health="unknown", current=False):
    checkpoint = point(DAY if current else DAY - timedelta(days=1))
    return evaluate(
        k,
        expected(),
        evidence(k, source_health=health, collected=checkpoint, published=checkpoint),
        NOW + timedelta(seconds=seconds),
    )


def test_old_batch_cannot_regress_verified_local_or_published_period(database):
    actor, k, _, repo, service = setup(database)
    original = service.commit(actor, candidate(k, current=True)).result
    result = service.commit(actor, candidate(k, seconds=1))
    assert result.state == "recorded" and result.result.status == "current"
    assert result.result.evidence.published == original.evidence.published
    assert result.result.evidence.collected == original.evidence.collected
    assert (
        result.result.evidence.source is None and result.result.evidence.source_health == "unknown"
    )
    assert PostgresFreshness(database).latest(k) == result.result
    assert len(repo.history(k)) == 2


def test_outage_overrides_retained_current_publication(database):
    actor, k, _, _, service = setup(database)
    original = service.commit(actor, candidate(k, current=True, health="available")).result
    outage = service.commit(actor, candidate(k, seconds=1, health="unavailable"))
    assert outage.result.status == "unavailable" and outage.result.reason == "source_unavailable"
    assert outage.result.evidence.published == original.evidence.published
    assert outage.result.evidence.source_health == "unavailable"


def test_unknown_rule_never_inherits_previous_release_policy(database):
    actor, k, _, _, service = setup(database)
    service.commit(actor, candidate(k, current=True))
    unknown = evaluate(k, None, evidence(k, source_health="unknown"), NOW + timedelta(seconds=1))
    result = service.commit(actor, unknown).result
    assert result.status == "unknown" and result.reason == "rule_unknown"
    assert result.expectation is None and result.evidence.published is not None


def test_replay_after_merge_is_idempotent_and_equal_clock_conflicts(database):
    actor, k, _, repo, service = setup(database)
    service.commit(actor, candidate(k, current=True))
    older_data = candidate(k, seconds=1)
    merged = service.commit(actor, older_data)
    again = service.commit(actor, older_data)
    assert again.state == "replayed" and again.result == merged.result
    with pytest.raises(DataFailure, match="conflict"):
        service.commit(actor, candidate(k, seconds=1, health="unavailable"))
    assert len(repo.history(k)) == 2


def test_old_check_returns_retry_signal_without_relabeling_or_insertion(database):
    actor, k, _, repo, service = setup(database)
    latest = service.commit(actor, candidate(k, seconds=10, current=True)).result
    old = service.commit(actor, candidate(k, seconds=1, health="unavailable"))
    assert old.state == "superseded" and old.result == latest
    assert repo.history(k) == (latest,)


def test_stale_calendar_target_cannot_certify_old_data(database):
    actor, k, _, repo, service = setup(database)
    current = service.commit(actor, candidate(k)).result
    older_policy = expected(complete_through=DAY - timedelta(days=1))
    older = evaluate(
        k,
        older_policy,
        evidence(k, published=point(DAY - timedelta(days=1))),
        NOW + timedelta(seconds=1),
    )
    assert older.status == "current"  # It would look healthy against the stale target.
    with pytest.raises(DataFailure, match="unverified"):
        service.commit(actor, older)
    assert repo.latest(k) == current


def test_multiple_writers_preserve_committed_progress_and_newest_outage(database):
    actor, k, auth, repo, service = setup(database)
    seed = service.commit(actor, candidate(k, current=True)).result
    values = [
        candidate(k, seconds=i, health="unavailable" if i == 12 else "unknown")
        for i in range(1, 13)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(
                lambda value: FreshnessRefresh(auth, PostgresFreshness(database)).commit(
                    actor, value
                ),
                reversed(values),
            )
        )
    final = repo.latest(k)
    assert final.checked_at == NOW + timedelta(seconds=12)
    assert final.status == "unavailable" and final.evidence.published == seed.evidence.published
    assert all(v.result.evidence.published == seed.evidence.published for v in outcomes)
    assert all(v.state in {"recorded", "superseded"} for v in outcomes)


def test_atomic_rollback_and_other_key_access_rejection(database):
    _, k, _, repo, _ = setup(database)
    original = candidate(k, current=True)
    with pytest.raises(RuntimeError):
        with repo.atomic(k) as transaction:
            transaction.append(original)
            raise RuntimeError("controlled failure before commit")
    assert repo.history(k) == ()
    with repo.atomic(k) as transaction:
        with pytest.raises(DataFailure, match="conflict"):
            transaction.latest(key())
        with pytest.raises(DataFailure, match="conflict"):
            transaction.append(candidate(key()))


def test_reader_cannot_refresh_and_revocation_during_lock_wait_rejects(database):
    actor, k, auth, repo, service = setup(database)
    auth.grants[f"{actor}:{k.scope_id}"] = "read"
    with pytest.raises(DataFailure, match="forbidden"):
        service.commit(actor, candidate(k))
    auth.grants[f"{actor}:{k.scope_id}"] = "manage"
    admitted = Event()

    class Signaling:
        def require(self, *args):
            auth.require(*args)
            admitted.set()

    # Hold the exact production advisory key while the worker passes its first
    # authorization check. The second check runs after it obtains the lock.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with repo.atomic(k):
            future = pool.submit(
                FreshnessRefresh(Signaling(), PostgresFreshness(database)).commit,
                actor,
                candidate(k),
            )
            assert admitted.wait(5)
            auth.grants.clear()
        with pytest.raises(DataFailure, match="forbidden"):
            future.result(timeout=5)
    assert repo.history(k) == ()


def test_lock_wait_is_bounded_and_does_not_insert_partial_snapshot(database):
    actor, k, auth, repo, _ = setup(database)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with repo.atomic(k):
            refresh = FreshnessRefresh(auth, PostgresFreshness(database, lock_wait_ms=100))
            future = pool.submit(refresh.commit, actor, candidate(k))
            with pytest.raises(DataFailure, match="unavailable"):
                future.result(timeout=3)
    assert repo.history(k) == ()
    # Timeout releases its connection and does not poison subsequent commits.
    assert FreshnessRefresh(auth, repo).commit(actor, candidate(k)).state == "recorded"


def test_unknown_new_source_does_not_borrow_previous_verified_observation(database):
    actor, k, _, _, service = setup(database)
    seed = evaluate(
        k,
        expected(),
        evidence(
            k, source_health="available", source=point(), collected=point(), published=point()
        ),
        NOW,
    )
    service.commit(actor, seed)
    merged = service.commit(actor, candidate(k, seconds=1)).result
    assert merged.evidence.source is None
    assert merged.evidence.source_health == "unknown"
    assert merged.evidence.published == seed.evidence.published

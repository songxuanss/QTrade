"""Bounded policy/evidence preparation; controlled provider is not a live price probe."""

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from test_daily_freshness_evidence import evidence as publication_fixture

from qtrade_data_core.default_range import natural_year_range
from qtrade_data_core.freshness_check import FreshnessCheck
from qtrade_data_core.freshness_refresh import RefreshCommit
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage_execution import UsageIdentity, usage_identity


def setup(tmp_path, *, monkeypatch=None, now=None, current=False):
    if current:
        local = now.astimezone(ZoneInfo("Asia/Shanghai"))
        day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        if local.time() < time(17, 10):
            day -= timedelta(days=1)
        task, pub, _, verifier, auth = publication_fixture(
            tmp_path, start=day, monkeypatch=monkeypatch, captured_at=now, published_at=now
        )
    else:
        task, pub, _, verifier, auth = publication_fixture(tmp_path)
    auth.grants[f"{task.request.actor_id}:{task.request.scope_id}"] = "manage"
    calls = []
    saved = []

    class Provider:
        def default_range(self, source, granularity, at):
            calls.append(usage_identity.get())
            day = at.astimezone(ZoneInfo("Asia/Shanghai")).date()
            return natural_year_range(
                at,
                (day - timedelta(days=1), day),
                timezone="Asia/Shanghai",
                release_time=time(17, 10),
                basis="fixture release calendar",
            )

    provider = Provider()

    def commit(actor, candidate):
        saved.append(candidate)
        return RefreshCommit("recorded", candidate)

    refresh = SimpleNamespace(commit=commit)
    check = FreshnessCheck(
        auth,
        verifier.repository,
        verifier,
        provider,
        refresh,
        clock=(lambda: now) if now else lambda: datetime.now(UTC),
    )
    return task, pub, check, calls, saved, provider, refresh


def test_recent_download_of_old_history_does_not_prove_latest_source(tmp_path):
    task, pub, check, calls, saved, *_ = setup(tmp_path)
    outcome = check.check(task.request.actor_id, pub.scope_id, pub.publication_id)
    assert outcome.result.status == "unknown" and outcome.result.evidence.source is None
    assert len(calls) == 2 and len(saved) == 1
    assert all(value == UsageIdentity(task.request.actor_id, pub.scope_id) for value in calls)
    assert usage_identity.get() is None


def test_recent_eligible_daily_acquisition_can_prove_expected_source(tmp_path, monkeypatch):
    now = datetime(2026, 10, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    task, pub, check, *_ = setup(tmp_path, monkeypatch=monkeypatch, now=now, current=True)
    result = check.check(task.request.actor_id, pub.scope_id, pub.publication_id).result
    assert result.status == "current" and result.evidence.source_health == "available"
    assert result.evidence.source.complete_through.isoformat() == "2026-10-14"


def test_release_boundary_prepares_again_instead_of_relabeling(tmp_path, monkeypatch):
    first = datetime(2026, 10, 15, 17, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    task, pub, check, calls, saved, *_ = setup(
        tmp_path, monkeypatch=monkeypatch, now=first, current=True
    )
    # The same archived day is current before release and behind afterwards.
    # Re-preparation changes the target, not the immutable publication.
    moments = iter(
        [first, first.replace(minute=11), first.replace(minute=11), first.replace(minute=11)]
    )
    check.clock = lambda: next(moments)
    result = check.check(task.request.actor_id, pub.scope_id, pub.publication_id).result
    assert len(calls) == 4 and len(saved) == 1
    assert result.expectation.complete_through.isoformat() == "2026-10-15"
    assert result.status == "unknown" and result.evidence.source is None


def test_read_role_has_no_policy_or_write_side_effect(tmp_path):
    task, pub, check, calls, saved, *_ = setup(tmp_path)
    check.auth.grants[f"{task.request.actor_id}:{pub.scope_id}"] = "read"
    with pytest.raises(DataFailure, match="forbidden"):
        check.check(task.request.actor_id, pub.scope_id, pub.publication_id)
    assert not calls and not saved


def test_source_failure_does_not_record_a_success(tmp_path):
    task, pub, check, _, saved, provider, _ = setup(tmp_path)

    def unavailable(*args):
        raise DataFailure("unavailable")

    provider.default_range = unavailable
    with pytest.raises(DataFailure, match="unavailable"):
        check.check(task.request.actor_id, pub.scope_id, pub.publication_id)
    assert not saved


def test_superseded_retries_are_bounded_and_reprepare(tmp_path):
    task, pub, check, calls, saved, _, refresh = setup(tmp_path)

    def superseded(actor, candidate):
        saved.append(candidate)
        return RefreshCommit("superseded", candidate)

    refresh.commit = superseded
    with pytest.raises(DataFailure, match="conflict"):
        check.check(task.request.actor_id, pub.scope_id, pub.publication_id)
    assert len(calls) == 4 and len(saved) == 2
    assert saved[1].checked_at >= saved[0].checked_at

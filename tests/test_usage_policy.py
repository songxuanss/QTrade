"""Credit policy rejects coercion and missing tenant context before admission."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from qtrade_data_core.usage import UsageDenied, UsagePolicy, UsageRequest, UsageResult


@pytest.mark.parametrize("value", [True, 0, -1, "10", 1.5])
def test_policy_and_credit_inputs_are_strict(value):
    with pytest.raises(ValueError):
        UsagePolicy(value, 10, 1, 1)
    with pytest.raises(ValueError):
        UsageRequest(uuid4(), "fixture", uuid4(), "bars.history", value)


def test_unscoped_collection_and_secret_like_labels_rejected():
    with pytest.raises(ValueError):
        UsageRequest(uuid4(), "fixture", None, "bars.history", 1)
    with pytest.raises(ValueError):
        UsageRequest(uuid4(), "https://credential@source", uuid4(), "bars.history", 1)
    with pytest.raises(ValueError):
        UsagePolicy(10, 10, 1, 1, timezone="not-a-zone")


def test_outcome_metrics_and_safe_error_consistent():
    with pytest.raises(ValueError):
        UsageResult("success", 1, 2, "timeout")
    with pytest.raises(ValueError):
        UsageResult("failure", 1, 2)
    with pytest.raises(ValueError):
        UsageResult("success", True, 2)


def test_daily_wait_uses_policy_midnight_and_slots_short_retry():
    now = datetime(2026, 10, 13, 15, 59, tzinfo=UTC)
    policy = UsagePolicy(10, 10, 2, 1)
    for reason in ("source_daily", "scope_daily"):
        scheduled = UsageDenied(reason).scheduled(policy, now)
        assert scheduled.retry_at == now + timedelta(minutes=1)
    assert UsageDenied("scope_concurrency").scheduled(policy, now).retry_at == now + timedelta(
        seconds=30
    )
    with pytest.raises(ValueError):
        UsageDenied("scope_daily").scheduled(policy, now.replace(tzinfo=None))
    assert UsageDenied("scope_daily").scheduled(UsagePolicy(100, 1, 2, 1), now, 5).retry_at is None

"""Controlled incident semantics; these are not live supplier outage evidence."""

from datetime import timedelta, timezone
from uuid import uuid4

import pytest
from test_collection_freshness import NOW, evidence, expected, key, point

from qtrade_data_contracts.freshness_notice import FreshnessNotice
from qtrade_data_core.freshness import evaluate
from qtrade_data_core.freshness_notice import transition_notice
from qtrade_data_core.ports import DataFailure


def result(k, seconds=0, *, status="unavailable"):
    state = evidence(k, source_health="unavailable" if status == "unavailable" else "unknown")
    policy = expected()
    if status == "current":
        state = evidence(k, published=point(), source=point(), source_health="available")
    elif status == "late":
        state = evidence(k, source=point(), source_health="available")
    elif status == "within_grace":
        policy = expected(grace_seconds=3600)
        state = evidence(k, source=point(), source_health="available")
    return evaluate(k, policy, state, NOW + timedelta(seconds=seconds))


def test_open_repeat_unknown_grace_recover_and_new_episode():
    actor, k = uuid4(), key()
    opened = transition_notice(actor, result(k), None)
    assert opened is not None and opened.kind == "opened"
    for i, state in enumerate(("unavailable", "unknown", "within_grace", "late"), 1):
        assert transition_notice(actor, result(k, i, status=state), opened) is None
    recovered = transition_notice(uuid4(), result(k, 10, status="current"), opened)
    assert recovered.kind == "recovered" and recovered.episode_id == opened.episode_id
    assert recovered.opened_at == opened.occurred_at
    assert recovered.notice_id != opened.notice_id
    assert transition_notice(actor, result(k, 11, status="current"), recovered) is None
    next_open = transition_notice(actor, result(k, 12, status="late"), recovered)
    assert next_open.episode_id != opened.episode_id


@pytest.mark.parametrize("status", ["unknown", "within_grace", "current"])
def test_first_observation_without_failure_does_not_invent_incident(status):
    assert transition_notice(uuid4(), result(key(), status=status), None) is None


def test_identity_is_stable_across_timezone_representations_and_restarts():
    actor, k = uuid4(), key()
    original = result(k)
    first = transition_notice(actor, original, None)
    offset = original.model_copy(
        update={"checked_at": original.checked_at.astimezone(timezone(timedelta(hours=8)))}
    )
    assert transition_notice(actor, offset, None) == first
    assert FreshnessNotice.model_validate_json(first.model_dump_json()) == first


@pytest.mark.parametrize("change", ["notice_id", "episode_id", "scope_id", "kind", "opened_at"])
def test_invalid_notice_identity_scope_or_recovery_rejected(change):
    notice = transition_notice(uuid4(), result(key()), None)
    body = notice.model_dump(mode="json")
    if change == "kind":
        body[change] = "recovered"
    elif change == "opened_at":
        body[change] = (NOW - timedelta(seconds=1)).isoformat()
    else:
        body[change] = str(uuid4())
    with pytest.raises(ValueError):
        FreshnessNotice.model_validate_json(__import__("json").dumps(body))


def test_wrong_key_and_out_of_order_observations_rejected():
    actor, k = uuid4(), key()
    opened = transition_notice(actor, result(k), None)
    for value in (result(key(), 1), result(k), result(k, -1)):
        with pytest.raises(DataFailure, match="conflict"):
            transition_notice(actor, value, opened)


def test_current_publication_with_unknown_source_does_not_claim_outage_recovery():
    actor, k = uuid4(), key()
    opened = transition_notice(actor, result(k), None)
    unknown = evaluate(
        k,
        expected(),
        evidence(k, published=point(), source_health="unknown"),
        NOW + timedelta(seconds=1),
    )
    assert unknown.status == "current" and unknown.evidence.source_health == "unknown"
    assert transition_notice(actor, unknown, opened) is None

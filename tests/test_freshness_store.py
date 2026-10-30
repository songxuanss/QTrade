"""Authorization precedes storage; malformed port responses are never published."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_collection_freshness import NOW, evidence, expected, key, point

from qtrade_data_core.freshness import evaluate
from qtrade_data_core.freshness_store import FreshnessSnapshots
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import StaticScopeAuthorizer


def setup(role="read"):
    actor, k = uuid4(), key()
    result = evaluate(k, expected(), evidence(k, source=point()), NOW)
    auth = StaticScopeAuthorizer({f"{actor}:{k.scope_id}": role})
    calls = []
    repo = SimpleNamespace(
        append=lambda value: calls.append("append") or value,
        latest=lambda value: calls.append("latest") or result,
        history=lambda value, **kwargs: calls.append("history") or (result,),
    )
    return actor, k, result, repo, calls, FreshnessSnapshots(auth, repo)


def test_read_only_denial_has_no_storage_side_effect():
    actor, _, result, _, calls, service = setup()
    with pytest.raises(DataFailure, match="forbidden"):
        service.record(actor, result)
    assert not calls


def test_forged_decision_rejected_before_append():
    actor, _, result, _, calls, service = setup("manage")
    with pytest.raises(DataFailure, match="invalid_input"):
        service.record(actor, result.model_copy(update={"status": "current"}))
    assert not calls


@pytest.mark.parametrize("method", ["latest", "history"])
def test_foreign_read_scope_never_reaches_storage(method):
    actor, k, _, _, calls, service = setup()
    with pytest.raises(DataFailure, match="forbidden"):
        getattr(service, method)(actor, k.model_copy(update={"scope_id": uuid4()}))
    assert not calls


@pytest.mark.parametrize("method", ["latest", "history"])
def test_wrong_dataset_from_port_not_returned(method):
    actor, k, result, repo, _, service = setup()
    other = result.model_copy(update={"key": k.model_copy(update={"source": "other"})})
    repo.latest = lambda value: other
    repo.history = lambda value, **kwargs: (other,)
    with pytest.raises(DataFailure, match="unavailable"):
        getattr(service, method)(actor, k)


@pytest.mark.parametrize("case", ["out_of_order", "duplicate", "too_many", "past_cursor"])
def test_history_cursor_and_order_verified_at_port_boundary(case):
    actor, k, result, repo, _, service = setup()
    newer = evaluate(k, expected(), evidence(k, source=point()), NOW + timedelta(seconds=1))
    rows = {
        "out_of_order": (result, newer),
        "duplicate": (result, result),
        "too_many": (newer, result),
        "past_cursor": (result,),
    }[case]
    repo.history = lambda value, **kwargs: rows
    kwargs = (
        {"limit": 1} if case == "too_many" else {"before": NOW} if case == "past_cursor" else {}
    )
    with pytest.raises(DataFailure, match="unavailable"):
        service.history(actor, k, **kwargs)


def test_changed_write_acknowledgement_not_accepted():
    actor, _, result, repo, _, service = setup("manage")
    repo.append = lambda value: value.model_copy(update={"checked_at": NOW + timedelta(seconds=1)})
    with pytest.raises(DataFailure, match="unavailable"):
        service.record(actor, result)


@pytest.mark.parametrize("limit", [0, True, 101, "1"])
def test_invalid_limits_do_not_query_port(limit):
    actor, k, _, _, calls, service = setup()
    with pytest.raises(DataFailure, match="invalid_input"):
        service.history(actor, k, limit=limit)
    assert not calls

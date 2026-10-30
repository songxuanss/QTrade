"""Research plans reject excess scale without fetching or truncating selections."""

from datetime import date

import pytest
from pydantic import ValidationError
from test_research_interface import ACTOR, SCOPE, ControlledSource, request

from qtrade_data_contracts.planning import CollectionPlanGuard
from qtrade_data_contracts.research_planning import ResearchPlanPreview, ResearchSubmission
from qtrade_data_contracts.research_transport import ResearchPartition
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchService
from qtrade_data_http.client import StaticScopeAuthorizer


def service(provider, **limits):
    guard = CollectionPlanGuard(
        version="collection-plan-guard.v1",
        max_instruments=limits.get("max_instruments", 2),
        max_new_parts=limits.get("max_new_parts", 2),
        confirm_new_parts=100,
        confirm_credits=500,
    )
    return ResearchService(
        StaticScopeAuthorizer({f"{ACTOR}:{SCOPE}": "manage"}), (provider,), plan_guard=guard
    )


def test_instrument_cap_checked_before_provider_discovery(monkeypatch):
    provider = ControlledSource()
    monkeypatch.setattr(provider, "capabilities", lambda: pytest.fail("provider must not run"))
    with pytest.raises(DataFailure, match="invalid_input"):
        service(provider, max_instruments=1).preview(
            request(instrument_ids=("600519.SH", "601288.SH"))
        )


def test_partition_iterator_consumption_is_bounded_even_if_provider_never_finishes(monkeypatch):
    provider = ControlledSource()
    seen = []

    def unbounded(req):
        while True:
            seen.append(1)
            yield ResearchPartition(
                instrument_id=req.instrument_ids[0], start=req.start, end=req.end
            )

    monkeypatch.setattr(provider, "partitions", unbounded)
    with pytest.raises(DataFailure, match="invalid_input"):
        service(provider).preview(request())
    assert len(seen) == 3 and provider.fetch_calls == 0


def test_exact_cap_preserves_plan_hash_and_dates():
    provider = ControlledSource()
    req = request(instrument_ids=("600519.SH", "601288.SH"))
    bounded = service(provider).preview(req)
    ordinary = ResearchService(
        StaticScopeAuthorizer({f"{ACTOR}:{SCOPE}": "manage"}), (provider,)
    ).preview(req)
    assert bounded == ordinary
    assert len(bounded.partitions) == 2
    assert all(
        p.start == date(2015, 1, 1) and p.end == date(2016, 1, 1) for p in bounded.partitions
    )
    assert provider.fetch_calls == 0


def test_unauthorized_input_does_not_disclose_guard_or_invoke_provider(monkeypatch):
    provider = ControlledSource()
    monkeypatch.setattr(provider, "capabilities", lambda: pytest.fail("provider must not run"))
    denied = ResearchService(StaticScopeAuthorizer({}), (provider,))
    with pytest.raises(DataFailure, match="forbidden"):
        denied.preview(request())


def test_control_digest_binds_policy_estimate_and_preserves_old_plan():
    provider = ControlledSource()
    research = service(provider)
    plan = research.preview(request())
    preview = research.controlled_preview(request())
    assert preview.frozen_plan() == plan and not preview.confirmation_required
    assert preview.execution_estimate.calls_max == 2 and provider.fetch_calls == 0
    research.plan_guard = research.plan_guard.model_copy(update={"confirm_credits": 1})
    current = research.controlled_preview(request())
    assert current.confirmation_required and current.control_sha256 != preview.control_sha256
    assert current.plan_sha256 == preview.plan_sha256
    body = current.model_dump(mode="json") | {"confirmation_required": False}
    import json

    with pytest.raises(ValidationError, match="checksum"):
        ResearchPlanPreview.model_validate_json(json.dumps(body))


@pytest.mark.parametrize("value", [1, 0, "true", "false", None])
def test_research_submission_requires_strict_boolean_and_strips_controls(value):
    import json

    body = request().model_dump(mode="json")
    valid = ResearchSubmission.model_validate_json(json.dumps(body | {"confirmed": True}))
    assert valid.selection() == request()
    with pytest.raises(ValidationError):
        ResearchSubmission.model_validate_json(json.dumps(body | {"confirmed": value}))

"""Plan acknowledgement is explicit; estimates are bounded internal safeguards."""

import pytest
from pydantic import ValidationError
from test_independent_collection import request

from qtrade_data_contracts.planning import CollectionCostEstimate, CollectionPlanGuard
from qtrade_data_mairui.collection import MaiRuiCollection


@pytest.mark.parametrize("value", [1, 0, "true", "false", None, []])
def test_http_confirmation_rejects_coercion_in_both_services(value, monkeypatch, tmp_path):
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "fixture-service-token-" + "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    from qtrade_api.collector import SubmitBody as BridgeBody
    from qtrade_collector.main import SubmitBody as CollectorBody

    body = {"request": request().model_dump(mode="json"), "coverage_version": "a" * 64}
    # The submitted frozen request retains identities; the routes check them
    # against the current authenticated actor/scope. A truthy string must never
    # become acknowledgement independently of those permission checks.
    for model in (BridgeBody, CollectorBody):
        assert model.model_validate(body).confirmed is False
        assert model.model_validate(body | {"confirmed": True}).confirmed is True
        with pytest.raises(ValidationError):
            model.model_validate(body | {"confirmed": value})


def test_estimate_model_rejects_inverted_or_misleading_intervals():
    body = dict(
        calls_min=0,
        calls_max=4,
        credits_min=0,
        credits_max=12,
        accounting="internal_credit_safeguard",
        basis="Cache may remove calls; retries excluded.",
        includes_retries=False,
    )
    for change in (
        {"calls_min": 5},
        {"credits_min": 13},
        {"calls_max": -1},
        {"credits_max": True},
        {"includes_retries": True},
        {"accounting": "supplier_invoice"},
    ):
        with pytest.raises(ValidationError):
            CollectionCostEstimate(**(body | change))


def test_guard_rejects_disabled_and_noninteger_hard_limits():
    body = dict(
        version="collection-plan-guard.v1",
        max_instruments=10000,
        max_new_parts=20000,
        confirm_new_parts=100,
        confirm_credits=500,
    )
    for change in ({"max_new_parts": 0}, {"max_instruments": True}, {"confirm_credits": "500"}):
        with pytest.raises(ValidationError):
            CollectionPlanGuard(**(body | change))


def test_estimate_is_pure_zero_for_reuse_and_factor_weighted(tmp_path):
    provider = MaiRuiCollection(None, cache=tmp_path)
    provider._get = lambda *args, **kwargs: pytest.fail("estimation must not call supplier")
    bars = provider.estimate(request(), 0)
    assert (bars.calls_max, bars.credits_max) == (0, 0)
    bars = provider.estimate(request(), 3)
    factors = provider.estimate(
        request(dataset_kind="factors", granularity="1d", fields=("pe_ttm",)), 3
    )
    assert bars.calls_max == factors.calls_max == 12
    assert bars.credits_max == 12 and factors.credits_max == 36
    assert not bars.includes_retries and bars.calls_min == factors.credits_min == 0

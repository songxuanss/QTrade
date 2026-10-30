"""Scope-specific safeguards cannot replace shared source control or tenant identity."""

import json
from uuid import uuid4

import pytest
from test_usage_execution import Ledger

from qtrade_collector.usage_config import scope_limits
from qtrade_data_core.usage import UsagePolicy
from qtrade_data_core.usage_execution import ScopeLimits, UsageGate, usage_scope


def test_effective_policy_isolated_immutable_and_used_before_io():
    first, other = uuid4(), uuid4()
    supplied = {first: ScopeLimits(5, 1)}
    ledger = Ledger(limit=100)
    base = UsagePolicy(100, 20, 4, 2)
    gate = UsageGate(ledger, "fixture", base, supplied)
    supplied[first] = ScopeLimits(100, 3)
    assert gate.policy_for(first).scope_daily == 5
    assert gate.policy_for(first).scope_concurrency == 1
    assert gate.policy_for(first).source_daily == base.source_daily
    assert gate.policy_for(first).source_concurrency == base.source_concurrency
    assert gate.policy_for(other) == gate.policy_for(None) == base
    seen = []
    reserve = ledger.reserve

    def spy(req, policy, now):
        seen.append((req.scope_id, policy.scope_daily))
        return reserve(req, policy, now)

    ledger.reserve = spy
    for scope, expected in ((first, 5), (other, 20)):
        with usage_scope(uuid4(), scope):
            gate.begin("bars.history", 1).finish(2, None)
        assert seen[-1] == (scope, expected)


def test_operator_json_canonical_id_and_complete_values():
    scope = uuid4()
    assert scope_limits("") == scope_limits("{}") == {}
    assert scope_limits(json.dumps({str(scope): {"daily_credits": 8, "concurrency": 1}})) == {
        scope: ScopeLimits(8, 1)
    }


@pytest.mark.parametrize(
    "values",
    [
        {"daily_credits": True, "concurrency": 1},
        {"daily_credits": 0, "concurrency": 1},
        {"daily_credits": "5", "concurrency": 1},
        {"daily_credits": 5, "concurrency": 1001},
        {"daily_credits": 5},
        {"daily_credits": 5, "concurrency": 1, "source_daily": 5000},
    ],
)
def test_bad_operator_values_fail_closed(values):
    with pytest.raises(ValueError, match="invalid scope limit configuration"):
        scope_limits(json.dumps({str(uuid4()): values}))


@pytest.mark.parametrize("raw", ["[]", "null", '{"private-config":{}}', "x" * 65537])
def test_malformed_input_is_not_echoed(raw):
    with pytest.raises(ValueError) as error:
        scope_limits(raw)
    assert str(error.value) == "invalid scope limit configuration"


def test_duplicate_keys_and_noncanonical_uuid_rejected():
    key = str(uuid4())
    limit = '{"daily_credits":5,"concurrency":1}'
    for raw in (
        '{"' + key + '":' + limit + ',"' + key + '":' + limit + "}",
        json.dumps({key.upper(): {"daily_credits": 5, "concurrency": 1}}),
    ):
        with pytest.raises(ValueError, match="invalid scope limit configuration"):
            scope_limits(raw)

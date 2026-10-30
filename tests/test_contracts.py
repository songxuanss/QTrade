import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from qtrade_contracts import (
    CONTRACTS,
    ArtifactManifest,
    Bar,
    OrderCommand,
    OrderIntent,
    StrategyVersion,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return json.loads((ROOT / f"tests/fixtures/contracts/{name}.json").read_text())


def test_strategy_version_v1_without_dependency_manifest_remains_compatible():
    value = fixture("StrategyVersion")
    value.pop("dependencies_object_key")
    assert StrategyVersion.model_validate_json(json.dumps(value)).dependencies_object_key is None


@pytest.mark.parametrize("model", CONTRACTS, ids=lambda model: model.__name__)
def test_language_independent_schema_and_roundtrip(model):
    schema = json.loads(
        (ROOT / f"packages/contracts/schemas/v1/{model.__name__}.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    value = fixture(model.__name__)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(value)
    instance = model.model_validate_json(json.dumps(value))
    validator.validate(instance.model_dump(mode="json"))
    assert model.model_validate_json(instance.model_dump_json()) == instance
    generated = model.model_json_schema(mode="serialization")
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    generated["$id"] = f"urn:qtrade:contracts:v1:{model.__name__}"
    assert schema == generated, "contract drift requires an explicit schema update/version decision"
    with pytest.raises(ValidationError):
        instance.workspace_id = instance.workspace_id


@pytest.mark.parametrize("model", CONTRACTS, ids=lambda model: model.__name__)
@pytest.mark.parametrize(
    "change", ["missing_version", "future_version", "boolean_version", "extra"]
)
def test_versions_and_fields_are_not_silently_coerced(model, change):
    value = fixture(model.__name__)
    if change == "missing_version":
        del value["schema_version"]
    elif change == "future_version":
        value["schema_version"] = 2
    elif change == "boolean_version":
        value["schema_version"] = True
    else:
        value["provider_private_object"] = {}
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("value", [1.1, "NaN", "Infinity", "1e3", "01", "0", "-1", True])
def test_order_quantity_rejects_invalid_or_imprecise_values(value):
    data = fixture("OrderIntent")
    data["quantity"] = value
    with pytest.raises(ValidationError):
        OrderIntent.model_validate_json(json.dumps(data))


def test_bar_rejects_naive_time_and_impossible_prices():
    data = fixture("Bar")
    data["timestamp"] = "2026-10-01T00:00:00"
    with pytest.raises(ValidationError):
        Bar.model_validate_json(json.dumps(data))


def test_backtest_manifest_binds_run_configuration_to_its_hash():
    value = fixture("ArtifactManifest")
    value["execution_config_json"] = value["execution_config_json"].replace('"v1"', '"v2"')
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate_json(json.dumps(value))
    data = fixture("Bar")
    data["high"] = "9"
    with pytest.raises(ValidationError):
        Bar.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("change", ["denied", "other_intent", "other_workspace", "other_trace"])
def test_broker_command_requires_matching_allowed_risk_decision(change):
    data = copy.deepcopy(fixture("OrderCommand"))
    other = "00000000-0000-4000-8000-000000000002"
    if change == "denied":
        data["decision"]["outcome"] = "deny"
        data["decision"]["reasons"] = [
            {"rule_id": "limit", "rule_version": "1", "code": "limit", "summary": "exposure"}
        ]
    elif change == "other_intent":
        data["decision"]["intent_id"] = other
    elif change == "other_workspace":
        data["intent"]["workspace_id"] = other
    else:
        data["decision"]["trace_id"] = other
    with pytest.raises(ValidationError):
        OrderCommand.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("params", ['{"x":NaN}', '{"x":1,"x":2}', "[]"])
def test_manifest_rejects_ambiguous_parameters(params):
    data = fixture("ArtifactManifest")
    data["parameters_json"] = params
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate_json(json.dumps(data))


def test_manifest_checks_canonical_parameter_hash():
    data = fixture("ArtifactManifest")
    data["parameters_json"] = '{ "lookback" : 20 }'
    assert (
        ArtifactManifest.model_validate_json(json.dumps(data)).parameters_json == '{"lookback":20}'
    )
    data["parameters_sha256"] = "b" * 64
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate_json(json.dumps(data))

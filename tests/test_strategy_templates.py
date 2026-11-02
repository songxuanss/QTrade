import json
from pathlib import Path

from qtrade_application.strategy_validation import validate_strategy_source
from qtrade_domain.strategies import canonical_parameter_schema

TEMPLATES = Path(__file__).resolve().parents[1] / "apps/web/src/features/strategies/templates.json"
REQUIRED_IDS = {
    "sma-cross",
    "rsi-reversal",
    "momentum-rank",
    "low-volatility",
    "equal-weight",
}


def load_templates() -> list[dict[str, object]]:
    return json.loads(TEMPLATES.read_text(encoding="utf-8"))


def test_templates_cover_expected_strategy_kinds():
    items = load_templates()
    assert len(items) >= 5
    ids = [str(item["id"]) for item in items]
    assert len(ids) == len(set(ids))
    assert REQUIRED_IDS <= set(ids)


def test_templates_have_required_metadata():
    for item in load_templates():
        for key in (
            "id",
            "name",
            "summary",
            "default_name",
            "default_description",
            "code",
        ):
            value = item.get(key)
            assert isinstance(value, str) and value.strip(), (item["id"], key)
        tags = item.get("tags")
        assert isinstance(tags, list) and tags, item["id"]
        assert isinstance(item.get("schema"), dict), item["id"]


def test_template_sources_pass_static_validation():
    for item in load_templates():
        result = validate_strategy_source(str(item["code"]), {})
        assert result.passed, (
            item["id"],
            [diagnostic.message for diagnostic in result.diagnostics],
        )


def test_template_schemas_are_canonical():
    for item in load_templates():
        canonical_parameter_schema(json.dumps(item["schema"]))


def test_template_parameters_are_referenced_by_source():
    for item in load_templates():
        schema = item["schema"]
        assert isinstance(schema, dict)
        properties = schema.get("properties", {})
        for name in properties:
            assert f'"{name}"' in str(item["code"]), (item["id"], name)

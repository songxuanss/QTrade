import importlib.util
import json
from pathlib import Path

from qtrade_application.strategy_validation import validate_strategy_source
from qtrade_domain.strategies import canonical_parameter_schema

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "apps/web/src/features/strategies/templates.json"
GENERATOR = ROOT / "scripts/generate-strategy-templates.py"
REQUIRED_IDS = {
    "sma-cross",
    "ts-momentum",
    "donchian-breakout",
    "macd-trend",
    "atr-chandelier",
    "high-window-momentum",
    "rsi-reversal",
    "bollinger-reversion",
    "rsi-trend-filter",
    "momentum-rank",
    "dual-momentum",
    "vol-target-momentum",
    "low-volatility",
    "cross-sectional-reversal",
    "dragon-pullback",
    "volume-price-surge",
    "ma-squeeze-breakout",
    "gap-hold",
    "grid-reversion",
    "kdj-cross",
    "equal-weight",
}


def load_templates() -> list[dict[str, object]]:
    return json.loads(TEMPLATES.read_text(encoding="utf-8"))


def load_generator():
    spec = importlib.util.spec_from_file_location("strategy_template_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_templates_cover_expected_strategy_kinds():
    items = load_templates()
    ids = [str(item["id"]) for item in items]
    assert len(ids) == len(set(ids))
    assert len(items) >= 21
    assert REQUIRED_IDS <= set(ids)
    categories = {str(item["category"]) for item in items}
    assert {"趋势", "反转", "横截面", "扫地僧", "基准"} <= categories


def test_templates_have_required_metadata():
    for item in load_templates():
        for key in (
            "id",
            "name",
            "category",
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


def test_templates_file_matches_generator():
    module = load_generator()
    assert TEMPLATES.read_text(encoding="utf-8") == module.render()

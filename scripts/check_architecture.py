"""Dependency policy for QTrade; run directly or use violations() in tests."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDORS = {
    "lean",
    "AlgorithmImports",
    "QuantConnect",
    "nautilus_trader",
    "qlib",
    "ccxt",
    "clickhouse_connect",
    "clickhouse_driver",
    "timescale",
    "timescaledb",
    "pyarrow",
    "ibapi",
    "ib_insync",
    "alpaca",
    "binance",
    "tushare",
    "xtquant",
}
LAYERS = {
    "qtrade_contracts": {"pydantic"},
    "qtrade_data_contracts": {"pydantic"},
    "qtrade_data_core": {"qtrade_data_contracts"},
    "qtrade_domain": {"qtrade_contracts"},
    "qtrade_application": {"qtrade_domain", "qtrade_contracts"},
}
APPS = {"qtrade_api", "qtrade_worker", "qtrade_realtime", "qtrade_collector"}
COMPOSITION_ROOTS = {"qtrade_composition"}


def violations(source: str, module: str) -> list[str]:
    """Check imports, including literal dynamic imports; reject dynamic unknowns."""
    errors: list[str] = []
    owner = module.split(".")[0]
    imports: list[tuple[int, str]] = []
    tree = ast.parse(source)
    dynamic_names = {"import_module", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic_names.update(
                item.asname or item.name for item in node.names if item.name == "import_module"
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, item.name) for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = module.split(".")[: -node.level]
                target = ".".join(parent + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            imports.append((node.lineno, target))
        elif isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
            )
            if name in dynamic_names:
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    imports.append((node.lineno, node.args[0].value))
                else:
                    errors.append(f"{module}:{node.lineno}: nonliteral dynamic import")
    for line, target in imports:
        prefix = target.split(".")[0]
        if owner in LAYERS:
            allowed = LAYERS[owner] | {owner} | sys.stdlib_module_names
            if prefix not in allowed:
                errors.append(f"{module}:{line}: {owner} cannot import {target}")
        if owner in APPS and prefix in APPS and prefix != owner:
            errors.append(f"{module}:{line}: process entry cannot import {target}")
        if (owner in APPS or owner in LAYERS) and prefix in VENDORS:
            errors.append(f"{module}:{line}: vendor SDK {target} belongs in an adapter")
        if (
            owner.startswith("qtrade_")
            and owner not in APPS | LAYERS.keys() | COMPOSITION_ROOTS
            and owner != "qtrade_runtime"
            and prefix in APPS | {"qtrade_application"}
        ):
            errors.append(f"{module}:{line}: adapter cannot import {target}")
        if (
            owner.startswith("qtrade_data_")
            and owner not in LAYERS
            and prefix.startswith("qtrade_")
            and prefix not in {owner, "qtrade_data_core", "qtrade_data_contracts"}
        ):
            errors.append(f"{module}:{line}: portable data adapter cannot import {target}")
        if (
            owner == "qtrade_collector"
            and prefix.startswith("qtrade_")
            and not (prefix.startswith("qtrade_data_") or prefix == owner)
        ):
            errors.append(f"{module}:{line}: collector cannot import business module {target}")
        if owner == "qtrade_runtime" and prefix.startswith("qtrade_") and prefix != owner:
            errors.append(f"{module}:{line}: runtime cannot depend on business module {target}")
        if owner == "qtrade_domain":
            current = module.split(".")
            dest = target.split(".")
            if len(current) >= 3 and len(dest) >= 3 and dest[0] == owner and current[1] != dest[1]:
                errors.append(f"{module}:{line}: use the {dest[1]} public facade")
    return errors


def check(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for area in ("apps", "packages", "adapters"):
        for path in (root / area).glob("*/src/**/*.py"):
            parts = path.relative_to(root).parts
            index = parts.index("src")
            module = ".".join((*parts[index + 1 : -1], path.stem))
            errors.extend(violations(path.read_text(), module))
    return errors


if __name__ == "__main__":
    failures = check()
    print("\n".join(failures) if failures else "Architecture checks passed")
    raise SystemExit(bool(failures))

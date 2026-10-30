import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "check_architecture", Path(__file__).resolve().parents[1] / "scripts/check_architecture.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_repository_dependencies():
    assert checker.check() == []


@pytest.mark.parametrize(
    "module,source",
    [
        ("qtrade_domain.risk.rules", "import ccxt"),
        ("qtrade_data_contracts.models", "from qtrade_domain.authorization import WorkspaceAccess"),
        ("qtrade_data_core.collection", "from qtrade_contracts import OrderIntent"),
        ("qtrade_data_mairui.reference", "from qtrade_domain.authorization import WorkspaceAccess"),
        ("qtrade_data_postgres.universes", "from qtrade_postgres.connection import PostgresStore"),
        ("qtrade_data_core.collection", "from qtrade_application.ingestion import Worker"),
        ("qtrade_domain.risk.rules", "import psycopg"),
        ("qtrade_domain.risk.rules", "import fastapi"),
        ("qtrade_application.backtesting", "from qtrade_mock import Engine"),
        ("qtrade_api.main", "from qtrade_worker.main import app"),
        ("qtrade_worker.main", "from qtrade_api.health import app"),
        ("qtrade_api.routes", "from clickhouse_connect import get_client"),
        ("qtrade_api.routes", "from ibapi.client import EClient"),
        ("qtrade_api.routes", "import pyarrow"),
        ("qtrade_domain.risk.rules", "from qtrade_domain.execution.internal import Order"),
        ("qtrade_runtime.health", "from qtrade_domain.risk import RiskEngine"),
        ("qtrade_mock.engine", "from qtrade_application import Backtesting"),
        ("qtrade_api.routes", "import importlib; importlib.import_module('qlib')"),
        ("qtrade_domain.risk.rules", "__import__('ccxt')"),
        ("qtrade_domain.risk.rules", "__import__(adapter_name)"),
        ("qtrade_domain.risk.rules", "from importlib import import_module as load; load('ccxt')"),
    ],
)
def test_forbidden_imports_fail(module, source):
    assert checker.violations(source, module)


@pytest.mark.parametrize(
    "module,source",
    [
        ("qtrade_domain.risk.rules", "from decimal import Decimal"),
        ("qtrade_parquet.raw_records", "import pyarrow"),
        ("qtrade_domain.risk.rules", "from qtrade_contracts import OrderIntent"),
        ("qtrade_domain.risk.rules", "from qtrade_domain.execution import Order"),
        ("qtrade_worker.main", "from qtrade_runtime.health import create_health_app"),
    ],
)
def test_public_interfaces_and_standard_library_are_allowed(module, source):
    assert checker.violations(source, module) == []

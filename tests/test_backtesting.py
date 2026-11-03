"""Use-case tests for frozen Backtest Run inputs and engine-state synchronization."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_application.artifacts import ManifestService
from qtrade_application.backtesting import (
    BacktestEngineIdentity,
    BacktestService,
    BacktestSubmission,
)
from qtrade_application.strategies import StrategyService
from qtrade_contracts import BacktestResult, MarketDataset, StrategyVersion
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.datasets import PublishedDataset
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategies import (
    Strategy,
    StrategyStateTransition,
    StrategyVersionLifecycle,
    StrategyVersionState,
)
from qtrade_mock.backtesting import MockBacktestRunStore
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ports import MockObjectStore, MockTradingEngine
from qtrade_mock.strategies import MockStrategyStore

FIXTURES = Path(__file__).parent / "fixtures/contracts"


def load(model):
    return model.model_validate_json((FIXTURES / f"{model.__name__}.json").read_text())


def test_submission_freezes_manifest_before_engine_and_syncs_immutable_result():
    actor, workspace_id = uuid4(), uuid4()
    workspace = Workspace(workspace_id, "Research", "team", actor, False, False, datetime.now(UTC))
    access = WorkspaceAccess(workspace, actor, Role.ADMIN)
    now = datetime.now(UTC)
    strategy_id, version_id, dataset_id = uuid4(), uuid4(), uuid4()
    source = "def signal(close):\n    return close[-1]\n"
    version = StrategyVersion(
        schema_version=1,
        workspace_id=workspace_id,
        strategy_id=strategy_id,
        strategy_version_id=version_id,
        version=1,
        created_by=actor,
        created_at=now,
        code_object_key=f"strategies/{strategy_id}/v1.py",
        code_sha256=sha256(source.encode()).hexdigest(),
        parameter_schema_json='{"type":"object","properties":{},"required":[]}',
        dependencies_sha256=sha256(b"{}").hexdigest(),
    )
    strategy = Strategy(strategy_id, workspace_id, "Momentum", "", actor, now, now, 1)
    strategy_store = MockStrategyStore()
    strategy_store.create(strategy, version)
    lifecycle = StrategyVersionLifecycle(
        workspace_id, strategy_id, version_id, 1, StrategyVersionState.VALIDATED, now
    )
    strategy_store.transition(
        StrategyVersionState.DRAFT,
        lifecycle,
        StrategyStateTransition(
            uuid4(),
            workspace_id,
            strategy_id,
            version_id,
            1,
            StrategyVersionState.DRAFT,
            StrategyVersionState.VALIDATED,
            actor,
            now,
        ),
    )
    metadata = load(MarketDataset).model_copy(
        update={"workspace_id": workspace_id, "dataset_id": dataset_id, "created_at": now}
    )
    datasets = MockDatasetRepository()
    datasets.put(actor, PublishedDataset(metadata, "b" * 64, None, "c" * 64))
    objects = MockObjectStore()
    runs = MockBacktestRunStore()
    engine = MockTradingEngine()
    service = BacktestService(
        runs,
        StrategyService(strategy_store, objects),
        strategy_store,
        datasets,
        ManifestService(objects),
        engine,
        objects,
        BacktestEngineIdentity("mock", "1", "1", "sha256:" + "a" * 64),
    )
    request = BacktestSubmission(
        strategy_id,
        1,
        dataset_id,
        metadata.start,
        metadata.end,
        Decimal("10000"),
        "USD",
        "cost.v1",
        {},
        random_seed=7,
        execution_model={
            "lot_size": 100,
            "fee_bps": "3",
            "session_open": "09:30:00",
            "session_close": "15:00:00",
            "session_breaks": [["11:30:00", "13:00:00"]],
            "timezone_name": "Asia/Shanghai",
        },
    )

    queued = service.create(access, request)
    manifest = ManifestService(objects).get(workspace_id, queued.run.run_id)
    assert queued.run.status == "queued"
    assert queued.engine_job_id == queued.run.run_id
    assert manifest.strategy_version_id == version_id
    assert manifest.dataset_sha256 == metadata.content_sha256
    assert manifest.random_seed == 7
    assert manifest.parameters_json == "{}"
    assert manifest.execution_config_json is not None
    assert '"initial_cash":"10000"' in manifest.execution_config_json
    assert '"cost_model_version":"cost.v1"' in manifest.execution_config_json
    assert json.loads(manifest.execution_config_json)["corporate_actions"] == []
    assert json.loads(manifest.execution_config_json)["execution_model"] == {
        "daily_price_limit_fraction": None,
        "fee_bps": "3",
        "impact_bps": "0",
        "latency_bars": 1,
        "limit_queue_fraction": "1",
        "lot_size": 100,
        "max_volume_fraction": "1",
        "minimum_fee": "0",
        "periods_per_year": None,
        "sell_tax_bps": "0",
        "session_breaks": [["11:30:00", "13:00:00"]],
        "session_close": "15:00:00",
        "session_open": "09:30:00",
        "settlement_days": 0,
        "slippage_bps": "0",
        "tick_aggregation_seconds": 3,
        "tick_size": "0",
        "timezone_name": "Asia/Shanghai",
    }
    assert (
        manifest.execution_config_sha256
        == sha256(manifest.execution_config_json.encode()).hexdigest()
    )
    assert len(service.history(access, queued.run.run_id)) == 1

    normalized_result = load(BacktestResult).model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "strategy_version_id": version_id,
        }
    )
    result_bytes = normalized_result.model_dump_json().encode()
    result = objects.put(
        workspace_id,
        f"runs/{queued.run.run_id}/result.json",
        result_bytes,
        content_type="application/json",
        expected_sha256=sha256(result_bytes).hexdigest(),
    )
    engine.complete(workspace_id, queued.engine_job_id, result)
    completed = service.get(access, queued.run.run_id)
    assert completed.run.status == "completed"
    assert completed.run.progress == 100
    assert completed.run.result_object_key == result.key
    assert service.report(access, queued.run.run_id) == normalized_result
    assert len(service.history(access, queued.run.run_id)) == 2
    changed_run = completed.run.model_copy(update={"initial_cash": Decimal("20000")})
    with pytest.raises(PortError) as caught:
        runs.update(
            completed.revision,
            replace(completed, run=changed_run, revision=completed.revision + 1),
            None,
        )
    assert caught.value.code == ErrorCode.INVALID_INPUT

    failed_queue = service.create(access, replace(request, random_seed=10))
    engine.fail(workspace_id, failed_queue.run.run_id, ErrorCode.UNAVAILABLE)
    failed = service.get(access, failed_queue.run.run_id)
    assert failed.run.status == "failed"
    assert failed.run.error_code == ErrorCode.UNAVAILABLE.value
    assert [item.status for item in service.history(access, failed_queue.run.run_id)] == [
        "queued",
        "failed",
    ]

    class CrashingEngine(MockTradingEngine):
        def submit(self, run, manifest):
            raise RuntimeError("adapter detail must stay private")

    crash_service = BacktestService(
        MockBacktestRunStore(),
        StrategyService(strategy_store, objects),
        strategy_store,
        datasets,
        ManifestService(objects),
        CrashingEngine(),
        objects,
        BacktestEngineIdentity("mock", "1", "1", "sha256:" + "a" * 64),
    )
    crash = crash_service.create(access, replace(request, random_seed=11))
    assert crash.run.status == "failed"
    assert crash.run.error_code == ErrorCode.UNAVAILABLE.value


def _frozen_execution_model(
    execution_model,
    *,
    granularity: str = "1d",
    dependencies_sha256: str | None = None,
):
    actor, workspace_id = uuid4(), uuid4()
    workspace = Workspace(workspace_id, "Research", "team", actor, False, False, datetime.now(UTC))
    access = WorkspaceAccess(workspace, actor, Role.ADMIN)
    now = datetime.now(UTC)
    strategy_id, version_id, dataset_id = uuid4(), uuid4(), uuid4()
    source = "def signal(close):\n    return close[-1]\n"
    version = StrategyVersion(
        schema_version=1,
        workspace_id=workspace_id,
        strategy_id=strategy_id,
        strategy_version_id=version_id,
        version=1,
        created_by=actor,
        created_at=now,
        code_object_key=f"strategies/{strategy_id}/v1.py",
        code_sha256=sha256(source.encode()).hexdigest(),
        parameter_schema_json='{"type":"object","properties":{},"required":[]}',
        dependencies_sha256=dependencies_sha256 or sha256(b"{}").hexdigest(),
    )
    strategy = Strategy(strategy_id, workspace_id, "Momentum", "", actor, now, now, 1)
    strategy_store = MockStrategyStore()
    strategy_store.create(strategy, version)
    strategy_store.transition(
        StrategyVersionState.DRAFT,
        StrategyVersionLifecycle(
            workspace_id, strategy_id, version_id, 1, StrategyVersionState.VALIDATED, now
        ),
        StrategyStateTransition(
            uuid4(),
            workspace_id,
            strategy_id,
            version_id,
            1,
            StrategyVersionState.DRAFT,
            StrategyVersionState.VALIDATED,
            actor,
            now,
        ),
    )
    metadata = load(MarketDataset).model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "granularity": granularity,
            "created_at": now,
        }
    )
    datasets = MockDatasetRepository()
    datasets.put(actor, PublishedDataset(metadata, "b" * 64, None, "c" * 64))
    objects = MockObjectStore()
    service = BacktestService(
        MockBacktestRunStore(),
        StrategyService(strategy_store, objects),
        strategy_store,
        datasets,
        ManifestService(objects),
        MockTradingEngine(),
        objects,
        BacktestEngineIdentity("mock", "1", "1", "sha256:" + "a" * 64),
    )
    queued = service.create(
        access,
        BacktestSubmission(
            strategy_id,
            1,
            dataset_id,
            metadata.start,
            metadata.end,
            Decimal("10000"),
            "CNY",
            "cost.v1",
            {},
            execution_model=execution_model,
        ),
    )
    manifest = ManifestService(objects).get(workspace_id, queued.run.run_id)
    return json.loads(manifest.execution_config_json)["execution_model"]


def _frozen_timezone(execution_model):
    return _frozen_execution_model(execution_model)["timezone_name"]


def test_daily_execution_defaults_to_exchange_zone_and_keeps_explicit_choice():
    # Daily bars are stamped at exchange midnight Asia/Shanghai. A UTC default
    # would read every trading date one day early and apply ex-date corporate
    # actions one bar late.
    assert _frozen_timezone(None) == "Asia/Shanghai"
    assert _frozen_timezone({}) == "Asia/Shanghai"
    assert _frozen_timezone({"lot_size": 100}) == "Asia/Shanghai"
    assert _frozen_timezone({"timezone_name": "UTC"}) == "UTC"


def test_cny_empty_execution_model_applies_a_share_defaults_and_annualization():
    frozen = _frozen_execution_model({}, granularity="1d")
    assert frozen["lot_size"] == 100
    assert frozen["fee_bps"] == "3"
    assert frozen["minimum_fee"] == "5"
    assert frozen["sell_tax_bps"] == "5"
    assert frozen["slippage_bps"] == "5"
    assert frozen["tick_size"] == "0.01"
    assert frozen["settlement_days"] == 1
    assert frozen["max_volume_fraction"] == "0.1"
    assert frozen["daily_price_limit_fraction"] == "0.1"
    assert frozen["periods_per_year"] == 252
    hourly = _frozen_execution_model({"lot_size": 100}, granularity="1h")
    assert hourly["periods_per_year"] == 252 * 4


def test_strategy_version_with_third_party_dependencies_is_rejected():
    with pytest.raises(PortError) as unsupported:
        _frozen_execution_model(
            {},
            dependencies_sha256=sha256(b'{"numpy":"==2.1.0"}').hexdigest(),
        )
    assert unsupported.value.code == ErrorCode.UNSUPPORTED


def test_dataset_list_orders_multi_instrument_composed_datasets_first():
    actor, workspace_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    datasets = MockDatasetRepository()
    composed = load(MarketDataset).model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": uuid4(),
            "instrument_ids": ("600519.SH", "000001.SZ", "601288.SH"),
            "granularity": "1d",
            "created_at": now,
        }
    )
    fragment = load(MarketDataset).model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": uuid4(),
            "instrument_ids": ("600519.SH",),
            "granularity": "1h",
            "created_at": now.replace(microsecond=0),
        }
    )
    datasets.put(actor, PublishedDataset(composed, "b" * 64, None, "c" * 64))
    datasets.put(actor, PublishedDataset(fragment, "d" * 64, None, "e" * 64))
    listed = datasets.list(workspace_id)
    assert listed[0].metadata.dataset_id == composed.dataset_id
    assert listed[1].metadata.dataset_id == fragment.dataset_id

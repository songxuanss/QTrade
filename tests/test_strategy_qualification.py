"""Qualification must come from a completed run, never from caller input."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from qtrade_application.strategies import StrategyService
from qtrade_application.strategy_qualification import BacktestRunQualification
from qtrade_contracts import BacktestRun
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunRecord
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.strategies import StrategyQualification
from qtrade_mock.backtesting import MockBacktestRunStore
from qtrade_mock.ports import MockObjectStore
from qtrade_mock.strategies import MockStrategyStore

FIXTURES = Path(__file__).parent / "fixtures/contracts"


def _setup():
    workspace_id, user_id = uuid4(), uuid4()
    access = WorkspaceAccess(
        Workspace(workspace_id, "Research", "team", user_id, False, False, datetime.now(UTC)),
        user_id,
        Role.ADMIN,
    )
    store = MockStrategyStore()
    strategies = StrategyService(store, MockObjectStore())
    strategy, version = strategies.create(
        access,
        name="Qualification",
        description="",
        code="def signal(close):\n    return close[-1]\n",
        parameter_schema_json=json.dumps({"type": "object", "properties": {}, "required": []}),
        dependencies_json="{}",
    )
    return access, strategies, strategy, version, store


def _run(access, version, *, status: str, run_id: UUID | None = None) -> BacktestRun:
    template = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    run_id = run_id or uuid4()
    return template.model_copy(
        update={
            "workspace_id": access.workspace.workspace_id,
            "run_id": run_id,
            "strategy_version_id": version.strategy_version_id,
            "created_by": access.user_id,
            "status": status,
            "progress": 100 if status == "completed" else 0,
            "result_object_key": f"backtests/{run_id}/result.json"
            if status == "completed"
            else None,
        }
    )


def _publish(runs, access, version) -> BacktestRun:
    """Store a real queued->completed transition and return the completed run."""
    run_id = uuid4()
    queued = _run(access, version, status="queued", run_id=run_id)
    runs.create(
        BacktestRunRecord(queued, None, 1),
        BacktestRunEvent(
            uuid4(),
            access.workspace.workspace_id,
            run_id,
            "queued",
            0,
            datetime.now(UTC),
            None,
            1,
        ),
    )
    completed = _run(access, version, status="completed", run_id=run_id)
    runs.update(
        1,
        BacktestRunRecord(completed, None, 2),
        BacktestRunEvent(
            uuid4(),
            access.workspace.workspace_id,
            run_id,
            "completed",
            100,
            datetime.now(UTC),
            None,
            2,
        ),
    )
    return completed


def test_only_a_completed_run_of_the_exact_version_qualifies():
    access, strategies, strategy, version, store = _setup()
    runs = MockBacktestRunStore()
    qualification = BacktestRunQualification(store, runs)
    workspace_id = access.workspace.workspace_id

    assert qualification.for_version(workspace_id, strategy.strategy_id, 1) == (
        StrategyQualification()
    )

    pending = _run(access, version, status="queued")
    runs.create(
        BacktestRunRecord(pending, None, 1),
        BacktestRunEvent(
            uuid4(),
            workspace_id,
            pending.run_id,
            "queued",
            0,
            datetime.now(UTC),
            None,
            1,
        ),
    )
    assert not qualification.for_version(workspace_id, strategy.strategy_id, 1).backtest_succeeded

    completed = _publish(runs, access, version)
    result = qualification.for_version(workspace_id, strategy.strategy_id, 1)
    assert result.backtest_succeeded and result.backtest_run_id == completed.run_id

    second = strategies.revise(
        access,
        strategy.strategy_id,
        code="def signal(close):\n    return close[-2]\n",
        parameter_schema_json=json.dumps({"type": "object", "properties": {}, "required": []}),
        dependencies_json="{}",
    )
    newer = _publish(runs, access, second)
    # Versions are immutable: a run of the newer version must not qualify the
    # older one and vice versa.
    assert qualification.for_version(workspace_id, strategy.strategy_id, 1).backtest_run_id == (
        completed.run_id
    )
    assert qualification.for_version(workspace_id, strategy.strategy_id, 2).backtest_run_id == (
        newer.run_id
    )
    assert not qualification.for_version(workspace_id, uuid4(), 1).backtest_succeeded

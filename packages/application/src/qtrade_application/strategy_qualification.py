"""Derive Strategy Version qualification from durable Backtest Runs.

The lifecycle must never trust a caller-supplied flag. A version is
``backtested`` only when the run store holds a completed run bound to that
exact immutable Strategy Version (workspace, strategy and version ID).
"""

from uuid import UUID

from qtrade_domain.backtesting import BacktestRunPort
from qtrade_domain.strategies import (
    StrategyPort,
    StrategyQualification,
)


class BacktestRunQualification:
    def __init__(self, strategies: StrategyPort, runs: BacktestRunPort) -> None:
        self._strategies = strategies
        self._runs = runs

    def for_version(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyQualification:
        frozen = next(
            (
                item
                for item in self._strategies.versions(workspace_id, strategy_id)
                if item.version == version
            ),
            None,
        )
        if frozen is None:
            return StrategyQualification()
        for record in self._runs.list(workspace_id, 200):
            run = record.run
            if (
                run.strategy_version_id == frozen.strategy_version_id
                and run.status == "completed"
                and run.progress == 100
                and run.result_object_key
            ):
                return StrategyQualification(
                    backtest_run_id=run.run_id,
                    backtest_succeeded=True,
                )
        return StrategyQualification()

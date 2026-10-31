"""Backtest engine seam that only enqueues durable work for the Worker."""

from uuid import UUID

from qtrade_contracts import ArtifactManifest, BacktestRun
from qtrade_domain.ports import ComputeJob, ErrorCode, PortError, TradingEnginePort


class QueuedBacktestEngine(TradingEnginePort):
    """Persist a queued run without executing source in the API process.

    The Worker claims the row from PostgreSQL, runs the isolated strategy
    program, and publishes the result. `status` deliberately does not invent an
    in-memory job: the authoritative status is the durable run row.
    """

    def submit(self, run: BacktestRun, manifest: ArtifactManifest) -> ComputeJob:
        if (
            run.workspace_id != manifest.workspace_id
            or run.run_id != manifest.run_id
            or run.manifest_id != manifest.manifest_id
            or run.dataset_id != manifest.dataset_id
            or run.strategy_version_id != manifest.strategy_version_id
            or manifest.kind != "backtest"
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "run/manifest mismatch")
        return ComputeJob(run.workspace_id, run.run_id, "queued", 0)

    def status(self, workspace_id: UUID, job_id: UUID) -> ComputeJob:
        del workspace_id, job_id
        raise PortError(ErrorCode.NOT_FOUND, "durable backtest is owned by the worker queue")

    def cancel(self, workspace_id: UUID, job_id: UUID) -> ComputeJob:
        del workspace_id, job_id
        raise PortError(ErrorCode.NOT_FOUND, "durable backtest cancellation uses the run store")

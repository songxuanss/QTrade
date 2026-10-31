from pathlib import Path

import pytest

from qtrade_composition.backtest_queue import QueuedBacktestEngine
from qtrade_contracts import ArtifactManifest, BacktestRun
from qtrade_domain.ports import ErrorCode, PortError

FIXTURES = Path(__file__).parent / "fixtures/contracts"


def test_queued_engine_only_enqueues_and_never_owns_status():
    engine = QueuedBacktestEngine()
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    job = engine.submit(run, manifest)
    assert job.status == "queued"
    assert job.job_id == run.run_id
    with pytest.raises(PortError) as status:
        engine.status(run.workspace_id, run.run_id)
    assert status.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(PortError) as cancel:
        engine.cancel(run.workspace_id, run.run_id)
    assert cancel.value.code == ErrorCode.NOT_FOUND

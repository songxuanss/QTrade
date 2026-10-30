"""The required CI job must reject every unsuccessful or missing prerequisite."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts/check-ci-gates.sh"


def _run_gate(*, python: str, frontend: str, integration: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHON_RESULT": python,
        "FRONTEND_RESULT": frontend,
        "INTEGRATION_RESULT": integration,
    }
    return subprocess.run(
        ["bash", str(GATE)],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_ci_gate_passes_only_when_every_required_job_succeeds() -> None:
    assert _run_gate(python="success", frontend="success", integration="success").returncode == 0
    for field in ("python", "frontend", "integration"):
        statuses = {"python": "success", "frontend": "success", "integration": "success"}
        for failing_status in ("failure", "cancelled", "skipped", ""):
            statuses[field] = failing_status
            result = _run_gate(**statuses)
            assert result.returncode == 1
            assert "CI gate rejected result" in result.stderr

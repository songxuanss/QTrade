import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


def test_images_wait_for_python_and_frontend_gates():
    jobs = WORKFLOW["jobs"]
    assert set(jobs["integration-and-images"]["needs"]) == {"python", "frontend"}
    assert set(jobs["required"]["needs"]) == {"python", "frontend", "integration-and-images"}
    for job in jobs.values():
        assert not job.get("continue-on-error", False)
        for step in job["steps"]:
            assert not step.get("continue-on-error", False)
    assert WORKFLOW["permissions"] == {"contents": "read"}


@pytest.mark.parametrize(
    "python,frontend,integration,expected",
    [
        ("success", "success", "success", 0),
        ("failure", "success", "skipped", 1),
        ("success", "failure", "skipped", 1),
        ("success", "success", "failure", 1),
        ("cancelled", "success", "skipped", 1),
    ],
)
def test_actual_required_gate_rejects_failed_or_skipped_jobs(
    python, frontend, integration, expected
):
    step = WORKFLOW["jobs"]["required"]["steps"][0]
    env = dict(
        os.environ, PYTHON_RESULT=python, FRONTEND_RESULT=frontend, INTEGRATION_RESULT=integration
    )
    result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=False)
    assert result.returncode == expected

"""Observe actual deployed UI baseline resources without printing private config."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    if phase not in {"baseline", "retest"}:
        raise SystemExit("Invalid verification phase")
    output = ROOT / f".tools/verification-local/review/collection-{phase}-resources.json"
    samples = []
    started = datetime.now(UTC).isoformat()
    process = subprocess.Popen(
        ["node", "tests/verification-collection-baseline-ui.mjs"],
        cwd=ROOT / "apps/web",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ, QTRADE_BASELINE_PHASE=phase),
    )
    try:
        while process.poll() is None:
            result = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    "qtrade-collector-collector-1",
                    "qtrade-collector-collector-db-1",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Docker stats contains resource values only, no environment.
                samples.append(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "containers": [json.loads(v) for v in result.stdout.splitlines()],
                    }
                )
            output.write_text(json.dumps({"started_at": started, "samples": samples}, indent=2))
            output.chmod(0o600)
            time.sleep(2)
        stdout, _ = process.communicate()
        print(stdout, end="")
        if process.returncode:
            raise SystemExit("Baseline UI observation failed; inspect private evidence")
    finally:
        # This runner owns only this child. Do not stop collector/Worker or
        # cancel real tasks just because a monitoring process is interrupted.
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

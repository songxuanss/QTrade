"""Build Compose application images labelled with the clean source revision."""

import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip():
    raise SystemExit("Commit changes before building a release")
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
subprocess.run(
    ["docker", "compose", "build", "api", "web", "worker", "realtime", "strategy-runner"],
    cwd=root,
    env={**os.environ, "QTRADE_BUILD_REVISION": revision},
    check=True,
)
print(f"Built application revision {revision}")

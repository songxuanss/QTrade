"""Temporarily require acknowledgement for small live UI tasks, always restore.

Only the confirmation threshold changes; source budgets/credentials and stored
configuration are untouched. Run on the authorized local verification stack.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
override = ROOT / ".tools/verification-local/research-confirmation.override.yaml"
if override.exists():
    raise SystemExit("Confirmation override already exists; inspect ownership before retry")
compose = [
    "docker",
    "compose",
    "--env-file",
    ".env.collector",
    "-f",
    "compose.collector.yaml",
    "-f",
    "compose.collector.local.yaml",
]


def run(command, cwd=ROOT, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode:
        # Browser/DB exception text may include private input. Do not log it.
        raise RuntimeError("verification command failed; detailed output withheld")


def active_guard():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "qtrade-collector-collector-1",
            "python",
            "-c",
            "from qtrade_collector.main import app; "
            "print(app.state.collection_service.plan_guard.model_dump_json())",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Cannot inspect active plan guard")
    return json.loads(result.stdout)


original = active_guard()


try:
    override.write_text(
        'services:\n  collector:\n    environment:\n      COLLECTOR_PLAN_CONFIRM_PARTS: "1"\n'
    )
    override.chmod(0o600)
    run(compose + ["-f", str(override), "up", "-d", "--no-build", "--wait", "collector"])
    print("Temporary confirmation threshold active; budgets unchanged", flush=True)
    for name in ("financial", "distribution"):
        run(["node", f"tests/verification-{name}-ui.mjs", "--confirm-small"], ROOT / "apps/web")
        print(
            f"PASS {name}: explicit keyboard confirmation / real source / publication / refresh",
            flush=True,
        )
    run([sys.executable, "scripts/verification-research-ui-archive.py", "--confirmed"])
    print("PASS confirmed UI tasks: nonempty persisted raw/standard and hashes", flush=True)
    env = dict(os.environ) | {"QTRADE_VERIFICATION_ORIGIN": "http://127.0.0.1:13000"}
    run(
        ["node", "tests/verification-independent-collector.mjs", "--confirm-small"],
        ROOT / "apps/web",
        env,
    )
    env.update(
        {
            "QTRADE_VERIFY_CONFIRMED": "true",
            "QTRADE_VERIFY_RAW_CAPTURES": "true",
            "QTRADE_VERIFY_COLLECTION_QUALITY": "true",
            "PYTHONPATH": ":".join(
                ("packages/data-contracts/src", "packages/data-core/src", "adapters/data_http/src")
            ),
        }
    )
    run([sys.executable, "scripts/verification-independent-collection.py"], env=env)
    print(
        "PASS confirmed bars/factors/pool: UI, persisted nonempty raw/standard, quality and market",
        flush=True,
    )
finally:
    # Restore the baseline Compose configuration, including any operator limits
    # present there; do not overwrite .env files or other scope overrides.
    run(compose + ["up", "-d", "--no-build", "--wait", "collector"])
    if active_guard() != original:
        raise RuntimeError("Restored plan guard differs from original runtime")
    override.unlink(missing_ok=True)
    print("Original confirmation configuration restored", flush=True)

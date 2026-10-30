#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
docker compose exec -T worker python - < scripts/selftest-runner-worker.py
docker compose exec -T strategy-runner python -m qtrade_runner.selftest
curl -fsS http://127.0.0.1:18000/health >/dev/null

#!/usr/bin/env bash
set -euo pipefail
QTRADE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$QTRADE_ROOT"
QTRADE_UV_BIN=${QTRADE_UV_BIN:-.tools/uv/bin/uv}
"$QTRADE_UV_BIN" run --locked ruff check .
"$QTRADE_UV_BIN" run --locked mypy
"$QTRADE_UV_BIN" run --locked python scripts/check_architecture.py
"$QTRADE_UV_BIN" run --locked python scripts/check_constitution.py
"$QTRADE_UV_BIN" run --locked pytest

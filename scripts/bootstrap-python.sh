#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.tools/uv-cache}"

# Install tooling in this checkout; no global packages or shell profile changes.
if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
  qtrade_python="$PWD/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  qtrade_python="$(command -v python3.12)"
elif [[ -x /home/pauls/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 ]]; then
  qtrade_python=/home/pauls/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12
else
  echo 'Python 3.12 is required. Install it, then rerun this script.' >&2
  exit 1
fi

"$qtrade_python" - <<'PY'
import sys
import urllib.error
import urllib.request
assert sys.version_info[:2] == (3, 12)
try:
    with urllib.request.urlopen('https://pypi.org/simple/uv/', timeout=10) as response:
        assert response.status == 200
except (OSError, urllib.error.URLError) as error:
    sys.exit(f'PyPI is unreachable: {error}. Run in a terminal with registry access.')
print('Python 3.12 and PyPI access OK')
PY

if [[ ! -x .tools/uv/bin/uv ]]; then
  "$qtrade_python" -m venv .tools/uv
  .tools/uv/bin/python -m pip install --disable-pip-version-check uv
fi
.tools/uv/bin/uv --version
if [[ -f uv.lock ]]; then
  .tools/uv/bin/uv sync --locked --python "$qtrade_python"
else
  .tools/uv/bin/uv lock --python "$qtrade_python"
  .tools/uv/bin/uv sync --locked --python "$qtrade_python"
fi
.tools/uv/bin/uv run --no-sync ruff check .
.tools/uv/bin/uv run --no-sync mypy
# pytest returns 5 for the initial empty suite; do not mask actual failures.
qtrade_test_status=0
.tools/uv/bin/uv run --no-sync pytest || qtrade_test_status=$?
if [[ "$qtrade_test_status" == 5 ]]; then
  echo 'pytest: no tests collected (empty baseline).'
elif [[ "$qtrade_test_status" != 0 ]]; then
  exit "$qtrade_test_status"
fi

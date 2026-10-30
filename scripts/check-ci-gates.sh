#!/usr/bin/env bash
set -euo pipefail

for result in "${PYTHON_RESULT:-}" "${FRONTEND_RESULT:-}" "${INTEGRATION_RESULT:-}"; do
    if [[ "$result" != "success" ]]; then
        echo "CI gate rejected result: ${result:-missing}" >&2
        exit 1
    fi
done

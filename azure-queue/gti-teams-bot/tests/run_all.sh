#!/usr/bin/env bash
# Runs all three test suites as separate pytest invocations — see
# tests/README.md for why they can't be collected in a single process.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

status=0
for suite in tests/ingest tests/worker tests/cross_app; do
    echo "=== ${suite} ==="
    python -m pytest "${suite}" "$@" || status=1
done
exit "${status}"

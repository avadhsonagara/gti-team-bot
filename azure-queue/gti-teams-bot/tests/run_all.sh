#!/usr/bin/env bash
# Runs all four test suites as separate pytest invocations — see
# tests/README.md for why they can't be collected in a single process.
# rs-alerts-function lives outside gti-teams-bot/ (a standalone app with its
# own colocated tests/), so it's invoked from its own directory below.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

status=0
for suite in tests/ingest tests/worker tests/cross_app; do
    echo "=== ${suite} ==="
    python -m pytest "${suite}" "$@" || status=1
done

echo "=== ../rs-alerts-function/tests ==="
(cd ../rs-alerts-function && python -m pytest tests "$@") || status=1

exit "${status}"

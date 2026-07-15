#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUN_ID="${RUN_ID:?Set RUN_ID to the completed labeled evaluation run ID.}"
OFFICIAL_TEST_RUN_ID="${OFFICIAL_TEST_RUN_ID:?Set OFFICIAL_TEST_RUN_ID to the completed official-test submission run ID.}"

exec python3 scripts/build_development_excluded_report.py \
  --run-root "data/outputs/finverisql/${RUN_ID}" \
  --official-test-run-root "data/outputs/finverisql/${OFFICIAL_TEST_RUN_ID}" \
  "$@"

#!/bin/bash
# Functional test for analyze_egress.py.
# Runs the analyzer against a known fixture and diffs against expected output.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

ANALYZER="${SCRIPT_DIR}/analyze_egress.py"
FIXTURE="${SCRIPT_DIR}/testdata/sample.jsonl"
EXPECTED="${SCRIPT_DIR}/testdata/expected.yaml"
ACTUAL=$(mktemp)
trap 'rm -f "$ACTUAL"' EXIT

python3 "$ANALYZER" "$FIXTURE" > "$ACTUAL"

if diff -u "$EXPECTED" "$ACTUAL"; then
    echo "PASS: analyze_egress.py output matches expected"
    exit 0
else
    echo "FAIL: analyze_egress.py output differs from expected (see diff above)" >&2
    exit 1
fi

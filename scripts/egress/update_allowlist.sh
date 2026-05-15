#!/bin/bash
# Post-capture analysis: merge egress logs from both modes, diff against the
# current allow list, and either show the diff (dry-run) or open a PR.
#
# Environment:
#   DRY_RUN    "true" (default) — print diff to step summary, post as PR comment
#              "false"          — commit updated file and open a PR
#   PR_NUMBER  PR number to comment on (set by slash command; empty = no comment)
#   GH_TOKEN   GitHub token with contents:write + pull-requests:write

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

DRY_RUN="${DRY_RUN:-true}"
PR_NUMBER="${PR_NUMBER:-}"

ALLOWLIST="${ENCLAVE_DIR}/docs/EGRESS_ALLOWLIST.yaml"
GENERATED=$(mktemp)

trap 'rm -f "$GENERATED"' EXIT

# ── Build the generated allow list ────────────────────────────────────────────

LOG_FILES=$(find egress-logs/ -name 'opensnitch-connections.json.gz' -type f 2>/dev/null || true)

if [ -z "$LOG_FILES" ]; then
    echo "No egress log files found — nothing to analyze." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    exit 0
fi

# shellcheck disable=SC2086
python3 "${SCRIPT_DIR}/analyze_egress.py" $LOG_FILES > "$GENERATED"

# ── Compare with current allow list ───────────────────────────────────────────

DIFF=$(diff -u "$ALLOWLIST" "$GENERATED" || true)

if [ -z "$DIFF" ]; then
    echo "Allow list is up to date — no changes detected." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    exit 0
fi

# ── There is a diff ───────────────────────────────────────────────────────────

SUMMARY="### Egress allow list changes\n\`\`\`diff\n${DIFF}\n\`\`\`"

# Always print to step summary
printf '%b\n' "$SUMMARY" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

if [ "$DRY_RUN" = "true" ]; then
    echo "Dry-run mode — not creating a PR."
    echo "$SUMMARY"
    if [ -n "$PR_NUMBER" ] && [ -n "${GH_TOKEN:-}" ]; then
        gh pr comment "$PR_NUMBER" --body "$(printf '%b' "$SUMMARY")"
    fi
    exit 0
fi

# ── Open a PR with the updated allow list ─────────────────────────────────────

BRANCH="egress-allowlist/$(date +%Y-%m-%d)-${GITHUB_RUN_ID:-local}"
git config user.email "github-actions[bot]@users.noreply.github.com"
git config user.name "github-actions[bot]"

git checkout -B "$BRANCH"
cp "$GENERATED" "$ALLOWLIST"
git add "$ALLOWLIST"
git commit -m "$(cat <<'EOF'
Update egress connectivity allow list

Auto-generated from OpenSnitch capture during CI e2e deployment.

Signed-off-by: github-actions[bot] <github-actions[bot]@users.noreply.github.com>
Assisted-by: egress-capture workflow
EOF
)"
git push --force-with-lease origin "$BRANCH"

# Reuse an existing open PR if one is present to avoid duplicates.
EXISTING_PR=$(gh pr list --state open --search "head:egress-allowlist/ in:head" --json number --jq '.[0].number' || true)
if [ -n "$EXISTING_PR" ]; then
    echo "Existing PR #${EXISTING_PR} found; skipping pr create."
    exit 0
fi

gh pr create \
    --title "Update egress connectivity allow list ($(date +%Y-%m-%d))" \
    --body "$(cat <<'EOF'
## Summary

Auto-generated from OpenSnitch egress capture during the daily CI e2e deployment.

Changes detected in `docs/EGRESS_ALLOWLIST.yaml` — see diff in the workflow step summary.

## Review checklist

- [ ] New entries are expected (new operator, new upstream dependency)
- [ ] Removed entries are no longer needed
- [ ] No unexpected external destinations
EOF
)" \
    --base main \
    --head "$BRANCH"

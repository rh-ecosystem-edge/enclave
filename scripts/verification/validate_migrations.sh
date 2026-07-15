#!/bin/bash
# Validate migration files in playbooks/tasks/migrations/.
#
# Checks:
#   1. Monotonic ordering: any new migration timestamp is strictly greater than
#      the maximum timestamp already on origin/main.
#   2. Not in future: every migration timestamp is <= current UTC time.
#   3. Uniqueness: no two files share the same 14-digit timestamp prefix.
#   4. Immutability: no migration already on origin/main has been modified.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"

MIGRATIONS_DIR="${ENCLAVE_DIR}/playbooks/tasks/migrations"
failed=0

# --- Collect all migration filenames ---

mapfile -t all_files < <(
    find "${MIGRATIONS_DIR}" -maxdepth 1 -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_*.yaml' \
        | sort
)

if [ "${#all_files[@]}" -eq 0 ]; then
    echo "No migration files found — nothing to validate."
    exit 0
fi

# --- 1. Monotonic ordering check ---

# Determine which files exist on origin/main
mapfile -t main_files < <(
    git ls-tree -r --name-only origin/main -- playbooks/tasks/migrations/ 2>/dev/null \
        | grep -E "^playbooks/tasks/migrations/[0-9]{14}_" \
        | sed 's|.*/||' \
        | sort \
        || true
)

max_main_ts=0
for f in "${main_files[@]:-}"; do
    ts="${f:0:14}"
    if (( ts > max_main_ts )); then
        max_main_ts="$ts"
    fi
done

# Check new migrations (files not on main)
for path in "${all_files[@]}"; do
    fname="$(basename "$path")"
    ts="${fname:0:14}"
    is_new=true
    for mf in "${main_files[@]:-}"; do
        if [ "$mf" = "$fname" ]; then
            is_new=false
            break
        fi
    done
    if $is_new && (( ts <= max_main_ts )); then
        error "$fname timestamp ($ts) is not greater than max on main ($max_main_ts)"
        failed=1
    fi
done

# --- 2. Not-in-future check ---

now="$(date -u +%Y%m%d%H%M%S)"
for path in "${all_files[@]}"; do
    fname="$(basename "$path")"
    ts="${fname:0:14}"
    if (( ts > now )); then
        error "$fname timestamp ($ts) is in the future (current UTC: $now)"
        failed=1
    fi
done

# --- 3. Uniqueness check ---

declare -A seen_ts
for path in "${all_files[@]}"; do
    fname="$(basename "$path")"
    ts="${fname:0:14}"
    if [ -n "${seen_ts[$ts]+x}" ]; then
        error "duplicate timestamp prefix $ts: ${seen_ts[$ts]} and $fname"
        failed=1
    fi
    seen_ts[$ts]="$fname"
done

# --- 4. Immutability check ---

if git rev-parse origin/main &>/dev/null; then
    while IFS= read -r fname; do
        # Only flag files that already exist on main (not newly added)
        for mf in "${main_files[@]:-}"; do
            if [ "$mf" = "$fname" ]; then
                error "existing migration $fname has been modified — migrations are immutable"
                failed=1
                break
            fi
        done
    done < <(git diff --name-only origin/main -- playbooks/tasks/migrations/ \
        | grep -E '^playbooks/tasks/migrations/[0-9]{14}_' \
        | sed 's|.*/||' \
        || true)
fi

if [ "$failed" -eq 0 ]; then
    echo "Migration validation passed (${#all_files[@]} files checked)"
fi

exit "$failed"

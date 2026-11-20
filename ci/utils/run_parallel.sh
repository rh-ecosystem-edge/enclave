#!/bin/bash
# Run named commands in parallel and wait for all of them.
#
# Each command runs in the background with stdin closed, its output streamed with a
# "[name] " prefix and kept in parallel_<name>.log. The script waits for every task
# (no fail-fast), then reports each result and duration. In GitHub Actions it writes
# <name>=success|failure and <name>_seconds=<n> to $GITHUB_OUTPUT and a table to the
# step summary. A cancelled job (SIGINT/SIGTERM) stops all tasks. Exits 1 if any
# task failed.
#
# Usage: ./run_parallel.sh <name> '<command>' [<name> '<command>' ...]
# Names: letters, digits and underscores (they become step output keys).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/ci/lib/output.sh"

if [ $# -lt 2 ] || [ $(($# % 2)) -ne 0 ]; then
    error "Usage: $0 <name> '<command>' [<name> '<command>' ...]"
    exit 1
fi

names=()
cmds=()
while [ $# -gt 0 ]; do
    if [[ ! "$1" =~ ^[A-Za-z0-9_]+$ ]]; then
        error "Invalid task name '$1' (letters, digits and underscores only)"
        exit 1
    fi
    names+=("$1")
    cmds+=("$2")
    shift 2
done

pids=()
starts=()
stop_all() {
    warn "Interrupted; stopping all tasks"
    for pid in "${pids[@]}"; do
        pkill -TERM -P "$pid" 2>/dev/null || true
        kill -TERM "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    exit 130
}
trap stop_all INT TERM

for i in "${!names[@]}"; do
    name="${names[$i]}"
    log="parallel_${name}.log"
    info "Starting task '${name}': ${cmds[$i]} (log: ${log})"
    rm -f "${log}.end"
    starts+=("$(date +%s)")
    # Each task records its own end time, so a short task isn't charged for waiting on
    # the ones collected before it.
    (
        set -o pipefail
        rc=0
        bash -c "${cmds[$i]}" </dev/null 2>&1 | tee "$log" | sed -u "s/^/[${name}] /" || rc=$?
        date +%s > "${log}.end"
        exit "$rc"
    ) &
    pids+=("$!")
done

results=()
durations=()
failed=0
for i in "${!names[@]}"; do
    rc=0
    wait "${pids[$i]}" || rc=$?
    end=$(cat "parallel_${names[$i]}.log.end" 2>/dev/null || date +%s)
    durations+=("$(( end - starts[i] ))")
    if [ "$rc" -eq 0 ]; then
        results+=("success")
    else
        results+=("failure")
        failed=1
    fi
done
trap - INT TERM

echo ""
for i in "${!names[@]}"; do
    secs="${durations[$i]}"
    line="${names[$i]}: ${results[$i]} in $((secs / 60))m$((secs % 60))s"
    if [ "${results[$i]}" = "success" ]; then success "$line"; else error "$line"; fi
    if [ -n "${GITHUB_OUTPUT:-}" ]; then
        echo "${names[$i]}=${results[$i]}" >> "$GITHUB_OUTPUT"
        echo "${names[$i]}_seconds=${secs}" >> "$GITHUB_OUTPUT"
    fi
done

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        echo "| Task | Result | Duration |"
        echo "|---|---|---|"
        for i in "${!names[@]}"; do
            secs="${durations[$i]}"
            icon=$([ "${results[$i]}" = "success" ] && echo "✅" || echo "❌")
            echo "| ${names[$i]} | ${icon} ${results[$i]} | $((secs / 60))m$((secs % 60))s |"
        done
    } >> "$GITHUB_STEP_SUMMARY"
fi

exit "$failed"

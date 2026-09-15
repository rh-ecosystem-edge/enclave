#!/bin/bash
# Start stopped GitHub Actions runner services on the local host
#
# CI runners get OOM-killed when dangling VMs exhaust host memory. This script
# enumerates the runner systemd units installed on THIS host and starts any that
# are not currently active. It is best-effort: a single unit that fails to start
# never aborts the run.
#
# The host association is implicit — the script only sees units on the machine it
# runs on, so there is no need to look up which runners belong to which host.
#
# Usage:
#   ./start-stopped-runners.sh
#   make -f Makefile.ci start-stopped-runners   # for manual OOM recovery
#
# Requires passwordless sudo (already configured for the github-runner user).

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared logging utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

# Match every installed runner unit regardless of state. list-unit-files reads
# from disk, so cleanly-stopped units that systemd has unloaded are still found
# (list-units --all would miss them).
readonly RUNNER_UNIT_GLOB='actions.runner.*.service'

main() {
    local started=0 skipped=0 failed=0
    local services svc

    services="$(systemctl list-unit-files --no-legend "${RUNNER_UNIT_GLOB}" \
        2>/dev/null | awk '{print $1}' || true)"

    if [ -z "${services}" ]; then
        warning "No runner services (${RUNNER_UNIT_GLOB}) found on this host"
        output "No runner services found on this host — nothing to start."
        return 0
    fi

    while IFS= read -r svc; do
        [ -n "${svc}" ] || continue

        if systemctl is-active --quiet "${svc}"; then
            skipped=$((skipped + 1))
            continue
        fi

        # OOM-killed units land in 'failed', which blocks a plain start.
        sudo systemctl reset-failed "${svc}" 2>/dev/null || true

        if sudo systemctl start "${svc}"; then
            success "Started ${svc}"
            started=$((started + 1))
        else
            error "Failed to start ${svc}"
            failed=$((failed + 1))
        fi
    done <<< "${services}"

    output "Runner restart summary: started=${started} skipped=${skipped} failed=${failed}"

    # Best-effort: report failures but do not fail the caller.
    return 0
}

main "$@"

#!/usr/bin/env bash
# Age-based reaper for leaked CI infrastructure.
#
# Per-job cleanup only matches its own run's cluster name, so VMs left behind by
# a cancelled/timed-out/killed job are never reclaimed by any later run. This
# sweeps ALL CI clusters (eci-/ecd-/nc-/nd-) on the host and tears down every one
# whose libvirt definition is older than REAP_AGE_HOURS (default 12h, safely
# above the longest e2e job timeout). Safe to run before a job and on a schedule.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"

REAP_AGE_HOURS="${REAP_AGE_HOURS:-12}"
VM_INFRA="${ENCLAVE_DIR}/scripts/infrastructure/vm_infra.py"

# Set REAP_DRY_RUN=1 to report what would be reaped without destroying anything.
DRY_RUN_ARGS=()
if [ "${REAP_DRY_RUN:-}" = "1" ] || [ "${REAP_DRY_RUN:-}" = "true" ]; then
    DRY_RUN_ARGS=(--dry-run)
fi

info "=========================================="
info "Reaping CI clusters older than ${REAP_AGE_HOURS}h"
info "=========================================="

if [ ! -f "${VM_INFRA}" ]; then
    warning "vm_infra.py not found at ${VM_INFRA} — nothing to reap"
    exit 0
fi

# Pass env explicitly via `sudo env`: inline VAR=... prefixes are dropped by
# sudo's env_reset, and `sudo -E` only preserves them when sudoers allows it.
# reap discovers clusters from libvirt; BASE_WORKING_DIR is only an optional
# fallback for locating a cluster's working directory.
sudo env \
    REAP_AGE_HOURS="${REAP_AGE_HOURS}" \
    BASE_WORKING_DIR="${BASE_WORKING_DIR:-}" \
    python3 "${VM_INFRA}" reap "${DRY_RUN_ARGS[@]}"

success "Reap complete"

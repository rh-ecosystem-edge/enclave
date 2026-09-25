#!/bin/bash
# Runner-side driver for the OSAC CLI smoke test.
#
# The smoke test itself (osac_smoke.sh) must run on the Landing Zone, where oc
# and the cluster kubeconfig live and the fulfillment-api route is reachable.
# This driver runs on the CI runner (or a workstation), locates the Landing Zone
# and invokes the smoke test there over SSH, mirroring verify_cluster.sh.
#
# The Enclave repository is already synced to the Landing Zone by the deploy
# flow, so the driver runs the copy of osac_smoke.sh that is present there.
#
# Environment variables:
#   OSAC_CHART_VERSION   Passed through to osac_smoke.sh (optional, reported)
#   OSAC_NAMESPACE       Passed through to osac_smoke.sh (optional)
#   GITHUB_STEP_SUMMARY  GitHub Actions summary file (optional)

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities (logging + Landing Zone SSH helpers)
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/ssh.sh"

LZ_IP="$("${SCRIPT_DIR}/../utils/get_landing_zone_ip.sh")"
if [ -z "${LZ_IP}" ]; then
    error "Could not determine Landing Zone IP"
    exit 1
fi
info "Landing Zone IP: ${LZ_IP}"

# Configure the shared LZ SSH helpers (sets LZ_SSH, LZ_USER, LZ_HOME,
# LZ_ENCLAVE_DIR and SSH_OPTS). Override SSH_OPTS afterwards to drop the util's
# `-q` so SSH's own connection errors still surface in the CI log, and to keep
# the longer connect timeout used by verify_cluster.sh.
setup_ssh_config "${LZ_IP}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
LZ_SESSION_DIR="${LZ_HOME}/sessions/1"
# Evaluated on the LZ: \$PATH expands to the remote PATH at runtime.
LZ_OC_ENV="export PATH=${LZ_SESSION_DIR}/bin:\$PATH KUBECONFIG=${LZ_SESSION_DIR}/ocp-cluster/auth/kubeconfig"

# Shell-quote a value (via printf %q) so it survives the remote shell's parse as a
# single literal token. The SSH command string is re-parsed by bash on the LZ
# before the smoke test runs, so an unquoted override could otherwise inject
# commands there. Result is placed in REPLY.
shell_quote() {
    printf -v REPLY '%q' "$1"
}

# Forward optional overrides to the remote smoke test, shell-quoted so shell
# metacharacters in a value cannot execute on the Landing Zone.
REMOTE_ENV=""
if [ -n "${OSAC_CHART_VERSION:-}" ]; then
    shell_quote "${OSAC_CHART_VERSION}"
    REMOTE_ENV="${REMOTE_ENV} OSAC_CHART_VERSION=${REPLY}"
fi
if [ -n "${OSAC_NAMESPACE:-}" ]; then
    shell_quote "${OSAC_NAMESPACE}"
    REMOTE_ENV="${REMOTE_ENV} OSAC_NAMESPACE=${REPLY}"
fi

info "Running OSAC CLI smoke test on the Landing Zone"
# ssh_exec (from scripts/lib/ssh.sh) runs the command on LZ_SSH with SSH_OPTS.
if ssh_exec "${LZ_OC_ENV} &&${REMOTE_ENV} bash ${LZ_ENCLAVE_DIR}/scripts/verification/osac_smoke.sh"; then
    success "OSAC CLI smoke test passed"
    [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "### ✅ OSAC CLI smoke test passed" >> "${GITHUB_STEP_SUMMARY}"
    exit 0
else
    error "OSAC CLI smoke test failed"
    [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "### ❌ OSAC CLI smoke test failed" >> "${GITHUB_STEP_SUMMARY}"
    exit 1
fi

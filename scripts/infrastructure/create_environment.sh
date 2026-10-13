#!/bin/bash
# Create the Enclave test environment.
#
# vm_infra.py owns cluster identity: when ENCLAVE_CLUSTER_NAME is unset it generates a
# unique name and derives WORKING_DIR from BASE_WORKING_DIR. This script runs it first,
# captures the emitted cluster-env.sh from its stdout (vm_infra logs to stderr), exports
# those values for the remaining steps, and appends the identity to $GITHUB_ENV so later
# CI steps (and the local ci-flow, which points GITHUB_ENV at a temp file) inherit it.
#
# Steps: vm_infra create -> pull secret -> GPU passthrough -> verify networks ->
#        Ironic CA (if ENCLAVE_IRONIC_HTTPS=true) -> sushy-tools -> verify.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

echo "=========================================="
echo "Creating Enclave test environment"
echo "=========================================="

info "Step 1: Validating prerequisites..."
"${ENCLAVE_DIR}/scripts/setup/validate_prerequisites.sh"

info "Step 2: Creating infrastructure (name, networks, pool, VMs) via vm_infra.py..."
# vm_infra.py logs to stderr; its stdout is the cluster-env.sh content, echoed BEFORE any
# libvirt resource is created. Capture it without aborting on failure so the generated
# identity is still recorded (and the cluster can always be torn down) even if a later
# create step fails.
set +e
cluster_env="$(sudo -E python3 "${ENCLAVE_DIR}/scripts/infrastructure/vm_infra.py" create)"
create_rc=$?
set -e
if [ -n "$cluster_env" ]; then
    set -a
    eval "$cluster_env"
    set +a
fi

# Thread the identity to later steps (CI: $GITHUB_ENV from the workflow; local ci-flow:
# a temp file it points GITHUB_ENV at and then sources). Do this even on failure so the
# Cleanup step can tear down a partially-created cluster.
if [ -n "${GITHUB_ENV:-}" ] && [ -n "${ENCLAVE_CLUSTER_NAME:-}" ]; then
    {
        echo "ENCLAVE_CLUSTER_NAME=${ENCLAVE_CLUSTER_NAME}"
        echo "WORKING_DIR=${WORKING_DIR}"
    } >> "$GITHUB_ENV"
fi

if [ "$create_rc" -ne 0 ]; then
    error "vm_infra.py create failed (exit ${create_rc})"
    exit "$create_rc"
fi
info "  Cluster: ${ENCLAVE_CLUSTER_NAME}"
info "  Working dir: ${WORKING_DIR}"

# vm_infra.py runs under sudo and creates WORKING_DIR as root. Hand the directory
# itself back to the invoking user so the (non-root) steps below and later targets
# can write into it (pull secret, generated config/). The pool/ subdir stays
# root-owned for libvirt.
sudo chown "$(id -u):$(id -g)" "${WORKING_DIR}"

info "Step 3: Writing pull secret..."
if [ -n "${PULL_SECRET:-}" ]; then
    printf '%s' "$PULL_SECRET" > "${WORKING_DIR}/pull_secret.json"
    chmod 600 "${WORKING_DIR}/pull_secret.json"
    info "  Pull secret written to ${WORKING_DIR}/pull_secret.json"
else
    info "  PULL_SECRET not set — skipping (must exist at ${WORKING_DIR}/pull_secret.json)"
fi

info "Step 4: Configuring GPU passthrough (if applicable)..."
"${ENCLAVE_DIR}/scripts/infrastructure/configure_gpu_passthrough.sh" || true

info "Step 5: Verifying networks..."
"${ENCLAVE_DIR}/scripts/infrastructure/verify_networks.sh"

if [ "${ENCLAVE_IRONIC_HTTPS:-}" = "true" ]; then
    info "Step 6: Generating Ironic ISO server CA..."
    "${ENCLAVE_DIR}/scripts/deployment/generate_ironic_ca.sh"
else
    info "Step 6: Skipping Ironic ISO server CA (HTTPS not configured)"
fi

info "Step 7: Starting BMC emulation (sushy-tools)..."
"${ENCLAVE_DIR}/scripts/infrastructure/start_sushy_tools.sh"

info "Step 8: Verifying infrastructure..."
"${ENCLAVE_DIR}/scripts/verification/verify_infrastructure.sh"

echo "=========================================="
echo "Environment creation complete!"
echo "=========================================="

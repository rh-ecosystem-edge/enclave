#!/bin/bash
# Clean up storage pools and networks that might conflict
# This runs BEFORE creating new infrastructure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/output.sh"

# Get cluster name for network cleanup
CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-}"

# Get the path we're about to use for pool cleanup
CLUSTER_WORKING_DIR="${WORKING_DIR:-}"

if [ -z "$CLUSTER_WORKING_DIR" ] && [ -z "$CLUSTER_NAME" ]; then
    info "No WORKING_DIR or CLUSTER_NAME set, skipping cleanup"
    exit 0
fi

# Clean up leftover networks first
if [ -n "$CLUSTER_NAME" ]; then
    info "Checking for conflicting networks for cluster: $CLUSTER_NAME..."

    # Network names follow pattern: ${CLUSTER_NAME}-p (provisioning/BMC) and ${CLUSTER_NAME}-e (external/cluster)
    for NETWORK_SUFFIX in "p" "e"; do
        NETWORK_NAME="${CLUSTER_NAME}-${NETWORK_SUFFIX}"

        # Check if network exists
        if sudo virsh net-info "$NETWORK_NAME" >/dev/null 2>&1; then
            info "Found leftover network: $NETWORK_NAME"

            # Destroy if active
            if sudo virsh net-info "$NETWORK_NAME" 2>/dev/null | grep -qE "Active:[[:space:]]+yes$"; then
                info "  Stopping network: $NETWORK_NAME"
                sudo virsh net-destroy "$NETWORK_NAME" 2>/dev/null || true
            fi

            # Undefine
            info "  Removing network: $NETWORK_NAME"
            sudo virsh net-undefine "$NETWORK_NAME" 2>/dev/null || true
        fi
    done
fi

# Clean up storage pools
if [ -z "$CLUSTER_WORKING_DIR" ]; then
    info "No WORKING_DIR set, skipping pool cleanup"
    exit 0
fi

info "Checking for conflicting storage pools..."

# Find any pools pointing to our target path
CONFLICTING_POOLS=$(sudo virsh pool-list --all --name | while read -r pool; do
    if [ -n "$pool" ]; then
        POOL_PATH=$(sudo virsh pool-dumpxml "$pool" 2>/dev/null | grep -oP '(?<=<path>).*(?=</path>)' || echo "")
        if [ "$POOL_PATH" = "${CLUSTER_WORKING_DIR}/pool" ]; then
            echo "$pool"
        fi
    fi
done)

if [ -n "$CONFLICTING_POOLS" ]; then
    info "Found conflicting storage pools, removing them..."
    echo "$CONFLICTING_POOLS" | while read -r pool; do
        if [ -n "$pool" ]; then
            info "  Removing pool: $pool"
            # Destroy if running
            if sudo virsh pool-info "$pool" 2>/dev/null | grep -qE "State:[[:space:]]+running$"; then
                sudo virsh pool-destroy "$pool" 2>/dev/null || true
            fi
            # Undefine
            sudo virsh pool-undefine "$pool" 2>/dev/null || true
        fi
    done
    success "Conflicting pools removed"
else
    info "No conflicting storage pools found"
fi

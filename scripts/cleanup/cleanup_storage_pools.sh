#!/bin/bash
# Clean up storage pools that might conflict
# This runs BEFORE creating new infrastructure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/output.sh"

info "Checking for conflicting storage pools..."

# Get the path we're about to use
CLUSTER_WORKING_DIR="${WORKING_DIR:-}"
if [ -z "$CLUSTER_WORKING_DIR" ]; then
    info "No WORKING_DIR set, skipping pool cleanup"
    exit 0
fi

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

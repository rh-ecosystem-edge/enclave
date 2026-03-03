#!/usr/bin/env bash
# Clean up orphaned dev-scripts resources before creating new environment
# This handles leftover resources from previous runs with different cluster names
#
# WARNING: This script removes hardcoded dev-scripts network names and should
# only be run when you're sure no other dev-scripts processes are using them.
# It is NOT safe for parallel execution and will interfere with other jobs.
#
# Usage: Only run this manually if environment creation fails due to network conflicts.

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() {
    echo -e "${GREEN}INFO:${NC} $1"
}

warning() {
    echo -e "${YELLOW}WARNING:${NC} $1"
}

info "Checking for orphaned dev-scripts resources..."

# Common dev-scripts network names that might conflict
KNOWN_NETWORKS=(
    "baremetal"
    "provisioning"
    "cluster"
    "ostestbm"
    "ostestpr"
)

# Clean up orphaned libvirt networks
for net in "${KNOWN_NETWORKS[@]}"; do
    if sudo virsh net-list --all --name 2>/dev/null | grep -q "^${net}$"; then
        warning "Found orphaned network: $net"
        info "  Destroying network: $net"
        sudo virsh net-destroy "$net" 2>/dev/null || true
        sudo virsh net-undefine "$net" 2>/dev/null || true
    fi
done

# Clean up orphaned bridge interfaces
for bridge in $(ip link show type bridge | grep -o '^[0-9]*: [^:]*' | cut -d' ' -f2 | grep -E '^(baremetal|provisioning|cluster|ostestbm|ostestpr)$' || true); do
    warning "Found orphaned bridge: $bridge"
    info "  Removing bridge: $bridge"
    sudo ip link set "$bridge" down 2>/dev/null || true
    sudo ip link delete "$bridge" 2>/dev/null || true
done

info "✓ Orphaned resource cleanup complete"

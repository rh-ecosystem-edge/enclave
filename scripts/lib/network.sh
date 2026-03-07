#!/bin/bash
# Shared network utilities
#
# Provides network-related helper functions for IP detection, subnet handling,
# and BMC port calculation.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/network.sh"
#   prefix=$(get_network_prefix "192.168.1.0/24")
#   ip=$(get_vm_ip_on_network "vm_name" "192.168.1.0/24")
#   port=$(calculate_bmc_port "192.168.3.0/24")
#
# Functions:
#   get_network_prefix CIDR          - Extract network prefix (e.g., "192.168.1" from "192.168.1.0/24")
#   escape_network_prefix PREFIX     - Escape dots for grep regex (e.g., "192\.168\.1")
#   get_vm_ip_on_network VM CIDR     - Get VM IP address on specific network using virsh
#   calculate_bmc_port CIDR          - Calculate BMC port from subnet ID (subnet 3 -> port 8003)
#   get_network_gateway CIDR         - Get network gateway IP (e.g., "192.168.1.1" from "192.168.1.0/24")

# Extract network prefix from CIDR notation
# Args: $1 = CIDR network (e.g., "192.168.1.0/24")
# Returns: Network prefix (e.g., "192.168.1")
get_network_prefix() {
    local cidr="$1"
    echo "$cidr" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3}'
}

# Escape network prefix for use in grep regex
# Args: $1 = Network prefix (e.g., "192.168.1")
# Returns: Escaped prefix (e.g., "192\.168\.1")
escape_network_prefix() {
    local prefix="$1"
    echo "$prefix" | sed 's/\./\\./g'
}

# Get VM IP address on a specific network using virsh
# Args: $1 = VM name, $2 = CIDR network
# Returns: IP address or empty string if not found
get_vm_ip_on_network() {
    local vm_name="$1"
    local network_cidr="$2"

    local prefix
    prefix=$(get_network_prefix "$network_cidr")

    local escaped_prefix
    escaped_prefix=$(escape_network_prefix "$prefix")

    sudo virsh domifaddr "$vm_name" 2>/dev/null | \
        grep -E "${escaped_prefix}\." | \
        awk '{print $4}' | \
        cut -d'/' -f1 | \
        head -1 || true
}

# Calculate BMC port from provisioning network subnet ID
# Uses subnet ID as port offset: subnet 3 -> port 8003, subnet 10 -> port 8010
# Args: $1 = Provisioning network CIDR (e.g., "192.168.3.0/24")
# Returns: BMC port number (e.g., "8003")
calculate_bmc_port() {
    local network_cidr="$1"
    local subnet_id
    subnet_id=$(echo "$network_cidr" | awk -F. '{print $3}')
    echo "$((8000 + subnet_id))"
}

# Get network gateway IP from CIDR notation
# Args: $1 = CIDR network (e.g., "192.168.1.0/24")
# Returns: Gateway IP (e.g., "192.168.1.1")
get_network_gateway() {
    local cidr="$1"
    echo "$cidr" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".1"}'
}

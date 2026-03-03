#!/usr/bin/env bash
# Get Landing Zone VM IP address with dynamic subnet detection
#
# This script attempts to find the Landing Zone IP using multiple methods:
# 1. Read from environment.json (cluster network first, then BMC network)
# 2. Query virsh with dynamic subnet detection
#
# Usage:
#   ./get_landing_zone_ip.sh [environment_file]
#
# Environment variables:
#   ENCLAVE_CLUSTER_NAME - Cluster name (default: enclave-test)
#   WORKING_DIR - Working directory (default: /opt/dev-scripts)

set -euo pipefail

# Determine environment file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
WORKING_DIR="${WORKING_DIR:-/opt/dev-scripts}"

if [ $# -gt 0 ]; then
    ENV_FILE="$1"
else
    ENV_FILE="${WORKING_DIR}/environment-${ENCLAVE_CLUSTER_NAME}.json"
fi

# Try to get IP from environment file (cluster network first, then BMC)
LZ_IP=$(jq -r '.vms.landing_zone.networks.cluster.ip // .vms.landing_zone.networks.bmc.ip // empty' "$ENV_FILE" 2>/dev/null | grep -v "unknown" || true)

# If not found in env file, try virsh with dynamic subnet detection
if [ -z "$LZ_IP" ]; then
    # Get VM name from env file or construct default
    LZ_VM_NAME=$(jq -r '.vms.landing_zone.name // empty' "$ENV_FILE" 2>/dev/null || echo "${ENCLAVE_CLUSTER_NAME}_landingzone_0")

    # Extract cluster network from environment file for dynamic detection
    CLUSTER_NETWORK=$(jq -r '.networks.cluster.cidr // empty' "$ENV_FILE" 2>/dev/null || echo "192.168.2.0/24")

    # Extract network prefix (e.g., "192.168.4" from "192.168.4.0/24")
    CLUSTER_NET_PREFIX=$(echo "$CLUSTER_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3}')

    # Escape dots for grep regex
    ESCAPED_PREFIX=$(echo "$CLUSTER_NET_PREFIX" | sed 's/\./\\./g')

    # Query virsh for VM IP on the cluster network
    LZ_IP=$(sudo virsh domifaddr "$LZ_VM_NAME" 2>/dev/null | grep -E "${ESCAPED_PREFIX}\." | awk '{print $4}' | cut -d'/' -f1 | head -1 || true)
fi

# Output the IP (empty string if not found)
echo "$LZ_IP"

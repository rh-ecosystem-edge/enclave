#!/bin/bash
# Configure dev-scripts environment for Enclave Lab
#
# This script exports all required environment variables for dev-scripts
# to create the Enclave test infrastructure.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

# Validate required environment variables
require_env_var "DEV_SCRIPTS_PATH"

# Cluster name (must be set before generating networks)
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Generate unique IP subnets for parallel execution isolation
# Use atomic subnet allocation to avoid conflicts between parallel jobs
if [ -z "${ENCLAVE_BMC_NETWORK:-}" ] || [ -z "${ENCLAVE_CLUSTER_NETWORK:-}" ]; then
    # Get script directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Allocate unique subnet atomically (uses file locking)
    echo "Allocating unique subnet for cluster ${ENCLAVE_CLUSTER_NAME}..."

    # Export variables for subprocess
    export ENCLAVE_CLUSTER_NAME
    # Use BASE_WORKING_DIR for shared allocation file across all clusters
    # This is where the subnet-allocations.json file is stored (shared across all clusters)
    ALLOCATION_BASE_DIR="${BASE_WORKING_DIR:-${WORKING_DIR:-}}"
    if [ -z "$ALLOCATION_BASE_DIR" ]; then
        echo "ERROR: WORKING_DIR or BASE_WORKING_DIR environment variable is required"
        exit 1
    fi
    # DO NOT overwrite WORKING_DIR - it should already be cluster-specific!
    # Pass ALLOCATION_BASE_DIR to allocate_subnet.sh for the shared lock file
    export ALLOCATION_BASE_DIR

    # Get subnet allocation with network configuration
    # Script outputs environment variable assignments
    ALLOCATION_OUTPUT=$("${SCRIPT_DIR}/allocate_subnet.sh" allocate)

    if [ -z "$ALLOCATION_OUTPUT" ]; then
        echo "ERROR: Failed to allocate subnet"
        exit 1
    fi

    # Source the environment variables from the script output
    eval "$ALLOCATION_OUTPUT"

    echo "✓ Allocated unique subnets for cluster ${ENCLAVE_CLUSTER_NAME}:"
    echo "  Subnet ID: ${ENCLAVE_SUBNET_ID}"
    echo "  BMC Network: ${ENCLAVE_BMC_NETWORK}"
    echo "  Cluster Network: ${ENCLAVE_CLUSTER_NETWORK}"
else
    echo "Using pre-configured networks:"
    echo "  BMC Network: ${ENCLAVE_BMC_NETWORK}"
    echo "  Cluster Network: ${ENCLAVE_CLUSTER_NETWORK}"
fi

# Calculate BMC port from subnet ID
BMC_PORT="$((8000 + ENCLAVE_SUBNET_ID))"

# Use unique config file per cluster for parallel execution safety
CONFIG_FILE="${DEV_SCRIPTS_PATH}/config_${ENCLAVE_CLUSTER_NAME}.sh"

echo "Creating dev-scripts configuration at: $CONFIG_FILE"
ENCLAVE_NUM_MASTERS="${ENCLAVE_NUM_MASTERS:-3}"
ENCLAVE_NUM_LANDINGZONE="${ENCLAVE_NUM_LANDINGZONE:-1}"

# Create configuration file
cat > "$CONFIG_FILE" <<EOF
#!/bin/bash
# Enclave Lab - dev-scripts configuration
#
# This configuration creates:
# - ${ENCLAVE_NUM_MASTERS} master VMs (for OpenShift cluster nodes)
# - ${ENCLAVE_NUM_LANDINGZONE} Landing Zone VM (deployment host)
# - 2 networks: BMC (${ENCLAVE_BMC_NETWORK}) and Cluster (${ENCLAVE_CLUSTER_NETWORK})
# - BMC emulation (sushy-tools + virtualbmc)

# =============================================================================
# VM Configuration
# =============================================================================

# Create master VMs for OpenShift cluster (will become control plane nodes)
export NUM_MASTERS=${ENCLAVE_NUM_MASTERS}

# No worker VMs (using compact cluster - masters run workloads)
export NUM_WORKERS=0

# Create Landing Zone VM (deployment host with dual-network access)
export NUM_LANDINGZONE=${ENCLAVE_NUM_LANDINGZONE}

# No arbiters or extra workers
export NUM_ARBITERS=0
export NUM_EXTRA_WORKERS=0

# =============================================================================
# Master VM Specs
# =============================================================================

# Master VMs need resources for OpenShift control plane nodes
export MASTER_MEMORY=24576    # 24 GB RAM
export MASTER_DISK=120        # 120 GB disk
export MASTER_VCPU=12         # 12 vCPUs

# Extra disks for storage (used by LVMS for PersistentVolumes)
export VM_EXTRADISKS=true
export VM_EXTRADISKS_LIST="vdb"
export VM_EXTRADISKS_SIZE="60G"

# =============================================================================
# Landing Zone VM Specs
# =============================================================================

# Landing Zone VM runs Enclave Lab and deployment tools
export LANDINGZONE_MEMORY=8192    # 8 GB RAM
export LANDINGZONE_DISK=60        # 60 GB disk
export LANDINGZONE_VCPU=4         # 4 vCPUs

# =============================================================================
# Network Configuration
# =============================================================================

# BMC Network (provisioning network in dev-scripts terminology)
# - Used for Redfish/IPMI endpoints
# - sushy-tools listens on first IP of this network on port 8000
# - Landing Zone VM gets interface on this network
# - Using suffix '-p' for provisioning (15 chars max for bridge names)
export PROVISIONING_NETWORK="${ENCLAVE_BMC_NETWORK}"
export PROVISIONING_NETWORK_NAME="${ENCLAVE_CLUSTER_NAME}-p"

# Cluster Network (baremetal network in dev-scripts terminology)
# - Used for OpenShift cluster traffic
# - Master VMs get interface on this network
# - Landing Zone VM also gets interface on this network
# - Using suffix '-e' for external/cluster network (15 chars max for bridge names)
export EXTERNAL_SUBNET_V4="${ENCLAVE_CLUSTER_NETWORK}"
export BAREMETAL_NETWORK_NAME="${ENCLAVE_CLUSTER_NAME}-e"

# Network mode - use bridge mode for both networks
# MANAGE_BR_BRIDGE controls if libvirt manages the cluster network
# Set to "y" to let libvirt manage it (avoids NetworkManager conflicts)
export MANAGE_BR_BRIDGE="y"
export MANAGE_PRO_BRIDGE="y"
export MANAGE_INT_BRIDGE="n"

# =============================================================================
# Cluster Configuration
# =============================================================================

# Cluster name (used for VM naming prefix)
export CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME}"

# Cluster domain (for DNS)
export CLUSTER_DOMAIN="${ENCLAVE_CLUSTER_NAME}.lab"

# Working directory (where VMs and configs are stored)
# Cluster-specific path for parallel execution isolation
export WORKING_DIR="${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}"

# Storage pool configuration - use cluster name for isolation in parallel execution
# LIBVIRT_VOLUME_POOL controls which pool VMs reference for disk images
# This prevents VMs from looking for "oooq_pool" and ensures they use the cluster-specific pool
export LIBVIRT_VOLUME_POOL="${ENCLAVE_CLUSTER_NAME}"

# =============================================================================
# BMC Configuration
# =============================================================================

# BMC port range for IPMI emulation
export VBMC_BASE_PORT=6230

# Redfish emulator (sushy-tools) configuration
export SUSHY_TOOLS_IMAGE="quay.io/metal3-io/sushy-tools:latest"

# Ignore boot device in Redfish emulator (for testing)
export REDFISH_EMULATOR_IGNORE_BOOT_DEVICE="False"

# =============================================================================
# Additional Options
# =============================================================================

# IP version (IPv4)
export IP_STACK="v4"

# Don't manage baremetal bridge via libvirt (we manage with NetworkManager)
# This was already set above, but keeping for clarity
# export MANAGE_BR_BRIDGE="y"

# Disable proxy (not needed for local testing)
export INSTALLER_PROXY=""

# Use local registry for caching images (optional)
export MIRROR_IMAGES="false"

# Libvirt firmware (UEFI)
export LIBVIRT_FIRMWARE="uefi"

# =============================================================================
# Environment Info
# =============================================================================

echo "dev-scripts configuration loaded for Enclave Lab:"
echo "  Cluster Name: \$CLUSTER_NAME"
echo "  Working Dir: \$WORKING_DIR"
echo "  VMs: \$NUM_MASTERS masters + \$NUM_LANDINGZONE Landing Zone"
echo "  Networks: \$PROVISIONING_NETWORK_NAME (\$PROVISIONING_NETWORK), \$BAREMETAL_NETWORK_NAME (\$EXTERNAL_SUBNET_V4)"
echo "  BMC Emulation: sushy-tools on first IP of BMC network, port ${BMC_PORT}"
EOF

# Make configuration file executable
chmod +x "$CONFIG_FILE"

echo "✓ Configuration file created: $CONFIG_FILE"
echo ""
echo "Configuration summary:"
echo "  - VMs: ${ENCLAVE_NUM_MASTERS} masters + ${ENCLAVE_NUM_LANDINGZONE} Landing Zone"
echo "  - Networks: bmc (${ENCLAVE_BMC_NETWORK}), cluster (${ENCLAVE_CLUSTER_NETWORK})"
echo "  - Working directory: /opt/dev-scripts"
echo "  - Cluster name: ${ENCLAVE_CLUSTER_NAME}"
echo ""

# Source the configuration to export variables for the current session
echo "Exporting configuration to current shell..."
# shellcheck source=/dev/null
source "$CONFIG_FILE"

echo "✓ Configuration exported and ready for dev-scripts"

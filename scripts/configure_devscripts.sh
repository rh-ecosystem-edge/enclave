#!/bin/bash
# Configure dev-scripts environment for Enclave Lab
#
# This script exports all required environment variables for dev-scripts
# to create the Enclave test infrastructure.

set -euo pipefail

# Configuration file location
if [ -z "$DEV_SCRIPTS_PATH" ]; then
    echo "ERROR: DEV_SCRIPTS_PATH environment variable is not set"
    echo ""
    echo "Please set DEV_SCRIPTS_PATH before running this script:"
    echo "  export DEV_SCRIPTS_PATH=/path/to/dev-scripts"
    exit 1
fi

# Cluster name (must be set before generating networks)
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Generate unique IP subnets for parallel execution isolation
# Use atomic subnet allocation to avoid conflicts between parallel jobs
if [ -z "${ENCLAVE_BMC_NETWORK:-}" ] || [ -z "${ENCLAVE_CLUSTER_NETWORK:-}" ]; then
    # Get script directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Allocate unique subnet ID atomically (uses file locking)
    echo "Allocating unique subnet for cluster ${ENCLAVE_CLUSTER_NAME}..."

    # Export variables for subprocess
    export ENCLAVE_CLUSTER_NAME
    export WORKING_DIR="${WORKING_DIR:-/opt/dev-scripts}"

    SUBNET_ID=$("${SCRIPT_DIR}/allocate_subnet.sh" allocate)

    if [ -z "$SUBNET_ID" ]; then
        echo "ERROR: Failed to allocate subnet"
        exit 1
    fi

    # Generate network configuration with allocated subnet
    ENCLAVE_BMC_NETWORK="${ENCLAVE_BMC_NETWORK:-100.64.${SUBNET_ID}.0/24}"
    ENCLAVE_CLUSTER_NETWORK="${ENCLAVE_CLUSTER_NETWORK:-192.168.${SUBNET_ID}.0/24}"

    echo "✓ Allocated unique subnets for cluster ${ENCLAVE_CLUSTER_NAME}:"
    echo "  Subnet ID: ${SUBNET_ID}"
    echo "  BMC Network: ${ENCLAVE_BMC_NETWORK}"
    echo "  Cluster Network: ${ENCLAVE_CLUSTER_NETWORK}"
else
    echo "Using pre-configured networks:"
    echo "  BMC Network: ${ENCLAVE_BMC_NETWORK}"
    echo "  Cluster Network: ${ENCLAVE_CLUSTER_NETWORK}"
fi

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
export VM_EXTRADISKS_SIZE="120G"

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

# Base domain (so dev-scripts uses .lab not test.metalkube.org for CLUSTER_DOMAIN)
export BASE_DOMAIN="lab"

# Working directory (where VMs and configs are stored)
export WORKING_DIR="/opt/dev-scripts"

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
echo "  BMC Emulation: sushy-tools on first IP of BMC network, port 8000"
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

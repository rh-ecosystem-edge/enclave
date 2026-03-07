#!/bin/bash
# Generate environment metadata JSON
#
# This script collects information about the created infrastructure
# and saves it to environment.json for use by other tools.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/common.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"

# Ensure working directory is set
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
ensure_working_dir

# Try to load dev-scripts config (non-fatal)
try_load_devscripts_config

CLUSTER_NAME="${CLUSTER_NAME:-$ENCLAVE_CLUSTER_NAME}"
OUTPUT_FILE="${WORKING_DIR}/environment-${CLUSTER_NAME}.json"
# Also create a symlink for backwards compatibility
SYMLINK_FILE="${WORKING_DIR}/environment.json"

# Get network info from config or defaults
PROVISIONING_NETWORK="${PROVISIONING_NETWORK:-100.64.1.0/24}"
EXTERNAL_SUBNET_V4="${EXTERNAL_SUBNET_V4:-192.168.2.0/24}"

# Calculate gateway IPs and port
BMC_GATEWAY=$(get_network_gateway "$PROVISIONING_NETWORK")
BMC_PORT=$(calculate_bmc_port "$PROVISIONING_NETWORK")
BMC_ENDPOINT="https://${BMC_GATEWAY}:${BMC_PORT}"

echo "=========================================="
echo "Generating environment metadata..."
echo "=========================================="
echo "Debug information:"
echo "  WORKING_DIR (env): ${WORKING_DIR}"
echo "  ENCLAVE_CLUSTER_NAME (env): ${ENCLAVE_CLUSTER_NAME}"
echo "  Cluster Name (computed): ${CLUSTER_NAME}"
echo "  Output File: ${OUTPUT_FILE}"
echo "  Directory exists: $([ -d "$WORKING_DIR" ] && echo 'YES' || echo 'NO')"
echo "  Directory writable: $([ -w "$WORKING_DIR" ] && echo 'YES' || echo 'NO')"
echo ""

# Helper function to get VM IP address
get_vm_ip() {
    local vm_name=$1
    local network_type=$2  # "cluster" or "bmc"

    # Map network type to actual network name
    local network_name
    if [ "$network_type" = "cluster" ]; then
        network_name="${CLUSTER_NAME}-e"  # external/cluster network
    elif [ "$network_type" = "bmc" ]; then
        network_name="${CLUSTER_NAME}-p"  # provisioning/BMC network
    else
        echo "unknown"
        return
    fi

    # Try to get IP from DHCP leases
    sudo virsh net-dhcp-leases "$network_name" 2>/dev/null | \
        grep "$vm_name" | \
        awk '{print $5}' | \
        cut -d'/' -f1 || echo "unknown"
}

# Helper function to get VM MAC address
get_vm_mac() {
    local vm_name=$1
    local network_type=$2  # "cluster" or "bmc"

    # Map network type to actual bridge name
    local bridge_name
    if [ "$network_type" = "cluster" ]; then
        bridge_name="${CLUSTER_NAME}-e"  # external/cluster network
    elif [ "$network_type" = "bmc" ]; then
        bridge_name="${CLUSTER_NAME}-p"  # provisioning/BMC network
    else
        echo "unknown"
        return
    fi

    sudo virsh dumpxml "$vm_name" 2>/dev/null | \
        grep -B 1 "bridge='${bridge_name}'" | \
        grep "mac address" | \
        sed "s/.*address='\([^']*\)'.*/\1/" || echo "unknown"
}

# Helper function to get Redfish endpoint for a VM
get_redfish_endpoint() {
    local vm_mac=$1
    echo "${BMC_ENDPOINT}/redfish/v1/Systems/${vm_mac}"
}

# Start building JSON
cat > "$OUTPUT_FILE" <<EOF
{
  "metadata": {
    "generated_at": "$(date -Iseconds)",
    "cluster_name": "${CLUSTER_NAME}",
    "working_dir": "${WORKING_DIR}"
  },
  "networks": {
    "bmc": {
      "name": "bmc",
      "cidr": "${PROVISIONING_NETWORK}",
      "gateway": "${BMC_GATEWAY}",
      "purpose": "Out-of-band management (BMC/Redfish)",
      "isolated": true
    },
    "cluster": {
      "name": "cluster",
      "cidr": "${EXTERNAL_SUBNET_V4}",
      "purpose": "OpenShift cluster traffic",
      "isolated": false
    }
  },
  "bmc_emulation": {
    "sushy_tools": {
      "endpoint": "${BMC_ENDPOINT}",
      "redfish_api": "${BMC_ENDPOINT}/redfish/v1",
      "systems_endpoint": "${BMC_ENDPOINT}/redfish/v1/Systems",
      "running": $(systemctl is-active sushy-emulator >/dev/null 2>&1 && echo "true" || echo "false")
    },
    "virtualbmc": {
      "port_range": "${VBMC_BASE_PORT:-6230}-$((${VBMC_BASE_PORT:-6230} + 5))",
      "base_port": ${VBMC_BASE_PORT:-6230}
    }
  },
  "vms": {
    "masters": [
EOF

# Verify file was created
if [ ! -f "$OUTPUT_FILE" ]; then
    echo "ERROR: Failed to create environment file: $OUTPUT_FILE"
    echo "Check that WORKING_DIR exists and is writable: ${WORKING_DIR}"
    exit 1
fi

# Add master VMs
MASTER_COUNT=3
for i in $(seq 0 $((MASTER_COUNT - 1))); do
    VM_NAME="${CLUSTER_NAME}_master_${i}"

    # Get MAC address for cluster network
    CLUSTER_MAC=$(get_vm_mac "$VM_NAME" "cluster")

    # Get IP if available
    CLUSTER_IP=$(get_vm_ip "$VM_NAME" "cluster")

    # Check if VM is running
    if sudo virsh list --all | grep -q "$VM_NAME.*running"; then
        VM_STATE="running"
    elif sudo virsh list --all | grep -q "$VM_NAME.*shut off"; then
        VM_STATE="shut off"
    else
        VM_STATE="unknown"
    fi

    # Get Redfish endpoint (based on cluster MAC since masters don't have BMC network)
    # Note: In real setup, masters are managed via BMC network from host
    REDFISH_ENDPOINT="${BMC_ENDPOINT}/redfish/v1/Systems/${CLUSTER_MAC}"

    cat >> "$OUTPUT_FILE" <<EOF
      {
        "name": "${VM_NAME}",
        "role": "master",
        "state": "${VM_STATE}",
        "specs": {
          "memory_mb": 16384,
          "vcpus": 8,
          "disk_gb": 120
        },
        "networks": {
          "cluster": {
            "mac": "${CLUSTER_MAC}",
            "ip": "${CLUSTER_IP}"
          }
        },
        "bmc": {
          "redfish_endpoint": "${REDFISH_ENDPOINT}",
          "accessible_from": ["host", "landing_zone"]
        }
      }$([ $i -lt $((MASTER_COUNT - 1)) ] && echo "," || echo "")
EOF
done

cat >> "$OUTPUT_FILE" <<EOF
    ],
    "landing_zone": {
EOF

# Add Landing Zone VM
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"
LZ_BMC_MAC=$(get_vm_mac "$LZ_VM_NAME" "bmc")
LZ_CLUSTER_MAC=$(get_vm_mac "$LZ_VM_NAME" "cluster")
# Calculate Landing Zone BMC IP (typically .2 in the BMC network)
LZ_BMC_IP=$(echo "$PROVISIONING_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".2"}')
LZ_CLUSTER_IP=$(get_vm_ip "$LZ_VM_NAME" "cluster")

if sudo virsh list --all | grep -q "$LZ_VM_NAME.*running"; then
    LZ_STATE="running"
elif sudo virsh list --all | grep -q "$LZ_VM_NAME.*shut off"; then
    LZ_STATE="shut off"
else
    LZ_STATE="unknown"
fi

cat >> "$OUTPUT_FILE" <<EOF
      "name": "${LZ_VM_NAME}",
      "role": "landing_zone",
      "state": "${LZ_STATE}",
      "specs": {
        "memory_mb": 8192,
        "vcpus": 4,
        "disk_gb": 60
      },
      "networks": {
        "bmc": {
          "interface": "eth0",
          "mac": "${LZ_BMC_MAC}",
          "ip": "${LZ_BMC_IP}",
          "purpose": "Access BMC emulation and manage cluster VMs"
        },
        "cluster": {
          "interface": "eth1",
          "mac": "${LZ_CLUSTER_MAC}",
          "ip": "${LZ_CLUSTER_IP}",
          "purpose": "Deploy and access OpenShift cluster"
        }
      },
      "access": {
        "ssh": "ssh cloud-user@${LZ_BMC_IP}",
        "console": "virsh console ${LZ_VM_NAME}",
        "note": "OS installation required before SSH access"
      }
    }
  },
  "useful_commands": {
    "list_vms": "sudo virsh list --all",
    "list_networks": "sudo virsh net-list --all",
    "check_bmc": "curl ${BMC_ENDPOINT}/redfish/v1/Systems",
    "ssh_to_landingzone": "ssh cloud-user@${LZ_BMC_IP}",
    "vm_console": "sudo virsh console ${CLUSTER_NAME}_<vm_name>",
    "cleanup": "make clean"
  }
}
EOF

echo "✓ Environment metadata saved to: $OUTPUT_FILE"

# Verify file was created and has content
if [ ! -f "$OUTPUT_FILE" ]; then
    echo "ERROR: Output file does not exist after write: $OUTPUT_FILE"
    echo "This indicates a write permission or disk space issue"
    ls -la "$WORKING_DIR" || true
    exit 1
fi

FILE_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null || echo "0")
if [ "$FILE_SIZE" -lt 100 ]; then
    echo "ERROR: Output file exists but appears empty or corrupted: $OUTPUT_FILE (size: $FILE_SIZE bytes)"
    cat "$OUTPUT_FILE" || true
    exit 1
fi

echo "✓ Environment file verified: $(ls -lh "$OUTPUT_FILE")"

# Create symlink so environment.json always points at current cluster (CI and default name)
if [ -n "${SYMLINK_FILE:-}" ]; then
    ln -sf "$(basename "$OUTPUT_FILE")" "$SYMLINK_FILE" 2>/dev/null || true
fi

echo ""
echo "Quick summary:"
echo "  - Cluster: ${CLUSTER_NAME}"
echo "  - Masters: $MASTER_COUNT VMs"
echo "  - Landing Zone: 1 VM (${LZ_STATE})"
echo "  - BMC endpoint: ${BMC_ENDPOINT}"
echo "  - Landing Zone SSH: ssh cloud-user@${LZ_BMC_IP} (after OS installation)"
echo ""
echo "View full details: cat $OUTPUT_FILE | jq"

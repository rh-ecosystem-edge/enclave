#!/bin/bash
# Configure GPU passthrough for master_0 VM (if NVIDIA GPU is available)
#
# This script auto-detects an NVIDIA GPU on the host and attaches it
# to the first master VM via PCI passthrough. It is designed to be
# called as part of `make environment` and exits silently (exit 0)
# when no GPU or no IOMMU is available.
#
# Prerequisites (manual, one-time on GPU host):
#   1. BIOS: VT-d (Intel) or AMD-Vi must be enabled
#   2. Kernel: sudo grubby --update-kernel=ALL --args="intel_iommu=on" && sudo reboot
#
# Usage:
#   ./scripts/infrastructure/configure_gpu_passthrough.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

load_devscripts_config

if [ -z "${CLUSTER_NAME:-}" ]; then
    error "CLUSTER_NAME not set after loading config"
    exit 1
fi

# Auto-detect NVIDIA GPU (lspci -D gives full domain:bus:slot.func format)
GPU_PCI_ADDRESS=$(lspci -D -nn | grep -i nvidia | grep -E '3D controller|VGA compatible' | head -1 | awk '{print $1}') || true

if [ -z "$GPU_PCI_ADDRESS" ]; then
    info "No NVIDIA GPU detected on host, skipping GPU passthrough"
    exit 0
fi

info "Detected NVIDIA GPU at PCI address: $GPU_PCI_ADDRESS"

# Check IOMMU
if [ ! -d /sys/kernel/iommu_groups ] || [ -z "$(ls /sys/kernel/iommu_groups/ 2>/dev/null)" ]; then
    warning "IOMMU not enabled — cannot do GPU passthrough"
    warning "To enable: sudo grubby --update-kernel=ALL --args='intel_iommu=on' && reboot"
    exit 0
fi

# Warn if GPU shares its IOMMU group with other devices
IOMMU_GROUP=$(basename "$(readlink "/sys/bus/pci/devices/${GPU_PCI_ADDRESS}/iommu_group")") || true
if [ -n "$IOMMU_GROUP" ]; then
    GROUP_DEVICES=$(find "/sys/kernel/iommu_groups/${IOMMU_GROUP}/devices/" -maxdepth 1 -mindepth 1 | wc -l)
    if [ "$GROUP_DEVICES" -gt 1 ]; then
        warning "GPU shares IOMMU group $IOMMU_GROUP with $((GROUP_DEVICES-1)) other device(s)"
    fi
fi

# Ensure vfio-pci module is loaded
sudo modprobe vfio-pci

# Parse "0000:41:00.0" into domain, bus, slot, function
DOMAIN=$(echo "$GPU_PCI_ADDRESS" | cut -d: -f1)
BUS=$(echo "$GPU_PCI_ADDRESS" | cut -d: -f2)
SLOT=$(echo "$GPU_PCI_ADDRESS" | cut -d: -f3 | cut -d. -f1)
FUNC=$(echo "$GPU_PCI_ADDRESS" | cut -d: -f3 | cut -d. -f2)

VM_NAME="${CLUSTER_NAME}_master_0"

# Check if GPU is already attached (idempotency)
if sudo virsh dumpxml "$VM_NAME" 2>/dev/null | grep -q "bus='0x${BUS}'.*slot='0x${SLOT}'.*function='0x${FUNC}'"; then
    info "GPU already attached to $VM_NAME, skipping"
    exit 0
fi

info "Attaching GPU to VM: $VM_NAME"

# Use managed='yes' so libvirt handles vfio-pci bind/unbind on VM start/stop
sudo virsh attach-device "$VM_NAME" /dev/stdin --config <<EOF
<hostdev mode='subsystem' type='pci' managed='yes'>
  <source>
    <address domain='0x${DOMAIN}' bus='0x${BUS}' slot='0x${SLOT}' function='0x${FUNC}'/>
  </source>
</hostdev>
EOF

# Verify attachment with specific PCI address match
if sudo virsh dumpxml "$VM_NAME" | grep -q "bus='0x${BUS}'.*slot='0x${SLOT}'.*function='0x${FUNC}'"; then
    success "GPU passthrough configured for $VM_NAME"
else
    error "Failed to attach GPU to $VM_NAME"
    exit 1
fi

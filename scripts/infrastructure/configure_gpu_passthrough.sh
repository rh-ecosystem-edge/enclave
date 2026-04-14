#!/bin/bash
# Configure GPU passthrough for master_0 VM (if NVIDIA GPU is available)
#
# This script auto-detects an NVIDIA GPU on the host and attaches it
# to the first master VM via PCI passthrough. It is designed to be
# called as part of `make environment` and exits silently (exit 0)
# when ENCLAVE_ENABLE_GPU_PASSTHROUGH is not set to "true", no GPU
# is detected, or no IOMMU is available.
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

# GPU passthrough is opt-in via ENCLAVE_ENABLE_GPU_PASSTHROUGH=true.
# When disabled (default), the script exits early and no GPU is attached.
# Set this env var in CI workflow env blocks or export it before running
# `make environment` to enable GPU passthrough for a specific job.
if [ "${ENCLAVE_ENABLE_GPU_PASSTHROUGH:-false}" != "true" ]; then
    info "GPU passthrough not enabled (set ENCLAVE_ENABLE_GPU_PASSTHROUGH=true to enable)"
    exit 0
fi

info "GPU passthrough enabled (ENCLAVE_ENABLE_GPU_PASSTHROUGH=true)"

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

# Check if the GPU is already assigned to another VM (running or defined).
# A physical GPU can only be passed through to one VM at a time, so when
# multiple OCP deployments share the same host, only the first one gets it.
# This prevents subsequent cluster deployment failures but only one deployment
# will hold the GPU.
# We check all VMs (not just running) because the GPU is attached via --config
# before the VM starts, so parallel jobs would both see it as available.
mapfile -t _all_vms < <(sudo virsh list --all --name)
GPU_IN_USE_BY=
for vm in "${_all_vms[@]}"; do
    [ -z "$vm" ] && continue
    if sudo virsh dumpxml "$vm" 2>/dev/null | grep -q "bus='0x${BUS}'.*slot='0x${SLOT}'.*function='0x${FUNC}'"; then
        GPU_IN_USE_BY="$vm"
        break
    fi
done

if [ -n "$GPU_IN_USE_BY" ]; then
    warning "GPU $GPU_PCI_ADDRESS is already in use by VM '$GPU_IN_USE_BY', skipping passthrough"
    exit 0
fi

VM_NAME="${CLUSTER_NAME}_master_0"

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

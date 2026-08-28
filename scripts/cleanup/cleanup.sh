#!/usr/bin/env bash
# Cleanup script for Enclave Lab infrastructure

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

info "=========================================="
info "Cleaning up infrastructure for: ${CLUSTER_NAME}"
info "=========================================="

# Reconstruct WORKING_DIR if not set
if [ -z "${WORKING_DIR:-}" ]; then
    if [ -n "${BASE_WORKING_DIR:-}" ]; then
        export WORKING_DIR="${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}"
        info "Reconstructed WORKING_DIR: ${WORKING_DIR}"
    else
        error "Neither WORKING_DIR nor BASE_WORKING_DIR is set"
        exit 1
    fi
fi

# ─── VM infrastructure teardown via vm_infra.py ───────────────────────────────
info "Destroying VM infrastructure (VMs, networks, pool)..."
VM_INFRA="${ENCLAVE_DIR}/scripts/infrastructure/vm_infra.py"

_DESTROY_OK=false
if [ -f "${VM_INFRA}" ]; then
    if ENCLAVE_CLUSTER_NAME="${CLUSTER_NAME}" \
       ENCLAVE_BMC_NETWORK="${ENCLAVE_BMC_NETWORK:-100.64.1.0/24}" \
       ENCLAVE_CLUSTER_NETWORK="${ENCLAVE_CLUSTER_NETWORK:-192.168.1.0/24}" \
       ENCLAVE_DEPLOYMENT_MODE="${ENCLAVE_DEPLOYMENT_MODE:-disconnected}" \
       ENCLAVE_NUM_MASTERS="${ENCLAVE_NUM_MASTERS:-3}" \
       WORKING_DIR="${WORKING_DIR}" \
         sudo -E python3 "${VM_INFRA}" destroy; then
        _DESTROY_OK=true
    else
        warning "vm_infra.py destroy reported errors — running manual virsh fallback"
    fi
else
    warning "vm_infra.py not found — falling back to manual virsh teardown"
fi

if [ "${_DESTROY_OK}" = "false" ]; then
    # Force-destroy any leftover VMs
    for dom in $(sudo virsh list --all --name 2>/dev/null | grep "^${CLUSTER_NAME}_" || true); do
        [ -z "$dom" ] && continue
        info "  Destroying: $dom"
        sudo virsh destroy "$dom" 2>/dev/null || true
        sudo virsh undefine "$dom" --nvram --remove-all-storage 2>/dev/null \
            || sudo virsh undefine "$dom" --remove-all-storage 2>/dev/null \
            || warning "  Failed to undefine $dom"
    done
    # Destroy leftover networks
    for net in $(sudo virsh net-list --all --name 2>/dev/null | grep "^${CLUSTER_NAME}-" || true); do
        [ -z "$net" ] && continue
        sudo virsh net-destroy "$net" 2>/dev/null || true
        sudo virsh net-undefine "$net" 2>/dev/null || true
        info "  Removed network: $net"
    done
fi

# ─── sushy-tools container ────────────────────────────────────────────────────
SUSHY_CONTAINER="sushy-tools-${CLUSTER_NAME}"
if sudo podman ps -a --format '{{.Names}}' | grep -q "^${SUSHY_CONTAINER}$"; then
    info "Stopping sushy-tools container: ${SUSHY_CONTAINER}"
    sudo podman stop "$SUSHY_CONTAINER" 2>/dev/null || warning "Failed to stop $SUSHY_CONTAINER"
    sudo podman rm "$SUSHY_CONTAINER" 2>/dev/null || warning "Failed to remove $SUSHY_CONTAINER"
else
    info "No sushy-tools container for cluster ${CLUSTER_NAME}"
fi

# ─── Firewall rules ───────────────────────────────────────────────────────────
if sudo firewall-cmd --state >/dev/null 2>&1; then
    if [ -n "${ENCLAVE_BMC_NETWORK:-}" ]; then
        SUBNET_ID=$(echo "$ENCLAVE_BMC_NETWORK" | awk -F. '{print $3}')
        BMC_PORT="$((8000 + SUBNET_ID))"
        info "Removing firewall port ${BMC_PORT}/tcp"
        for zone in $(sudo firewall-cmd --get-active-zones | grep -v "^\s" | grep -v "^$"); do
            sudo firewall-cmd --zone="$zone" --remove-port="${BMC_PORT}/tcp" 2>/dev/null || true
            sudo firewall-cmd --zone="$zone" --remove-port="${BMC_PORT}/tcp" --permanent 2>/dev/null || true
        done
    fi
fi

# ─── Orphaned bridge interfaces ───────────────────────────────────────────────
info "Cleaning up orphaned bridge interfaces for: ${CLUSTER_NAME}..."
for bridge in $(ip link show type bridge 2>/dev/null | grep -oE "${CLUSTER_NAME}-[a-z]" || true); do
    info "  Removing bridge: $bridge"
    if nmcli con show 2>/dev/null | grep -q "$bridge"; then
        sudo nmcli con delete "$bridge" 2>/dev/null || true
    fi
    sudo ip link set "$bridge" down 2>/dev/null || true
    sudo ip link delete "$bridge" 2>/dev/null || true
done

# ─── Working directory cleanup ────────────────────────────────────────────────
CLUSTER_DIR_TO_REMOVE=""
if [[ "${WORKING_DIR}" == *"/clusters/${CLUSTER_NAME}" ]]; then
    CLUSTER_DIR_TO_REMOVE="${WORKING_DIR}"
elif [ -n "${BASE_WORKING_DIR:-}" ] && [ -d "${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}" ]; then
    CLUSTER_DIR_TO_REMOVE="${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}"
fi

if [ -n "${CLUSTER_DIR_TO_REMOVE:-}" ] && [ -d "$CLUSTER_DIR_TO_REMOVE" ]; then
    info "Removing cluster working directory: $CLUSTER_DIR_TO_REMOVE"
    sudo rm -rf "$CLUSTER_DIR_TO_REMOVE" || warning "Failed to remove cluster directory"
fi

# ─── Landing-zone directory ───────────────────────────────────────────────────
BASE_DIR="${BASE_WORKING_DIR:-${WORKING_DIR%/clusters/${CLUSTER_NAME}}}"
LZ_DIR="${BASE_DIR}/landing-zone/${CLUSTER_NAME}"
if [ -d "$LZ_DIR" ]; then
    info "Removing landing-zone directory: $LZ_DIR"
    sudo rm -rf "$LZ_DIR" || warning "Failed to remove landing-zone directory"
fi

# ─── Orphaned private-mirror files ───────────────────────────────────────────
if [ -n "${HOME:-}" ]; then
    PRIVATE_MIRROR_HOME="${HOME}/private-mirror-${CLUSTER_NAME}.json"
    if [ -f "$PRIVATE_MIRROR_HOME" ]; then
        info "Removing private-mirror file: $PRIVATE_MIRROR_HOME"
        rm -f "$PRIVATE_MIRROR_HOME"
    fi
    # Remove orphaned private-mirror files from clusters with no active VMs
    if VIRSH_ALL=$(sudo virsh list --all 2>/dev/null); then
        while IFS= read -r mirror_file; do
            [ -z "$mirror_file" ] || [ ! -f "$mirror_file" ] && continue
            ORPHAN_CLUSTER=$(basename "$mirror_file" | sed 's/private-mirror-\(eci-[^.]*\)\.json/\1/')
            echo "${VIRSH_ALL}" | grep -qF "$ORPHAN_CLUSTER" && continue
            info "  Removing orphaned: $(basename "$mirror_file")"
            rm -f "$mirror_file"
        done < <(find "${HOME}" -maxdepth 1 -name "private-mirror-eci-*.json" 2>/dev/null || true)
    else
        warning "Could not query libvirt — skipping orphaned mirror file cleanup"
    fi
fi

# ─── Subnet release ───────────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/../setup/allocate_subnet.sh" ]; then
    info "Releasing allocated subnet for cluster: ${CLUSTER_NAME}"
    export ENCLAVE_CLUSTER_NAME="${CLUSTER_NAME}"
    BASE_DIR="${BASE_WORKING_DIR:-${WORKING_DIR%/clusters/${CLUSTER_NAME}}}"
    export WORKING_DIR="${BASE_DIR}"
    "${SCRIPT_DIR}/../setup/allocate_subnet.sh" release || warning "Failed to release subnet"
fi

success "=========================================="
success "Cleanup complete for cluster: ${CLUSTER_NAME}"
success "=========================================="

# ─── Verification ─────────────────────────────────────────────────────────────
info ""
info "Verifying cleanup..."

LEFTOVER_VMS=$(sudo virsh list --all --name 2>/dev/null | grep -E "^${CLUSTER_NAME}_" || true)
if [ -n "$LEFTOVER_VMS" ]; then
    warning "Found leftover VMs after cleanup:"
    echo "$LEFTOVER_VMS"
else
    success "No leftover VMs"
fi

LEFTOVER_NETS=$(sudo virsh net-list --all 2>/dev/null | grep -E "${CLUSTER_NAME}" || true)
if [ -n "$LEFTOVER_NETS" ]; then
    warning "Found leftover networks:"
    echo "$LEFTOVER_NETS"
else
    success "No leftover networks"
fi

if [ -n "${BASE_WORKING_DIR:-}" ] && [ -d "${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}" ]; then
    warning "Leftover cluster working directory: ${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}"
else
    success "No leftover cluster working directory"
fi

#!/bin/bash
# Provision the Landing Zone VM (CentOS Stream 10 or RHEL 10).
#
# vm_infra.py already defines the Landing Zone domain (NICs, MACs, memory, vcpu,
# disk size, pool volume and a cloud-init cdrom pointing at ENCLAVE_LZ_CLOUD_INIT_ISO).
# This script does the OS provisioning only:
#   1. build the cloud-init ISO at the path the domain references,
#   2. populate the LZ root volume with the cloud image,
#   3. start the (already-defined) domain and wait for it to come up,
#   4. configure the BMC-network static IP and mirror DNS.
# It never undefines/recreates the domain or manages the storage pool.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"
source "${ENCLAVE_DIR}/scripts/lib/ssh.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
load_cluster_env
ensure_working_dir

# Infrastructure facts (owned/emitted by vm_infra.py in cluster-env.sh)
LZ_VM_NAME="${ENCLAVE_LZ_VM_NAME}"
LZ_DISK_PATH="${ENCLAVE_LZ_DISK_PATH}"
LZ_DISK_GB="${ENCLAVE_LZ_DISK_GB}"
CLOUD_INIT_ISO="${ENCLAVE_LZ_CLOUD_INIT_ISO}"
CLUSTER_IP="${ENCLAVE_LZ_CLUSTER_IP:-}"
BMC_IP="${ENCLAVE_LZ_BMC_IP}"
BMC_PREFIX="${ENCLAVE_BMC_NETWORK##*/}"
BMC_GATEWAY="${ENCLAVE_BMC_GATEWAY}"
BMC_PORT="${ENCLAVE_BMC_PORT}"

# Cloud image / OS configuration
CLOUD_IMAGE_URL="${LZ_CLOUD_IMAGE_URL:-https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2}"
CLOUD_IMAGE_NAME="${LZ_CLOUD_IMAGE_NAME:-centos-stream-10-cloud.qcow2}"
CLOUD_IMAGE_CACHE_DIR="${LZ_CLOUD_IMAGE_CACHE_DIR:-/opt/images}"

# Staging dir for the downloaded cloud image (ISO + volume live in the pool).
STAGING_DIR="${WORKING_DIR}/landing-zone/${ENCLAVE_CLUSTER_NAME}"

# SSH public key for the cloud-user account.
if ! SSH_KEY_FILE=$(find_local_ssh_public_key); then
    error "SSH public key not found in ~/.ssh (looked for: $SSH_PUBLIC_KEY_CANDIDATES)"
    error "Please generate an SSH key: ssh-keygen -t ed25519"
    exit 1
fi
SSH_PUBLIC_KEY=$(cat "$SSH_KEY_FILE")

info "Landing Zone VM Provisioning:"
info "  VM Name:        $LZ_VM_NAME"
info "  Root disk:      $LZ_DISK_PATH (${LZ_DISK_GB}G)"
info "  Cloud-init ISO: $CLOUD_INIT_ISO"
info "  Cluster IP:     $CLUSTER_IP   BMC IP: ${BMC_IP}/${BMC_PREFIX}"
echo ""

# Confirm the domain exists (vm_infra.py must have run first).
if ! sudo virsh dominfo "$LZ_VM_NAME" >/dev/null 2>&1; then
    error "Landing Zone domain '$LZ_VM_NAME' not found."
    error "Run 'make -f Makefile.ci environment' to define the infrastructure first."
    exit 1
fi

sudo mkdir -p "$STAGING_DIR"
sudo chown "$USER":"$USER" "$STAGING_DIR"

# --- 1. Build the cloud-init ISO at the path the LZ domain references ----------
info "Building cloud-init ISO..."
cat > "${STAGING_DIR}/meta-data" <<EOF
instance-id: ${LZ_VM_NAME}
local-hostname: enclave-lz
EOF

cat > "${STAGING_DIR}/user-data" <<EOF
#cloud-config
users:
  - name: cloud-user
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: wheel
    shell: /bin/bash
    ssh_authorized_keys:
      - ${SSH_PUBLIC_KEY}

hostname: enclave-lz
fqdn: enclave-lz.${ENCLAVE_BASE_DOMAIN:-lab}

timezone: UTC

ssh_pwauth: false
disable_root: true

final_message: "Enclave Landing Zone VM is ready. Time: \$UPTIME"
EOF

# rh_subscription must appear before runcmd so cloud-init registers the system
# before the runcmd stage uses subscription-manager.
if [ -n "${LZ_RHSM_ORG:-}" ] && [ -n "${LZ_RHSM_ACTIVATION_KEY:-}" ]; then
    info "  Adding RHSM subscription to cloud-init..."
    cat >> "${STAGING_DIR}/user-data" <<RHSM_EOF

rh_subscription:
  activation-key: "${LZ_RHSM_ACTIVATION_KEY}"
  org: "${LZ_RHSM_ORG}"
RHSM_EOF
fi

cat >> "${STAGING_DIR}/user-data" <<'EOF'

runcmd:
EOF

if [ -n "${LZ_RHSM_ORG:-}" ] && [ -n "${LZ_RHSM_ACTIVATION_KEY:-}" ]; then
    cat >> "${STAGING_DIR}/user-data" <<'RUNCMD_EOF'
  - |
    subscription-manager refresh
    subscription-manager repos --disable='*-eus-*' --disable='*-debug-rpms' --disable='*-source-rpms'
    dnf clean metadata
RUNCMD_EOF
fi

cat >> "${STAGING_DIR}/user-data" <<'EOF'
  - dnf install -y git
  - systemctl disable cloud-init
  - touch /etc/cloud/cloud-init.disabled
EOF

# Network config is intentionally omitted: the cluster NIC uses DHCP (static
# leases from vm_infra.py); the BMC NIC static IP is set via nmcli below.
sudo xorrisofs -quiet \
    -output "${CLOUD_INIT_ISO}" \
    -volid cidata -joliet -rock \
    "${STAGING_DIR}/user-data" \
    "${STAGING_DIR}/meta-data"
# Remove cloud-init files that may contain RHSM credentials.
rm -f "${STAGING_DIR}/user-data" "${STAGING_DIR}/meta-data"
info "✓ cloud-init ISO created"

# --- 2. Populate the LZ root volume with the cloud image ----------------------
# Each job gets its own copy to avoid I/O conflicts during parallel execution.
if [ -f "${CLOUD_IMAGE_CACHE_DIR}/${CLOUD_IMAGE_NAME}" ]; then
    info "Using cached cloud image: ${CLOUD_IMAGE_CACHE_DIR}/${CLOUD_IMAGE_NAME}"
    cp "${CLOUD_IMAGE_CACHE_DIR}/${CLOUD_IMAGE_NAME}" "${STAGING_DIR}/${CLOUD_IMAGE_NAME}"
elif [[ "$CLOUD_IMAGE_URL" == file://* ]]; then
    info "Copying cloud image from ${CLOUD_IMAGE_URL}..."
    cp "${CLOUD_IMAGE_URL#file://}" "${STAGING_DIR}/${CLOUD_IMAGE_NAME}"
else
    info "Downloading cloud image..."
    curl -fL --progress-bar -o "${STAGING_DIR}/${CLOUD_IMAGE_NAME}" "$CLOUD_IMAGE_URL"
fi

# If the domain is running (re-provision), stop it before rewriting its disk.
if sudo virsh domstate "$LZ_VM_NAME" 2>/dev/null | grep -q running; then
    info "Stopping running Landing Zone VM for re-provision..."
    sudo virsh destroy "$LZ_VM_NAME" || true
fi

info "Writing cloud image into the LZ volume and resizing to ${LZ_DISK_GB}G..."
sudo qemu-img convert -f qcow2 -O qcow2 "${STAGING_DIR}/${CLOUD_IMAGE_NAME}" "${LZ_DISK_PATH}"
sudo qemu-img resize "${LZ_DISK_PATH}" "${LZ_DISK_GB}G"
rm -f "${STAGING_DIR}/${CLOUD_IMAGE_NAME}"
sudo virsh pool-refresh "$ENCLAVE_CLUSTER_NAME" >/dev/null 2>&1 || true
info "✓ Root disk prepared"

# --- 3. Start the (already-defined) domain and wait for it to come up ---------
info "Starting Landing Zone VM..."
sudo virsh start "$LZ_VM_NAME"

info "Waiting for Landing Zone VM to boot (this may take 2-5 minutes)..."
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 -o BatchMode=yes -q"
MAX_WAIT=300
COUNTER=0
BOOT_COMPLETE=false
while [ $COUNTER -lt $MAX_WAIT ]; do
    # runcmd self-disables cloud-init, so "disabled" is also success once SSH is up.
    CI_STATUS=$(ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "cloud-init status 2>/dev/null" 2>/dev/null || true)
    if [[ "$CI_STATUS" == "status: done" ]] || [[ "$CI_STATUS" == "status: disabled" ]]; then
        BOOT_COMPLETE=true
        break
    fi
    if [ $((COUNTER % 30)) -eq 0 ]; then
        info "  Waiting for cloud-init to complete... (${COUNTER}s elapsed)"
    fi
    sleep 3
    COUNTER=$((COUNTER + 3))
done

if [ "$BOOT_COMPLETE" != true ]; then
    error "Timeout after ${COUNTER}s waiting for the Landing Zone VM."
    error "Debug: sudo virsh console $LZ_VM_NAME ; ssh cloud-user@${CLUSTER_IP}"
    exit 1
fi
info "✓ Landing Zone VM boot complete (${COUNTER}s)"

# --- 4. Configure the BMC-network static IP (enp1s0) --------------------------
info "Configuring BMC network interface (enp1s0 -> ${BMC_IP}/${BMC_PREFIX})..."
if ! ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "ip addr show enp1s0 | grep -q 'inet ${BMC_IP}'" 2>/dev/null; then
    ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "sudo nmcli con show | awk '/enp1s0/{print \$1}' | xargs -r -I{} sudo nmcli con delete {} 2>/dev/null || true"
    ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "sudo nmcli con add type ethernet ifname enp1s0 con-name bmc \
        ipv4.addresses ${BMC_IP}/${BMC_PREFIX} ipv4.method manual \
        connection.autoconnect yes connection.autoconnect-priority 100" 2>/dev/null \
      || ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "sudo nmcli con mod bmc \
        ipv4.addresses ${BMC_IP}/${BMC_PREFIX} ipv4.method manual" 2>/dev/null || true
    ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "sudo nmcli con up bmc" 2>/dev/null || true
    sleep 3
fi
if ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "ip addr show enp1s0 | grep -q '${BMC_IP}'" 2>/dev/null; then
    info "✓ BMC network configured"
else
    warning "BMC network configuration may have failed — verify manually"
fi

# Verify the LZ can reach the sushy-tools BMC endpoint (required for Ironic).
if ssh $SSH_OPTS cloud-user@"${CLUSTER_IP}" "curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 5 https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems 2>/dev/null | grep -q 200"; then
    info "✓ sushy-tools reachable from the Landing Zone"
else
    error "Cannot reach sushy-tools at https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems from the LZ"
    error "Check the sushy-tools container: sudo podman ps | grep sushy-tools"
    exit 1
fi

# Cluster DNS (mirror / api / *.apps) is baked into the libvirt networks by vm_infra.py
# (see Config.cluster_dns_addresses), so there is no runtime net-update here.

echo ""
info "========================================="
info "Landing Zone VM Provisioned Successfully"
info "========================================="
info "  SSH:         ssh cloud-user@${CLUSTER_IP}"
info "  BMC IP:      ${BMC_IP} (enp1s0)"
info "  Cluster IP:  ${CLUSTER_IP} (enp2s0)"
info ""
info "Next: make install-enclave"

#!/bin/bash
# Provision Landing Zone VM with CentOS Stream 10
#
# This script provisions the existing Landing Zone VM (created by dev-scripts)
# with CentOS Stream 10 cloud image and configures it for Enclave Lab deployment.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

# Validate required environment variables
require_env_var "DEV_SCRIPTS_PATH"

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration to get network and cluster info
load_devscripts_config

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-enclave-test}"
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"
ensure_working_dir
LZ_WORKING_DIR="${WORKING_DIR}/landing-zone/${CLUSTER_NAME}"
CLOUD_IMAGE_URL="https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2"
CLOUD_IMAGE_NAME="centos-stream-10-cloud.qcow2"
# Use cluster-specific storage pool for isolation in parallel execution
# Each cluster gets its own pool in its dedicated working directory
POOL_NAME="${CLUSTER_NAME}"
POOL_PATH="${WORKING_DIR}/pool"

# Network configuration (from dev-scripts config)
BMC_NETWORK="${PROVISIONING_NETWORK}"
BMC_NETWORK_NAME="${PROVISIONING_NETWORK_NAME:-bmc}"
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"
CLUSTER_NETWORK_NAME="${BAREMETAL_NETWORK_NAME:-cluster}"

# Calculate network prefixes for IP detection
BMC_IP=$(echo "$BMC_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".2"}')
BMC_PREFIX=$(echo "$BMC_NETWORK" | sed 's|.*/||')

# Extract cluster network prefix for dynamic IP detection
CLUSTER_NET_PREFIX=$(get_network_prefix "$CLUSTER_NETWORK")
CLUSTER_IP="${CLUSTER_NET_PREFIX}.2"  # Initial guess, will be updated from DHCP

# SSH key
SSH_KEY_FILE="$HOME/.ssh/id_rsa.pub"
if [ ! -f "$SSH_KEY_FILE" ]; then
    error "SSH public key not found: $SSH_KEY_FILE"
    error "Please generate SSH key: ssh-keygen -t rsa -b 4096"
    exit 1
fi
SSH_PUBLIC_KEY=$(cat "$SSH_KEY_FILE")

info "Landing Zone VM Provisioning Configuration:"
info "  VM Name: $LZ_VM_NAME"
info "  BMC Network: $BMC_NETWORK (DHCP)"
info "  Cluster Network: $CLUSTER_NETWORK (DHCP)"
info "  Working Directory: $LZ_WORKING_DIR"
info ""
info "Note: VM will use DHCP for initial boot. Static IPs can be configured later if needed."
echo ""

# Create working directory
info "Creating working directory: $LZ_WORKING_DIR"
sudo mkdir -p "$LZ_WORKING_DIR"
sudo chown $USER:$USER "$LZ_WORKING_DIR"

# Download CentOS Stream 10 cloud image
if [ ! -f "${LZ_WORKING_DIR}/${CLOUD_IMAGE_NAME}" ]; then
    info "Downloading CentOS Stream 10 cloud image..."
    wget -nv -O "${LZ_WORKING_DIR}/${CLOUD_IMAGE_NAME}" "$CLOUD_IMAGE_URL"
    info "✓ Cloud image downloaded"
else
    info "Cloud image already exists, skipping download"
fi

# Create cloud-init configuration
info "Creating cloud-init configuration..."

cat > "${LZ_WORKING_DIR}/meta-data" <<EOF
instance-id: ${LZ_VM_NAME}
local-hostname: enclave-lz
EOF

cat > "${LZ_WORKING_DIR}/user-data" <<EOF
#cloud-config
users:
  - name: cloud-user
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: wheel
    shell: /bin/bash
    ssh_authorized_keys:
      - ${SSH_PUBLIC_KEY}

hostname: enclave-lz
fqdn: enclave-lz.${CLUSTER_DOMAIN:-enclave-test.lab}

packages:
  - git
  - vim
  - curl
  - wget
  - jq
  - python3
  - python3-pip
  - ansible-core

runcmd:
  - systemctl disable cloud-init
  - touch /etc/cloud/cloud-init.disabled

timezone: UTC

ssh_pwauth: false
disable_root: true

final_message: "Enclave Landing Zone VM is ready. Time: \$UPTIME"
EOF

# Note: Skipping network-config - let cloud-init use DHCP from libvirt networks
# This ensures the VM gets network connectivity quickly
# Static IPs can be configured later if needed via Task 3

info "✓ cloud-init configuration created"

# Create cloud-init ISO
info "Creating cloud-init ISO..."
sudo genisoimage -output "${LZ_WORKING_DIR}/cloud-init.iso" \
    -volid cidata -joliet -rock \
    "${LZ_WORKING_DIR}/user-data" \
    "${LZ_WORKING_DIR}/meta-data" 2>&1 | grep -v "Warning: creating filesystem"
info "✓ cloud-init ISO created"

# Stop and remove existing VM if it exists
if sudo virsh list --all | grep -q "$LZ_VM_NAME"; then
    info "Removing existing Landing Zone VM..."
    if sudo virsh list --state-running | grep -q "$LZ_VM_NAME"; then
        sudo virsh destroy "$LZ_VM_NAME"
    fi
    sudo virsh undefine "$LZ_VM_NAME" --nvram 2>/dev/null || sudo virsh undefine "$LZ_VM_NAME"
    info "✓ Existing VM removed"
fi

# Find or create storage pool for cluster-specific path
# dev-scripts may create a pool (possibly named oooq_pool) pointing to our cluster path
# We'll use whatever pool exists for our path, or create one if needed
if ! sudo virsh pool-uuid "$POOL_NAME" > /dev/null 2>&1; then
    info "Pool '$POOL_NAME' not found, checking if any pool uses path $POOL_PATH..."

    # Check if any pool already points to our cluster-specific path
    EXISTING_POOL=$(sudo virsh pool-list --all --name | while read pool; do
        if [ -n "$pool" ]; then
            POOL_PATH_CHECK=$(sudo virsh pool-dumpxml "$pool" 2>/dev/null | grep -oP '(?<=<path>).*(?=</path>)' || echo "")
            if [ "$POOL_PATH_CHECK" = "$POOL_PATH" ]; then
                echo "$pool"
                break
            fi
        fi
    done)

    if [ -n "$EXISTING_POOL" ]; then
        # A pool exists for our path - use it regardless of name
        info "Found existing pool '$EXISTING_POOL' using path $POOL_PATH, will use it"
        POOL_NAME="$EXISTING_POOL"

        # Ensure the pool is active - start it or ignore if already active
        set +e  # Temporarily disable exit on error
        START_OUTPUT=$(sudo virsh pool-start "$POOL_NAME" 2>&1)
        START_EXIT_CODE=$?
        set -e  # Re-enable exit on error

        if [ $START_EXIT_CODE -eq 0 ]; then
            info "✓ Pool '$POOL_NAME' started"
        elif echo "$START_OUTPUT" | grep -q "already active"; then
            info "✓ Pool '$POOL_NAME' is already active"
        else
            error "Failed to start pool '$POOL_NAME': $START_OUTPUT"
            exit 1
        fi

        sudo virsh pool-autostart "$POOL_NAME" 2>/dev/null || true
        sudo virsh pool-refresh "$POOL_NAME" 2>/dev/null || true
    else
        # No pool exists for our path - create one
        info "No pool found for path $POOL_PATH, creating pool '$POOL_NAME'..."

        # Create the pool directory if it doesn't exist
        sudo mkdir -p "$POOL_PATH"

        # Define and start the pool
        sudo virsh pool-define /dev/stdin <<EOF
<pool type='dir'>
  <name>$POOL_NAME</name>
  <target>
    <path>$POOL_PATH</path>
    <permissions>
      <mode>0755</mode>
      <owner>-1</owner>
      <group>-1</group>
    </permissions>
  </target>
</pool>
EOF

        sudo virsh pool-start "$POOL_NAME"
        sudo virsh pool-autostart "$POOL_NAME"
        info "✓ Storage pool '$POOL_NAME' created and started"
    fi
else
    info "Using existing storage pool: $POOL_NAME"
    # Ensure the pool is started (it might be inactive)
    set +e  # Temporarily disable exit on error
    START_OUTPUT=$(sudo virsh pool-start "$POOL_NAME" 2>&1)
    START_EXIT_CODE=$?
    set -e  # Re-enable exit on error

    if [ $START_EXIT_CODE -eq 0 ]; then
        info "✓ Pool '$POOL_NAME' started"
    elif echo "$START_OUTPUT" | grep -q "already active"; then
        info "✓ Pool '$POOL_NAME' is already active"
    else
        error "Failed to start pool '$POOL_NAME': $START_OUTPUT"
        exit 1
    fi

    sudo virsh pool-autostart "$POOL_NAME" 2>/dev/null || true
fi

# Refresh the pool so libvirt sees all volumes (important for sushy-tools)
info "Refreshing storage pool '$POOL_NAME'..."
sudo virsh pool-refresh "$POOL_NAME"

# Ensure pool is active after refresh
set +e  # Temporarily disable exit on error
FINAL_START_OUTPUT=$(sudo virsh pool-start "$POOL_NAME" 2>&1)
FINAL_START_EXIT_CODE=$?
set -e  # Re-enable exit on error

if [ $FINAL_START_EXIT_CODE -eq 0 ]; then
    info "✓ Pool '$POOL_NAME' started"
elif echo "$FINAL_START_OUTPUT" | grep -q "already active"; then
    info "✓ Pool '$POOL_NAME' is active and ready"
else
    error "Failed to ensure pool is active: $FINAL_START_OUTPUT"
    exit 1
fi

# Get pool path and prepare disk
info "Preparing VM disk in libvirt pool..."
POOL_PATH=$(sudo virsh pool-dumpxml "$POOL_NAME" | grep "<path>" | sed 's/.*<path>\(.*\)<\/path>.*/\1/')
info "Pool path: $POOL_PATH"

# Delete old disk volume if exists
if sudo virsh vol-list "$POOL_NAME" | grep -q "${LZ_VM_NAME}.qcow2"; then
    info "Removing old disk volume..."
    sudo virsh vol-delete "${LZ_VM_NAME}.qcow2" --pool "$POOL_NAME"
fi

# Convert cloud image to pool location
info "Converting cloud image to pool volume..."
sudo qemu-img convert -f qcow2 -O qcow2 \
    "${LZ_WORKING_DIR}/${CLOUD_IMAGE_NAME}" \
    "${POOL_PATH}/${LZ_VM_NAME}.qcow2"

# Resize to 600GB (needed for OpenShift image mirroring)
# Provides ample space for: Quay storage ~130GB, oc-mirror workspace ~7GB,
# ISO files ~3GB, binaries ~3GB, OS and other ~7GB, plus large buffer
info "Resizing disk to 600GB..."
sudo qemu-img resize "${POOL_PATH}/${LZ_VM_NAME}.qcow2" 600G

# Refresh pool so libvirt sees the new volume
info "Refreshing libvirt pool..."
sudo virsh pool-refresh "$POOL_NAME"
info "✓ Disk prepared in pool"

# Create VM using virt-install with BIOS boot
info "Creating Landing Zone VM with virt-install..."
sudo virt-install \
    --name "$LZ_VM_NAME" \
    --memory 8192 \
    --vcpus 4 \
    --disk vol=${POOL_NAME}/${LZ_VM_NAME}.qcow2,bus=virtio \
    --disk "${LZ_WORKING_DIR}/cloud-init.iso,device=cdrom,bus=sata" \
    --network network=${BMC_NETWORK_NAME} \
    --network network=${CLUSTER_NETWORK_NAME} \
    --boot hd,cdrom \
    --os-variant centos-stream10 \
    --graphics vnc \
    --noautoconsole \
    --import

info "✓ Landing Zone VM created and started"

# Wait for VM to boot and get IP via DHCP
info "Waiting for Landing Zone VM to boot and get IP (this may take 2-3 minutes)..."
MAX_WAIT=180
COUNTER=0
BOOT_COMPLETE=false
SSH_READY=false
VM_IP=""

while [ $COUNTER -lt $MAX_WAIT ]; do
    # Try to get VM IP from libvirt (use dynamic network prefix from allocated subnet)
    if [ -z "$VM_IP" ]; then
        VM_IP=$(get_vm_ip_on_network "$LZ_VM_NAME" "$CLUSTER_NETWORK")
        if [ -n "$VM_IP" ]; then
            info "  VM got IP address: $VM_IP (${COUNTER}s)"
            CLUSTER_IP="$VM_IP"  # Update with actual IP from DHCP
        fi
    fi

    # Once we have an IP, check if SSH port is open
    if [ -n "$VM_IP" ] && ! $SSH_READY; then
        if nc -z -w 2 ${VM_IP} 22 2>/dev/null; then
            info "  SSH port is now open (${COUNTER}s)"
            SSH_READY=true
        fi
    fi

    # Try to SSH and check for cloud-init completion
    if [ -n "$VM_IP" ]; then
        if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
               -o ConnectTimeout=3 -o BatchMode=yes -q cloud-user@${VM_IP} \
               "cloud-init status --wait" 2>/dev/null; then
            BOOT_COMPLETE=true
            break
        fi
    fi

    # Show progress
    if [ $((COUNTER % 30)) -eq 0 ]; then
        if [ -z "$VM_IP" ]; then
            info "  Waiting for VM to get IP address... (${COUNTER}s elapsed)"
        elif $SSH_READY; then
            info "  Waiting for cloud-init to complete... (${COUNTER}s elapsed)"
        else
            info "  Waiting for SSH service... (${COUNTER}s elapsed)"
        fi
    fi

    sleep 3
    COUNTER=$((COUNTER + 3))
done

if [ "$BOOT_COMPLETE" = true ]; then
    info "✓ Landing Zone VM boot complete! (${COUNTER}s)"

    # Configure BMC network interface
    echo ""
    info "Configuring BMC network interface..."
    SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -q"

    # Check if BMC interface already has an IP
    BMC_CHECK=$(ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip addr show enp1s0 | grep 'inet ${BMC_IP}'" 2>/dev/null || echo "")

    if [ -z "$BMC_CHECK" ]; then
        info "  Configuring enp1s0 with IP ${BMC_IP}/${BMC_PREFIX}..."

        # Delete any existing cloud-init or DHCP connections on enp1s0
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo nmcli con show | grep enp1s0 | awk '{print \$1}' | xargs -r -I{} sudo nmcli con delete {} 2>/dev/null || true"

        # Configure BMC interface using nmcli with static IP
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo nmcli con add type ethernet ifname enp1s0 con-name bmc \
            ipv4.addresses ${BMC_IP}/${BMC_PREFIX} \
            ipv4.method manual \
            connection.autoconnect yes \
            connection.autoconnect-priority 100" 2>/dev/null || {
            # Connection may already exist, modify it
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo nmcli con mod bmc \
                ipv4.addresses ${BMC_IP}/${BMC_PREFIX} \
                ipv4.method manual \
                connection.autoconnect yes \
                connection.autoconnect-priority 100" 2>/dev/null || true
        }

        # Ensure interface is up and activate the connection
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo ip link set enp1s0 up"
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo nmcli con up bmc" 2>/dev/null || {
            # Fallback to manual IP configuration if nmcli fails
            warning "  nmcli failed, using manual IP configuration"
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo ip addr flush dev enp1s0"
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo ip addr add ${BMC_IP}/${BMC_PREFIX} dev enp1s0"
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo ip link set enp1s0 up"
        }

        # Wait for interface to stabilize
        sleep 3

        # Verify configuration
        if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip addr show enp1s0 | grep -q '${BMC_IP}'" 2>/dev/null; then
            info "✓ BMC network configured: enp1s0 (${BMC_IP}/${BMC_PREFIX})"
        else
            warning "BMC network configuration may have failed - verify manually"
        fi
    else
        info "✓ BMC network already configured: enp1s0 (${BMC_IP}/${BMC_PREFIX})"
    fi

    # Verify BMC gateway connectivity (critical for Ironic to work)
    info "Verifying BMC gateway connectivity..."
    BMC_GATEWAY=$(get_network_gateway "$BMC_NETWORK")
    BMC_PORT=$(calculate_bmc_port "$BMC_NETWORK")

    # Before attempting ping, collect diagnostic information
    info "Collecting network diagnostics..."

    # Define bridge name early for use in diagnostics
    BRIDGE_NAME="${CLUSTER_NAME}-p"

    # Check for IP/network conflicts on host
    BMC_SUBNET=$(echo "$BMC_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3}')

    info "  Checking for IP conflicts on host:"
    CONFLICTING_IPS=$(ip addr show | grep "inet ${BMC_SUBNET}\." | grep -v "${BRIDGE_NAME}" || true)
    if [ -n "$CONFLICTING_IPS" ]; then
        warning "    ⚠ Found other interfaces using ${BMC_SUBNET}.0/24:"
        echo "$CONFLICTING_IPS" | while IFS= read -r line; do
            warning "      $line"
        done
    else
        info "    ✓ No IP conflicts on other interfaces"
    fi

    # Check for existing libvirt networks using this subnet
    info "  Checking for conflicting libvirt networks:"
    CONFLICTING_NETS=$(sudo virsh net-list --all | grep -v "$BRIDGE_NAME" | awk 'NR>2 {print $1}' | while read net; do
        if sudo virsh net-dumpxml "$net" 2>/dev/null | grep -q "${BMC_SUBNET}\."; then
            echo "$net"
        fi
    done)
    if [ -n "$CONFLICTING_NETS" ]; then
        warning "    ⚠ Found libvirt networks using ${BMC_SUBNET}.0/24:"
        echo "$CONFLICTING_NETS" | while IFS= read -r net; do
            NET_IP=$(sudo virsh net-dumpxml "$net" 2>/dev/null | grep -oP '(?<=<ip address=")[^"]*' || echo "unknown")
            warning "      Network '$net' has IP: $NET_IP"
        done
    else
        info "    ✓ No conflicting libvirt networks"
    fi

    # Check bridge on host
    info "  Bridge ${BRIDGE_NAME} status on host:"
    if sudo ip addr show "$BRIDGE_NAME" 2>/dev/null | grep -q "inet ${BMC_GATEWAY}/"; then
        info "    ✓ Bridge has IP ${BMC_GATEWAY}"
    else
        warning "    ✗ Bridge does NOT have expected IP ${BMC_GATEWAY}"
        sudo ip addr show "$BRIDGE_NAME" 2>/dev/null | grep "inet " || true
    fi

    # Check if host can ping the bridge IP
    if ping -c 1 -W 2 ${BMC_GATEWAY} >/dev/null 2>&1; then
        info "    ✓ Host can ping bridge IP ${BMC_GATEWAY}"
    else
        warning "    ✗ Host CANNOT ping bridge IP ${BMC_GATEWAY}"
    fi

    # Check VM interface configuration
    info "  VM interface enp1s0:"
    ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip addr show enp1s0" 2>&1 | grep "inet " | while IFS= read -r line; do
        info "    $line"
    done

    # Check VM routing table
    info "  VM routing table:"
    ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip route show" 2>&1 | grep -E "${BMC_NETWORK%/*}" | while IFS= read -r line; do
        info "    $line"
    done

    # Check bridge interface membership
    info "  Bridge ${BRIDGE_NAME} members:"
    sudo bridge link show | grep "$BRIDGE_NAME" | while IFS= read -r line; do
        info "    $line"
    done

    # Check if VM's vnet interface is attached
    VM_NAME="${CLUSTER_NAME}_landingzone_0"
    LZ_VNET_BMC=$(sudo virsh domiflist "$VM_NAME" 2>/dev/null | grep "$BRIDGE_NAME" | awk '{print $1}')
    if [ -n "$LZ_VNET_BMC" ]; then
        info "    VM BMC vnet interface: $LZ_VNET_BMC"
        if sudo bridge link show | grep -q "$LZ_VNET_BMC"; then
            info "    ✓ $LZ_VNET_BMC is attached to bridge"
        else
            warning "    ✗ $LZ_VNET_BMC is NOT attached to bridge"
        fi
    else
        warning "    ✗ Could not find VM's vnet interface for BMC network"
    fi

    # Check bridge forwarding settings
    info "  Bridge forwarding settings:"
    BRIDGE_FWD=$(cat /sys/class/net/${BRIDGE_NAME}/bridge/stp_state 2>/dev/null || echo "unknown")
    info "    STP state: $BRIDGE_FWD"

    # Check if bridge has proxy_arp enabled
    PROXY_ARP=$(cat /proc/sys/net/ipv4/conf/${BRIDGE_NAME}/proxy_arp 2>/dev/null || echo "unknown")
    info "    proxy_arp: $PROXY_ARP"

    # Check firewall zones and ARP filtering
    info "  Firewall configuration:"
    if sudo firewall-cmd --state >/dev/null 2>&1; then
        BMC_ZONE=$(sudo firewall-cmd --get-zone-of-interface="$BRIDGE_NAME" 2>/dev/null || echo "none")
        info "    Bridge zone: $BMC_ZONE"
        if [ "$BMC_ZONE" != "none" ]; then
            if sudo firewall-cmd --zone="$BMC_ZONE" --query-icmp-block=echo-request 2>/dev/null; then
                warning "    ✗ ICMP echo-request is BLOCKED in zone $BMC_ZONE"
            else
                info "    ✓ ICMP echo-request allowed in zone $BMC_ZONE"
            fi
        fi
    else
        info "    Firewalld not running"
    fi

    # Check ebtables for ARP filtering (this is the likely culprit)
    info "  Layer 2 filtering (ebtables):"
    if command -v ebtables &>/dev/null; then
        if sudo ebtables -L 2>/dev/null | grep -q "Bridge"; then
            info "    ebtables rules exist (checking for ARP blocks):"
            # Use || true to prevent grep failure from exiting script
            ARP_RULES=$(sudo ebtables -L 2>/dev/null | grep -E "ARP|$BRIDGE_NAME" || true)
            if [ -n "$ARP_RULES" ]; then
                echo "$ARP_RULES" | while IFS= read -r line; do
                    info "      $line"
                done
            else
                info "      No ARP-related rules found"
            fi
        else
            info "    No ebtables rules"
        fi
    else
        info "    ebtables not installed"
    fi

    # Check nftables for ARP filtering
    info "  Netfilter bridge filtering:"
    if sudo nft list tables 2>/dev/null | grep -q "bridge"; then
        info "    nftables bridge family rules exist:"
        # Use || true to prevent grep failure from exiting script
        NFT_ARP_RULES=$(sudo nft list table bridge filter 2>/dev/null | grep -E "arp|ARP" || true)
        if [ -n "$NFT_ARP_RULES" ]; then
            echo "$NFT_ARP_RULES" | while IFS= read -r line; do
                info "      $line"
            done
        else
            info "      No ARP-related nftables rules found"
        fi
    else
        info "    No nftables bridge rules"
    fi

    # Check br_netfilter settings
    info "  Bridge netfilter settings:"
    if [ -f /proc/sys/net/bridge/bridge-nf-call-arptables ]; then
        BR_NF_ARP=$(cat /proc/sys/net/bridge/bridge-nf-call-arptables)
        info "    bridge-nf-call-arptables: $BR_NF_ARP"
        if [ "$BR_NF_ARP" = "1" ]; then
            warning "    ⚠ ARP packets are being passed to arptables (may be filtered)"
        fi
    fi

    # Check if there are any arptables rules
    if command -v arptables &>/dev/null; then
        info "  ARP tables:"
        # Get arptables rules, filtering out headers and empty lines
        ARPTABLES_RULES=$(sudo arptables -L -n 2>/dev/null | grep -v "^Chain\|^$" || true)
        if [ -n "$ARPTABLES_RULES" ]; then
            echo "$ARPTABLES_RULES" | while IFS= read -r line; do
                info "    $line"
            done
        else
            info "    No arptables rules"
        fi
    else
        info "    arptables not installed"
    fi

    # Test bidirectional connectivity
    info "Testing connectivity:"
    info "  From host to VM's BMC IP (${BMC_IP}):"
    if ping -c 2 -W 2 ${BMC_IP} >/dev/null 2>&1; then
        info "    ✓ Host can ping VM's BMC IP ${BMC_IP}"
    else
        warning "    ✗ Host CANNOT ping VM's BMC IP ${BMC_IP}"
        warning "    This suggests ARP is failing in both directions"
    fi

    # Try enabling proxy_arp as a workaround
    info "  Attempting to enable proxy_arp on bridge ${BRIDGE_NAME}..."
    if sudo sysctl -w net.ipv4.conf.${BRIDGE_NAME}.proxy_arp=1 >/dev/null 2>&1; then
        info "    ✓ proxy_arp enabled"
        PROXY_ARP_NEW=$(cat /proc/sys/net/ipv4/conf/${BRIDGE_NAME}/proxy_arp 2>/dev/null || echo "unknown")
        info "    New proxy_arp value: $PROXY_ARP_NEW"
    else
        warning "    ✗ Failed to enable proxy_arp"
    fi

    # Retry ping check
    MAX_PING_ATTEMPTS=3
    PING_WAIT_SECONDS=2
    PING_SUCCESS=false

    info "Attempting to ping BMC gateway ${BMC_GATEWAY} from VM..."
    for attempt in $(seq 1 $MAX_PING_ATTEMPTS); do
        if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ping -c 2 -W 2 ${BMC_GATEWAY} >/dev/null 2>&1"; then
            info "✓ Can ping BMC gateway: ${BMC_GATEWAY} (attempt $attempt)"
            PING_SUCCESS=true
            break
        fi

        if [ $attempt -lt $MAX_PING_ATTEMPTS ]; then
            info "  Attempt $attempt/$MAX_PING_ATTEMPTS: Cannot ping ${BMC_GATEWAY}, waiting ${PING_WAIT_SECONDS}s..."

            # Check ARP after failed ping
            info "    Checking ARP table on VM:"
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip neigh show ${BMC_GATEWAY}" 2>&1 | while IFS= read -r line; do
                info "      $line"
            done

            sleep $PING_WAIT_SECONDS
        fi
    done

    if [ "$PING_SUCCESS" = false ]; then
        error "Cannot ping BMC gateway: ${BMC_GATEWAY} after $MAX_PING_ATTEMPTS attempts"
        error ""
        error "Full diagnostic information:"
        error "  Host bridge ${BRIDGE_NAME}:"
        sudo ip addr show "$BRIDGE_NAME" 2>&1 | while IFS= read -r line; do
            error "    $line"
        done
        error ""
        error "  VM interface enp1s0:"
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip addr show enp1s0" 2>&1 | while IFS= read -r line; do
            error "    $line"
        done
        error ""
        error "  VM routing table:"
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip route" 2>&1 | while IFS= read -r line; do
            error "    $line"
        done
        error ""
        error "  VM ARP table:"
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip neigh" 2>&1 | while IFS= read -r line; do
            error "    $line"
        done

        error ""
        error "This will cause Ironic deployment to fail"
        exit 1
    fi

    # Test sushy-tools endpoint
    if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 5 https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems 2>/dev/null | grep -q 200"; then
        info "✓ Can reach sushy-tools at https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems"
    else
        error "Cannot reach sushy-tools endpoint at https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems"
        error "This will cause Ironic deployment to fail"
        error "Check that sushy-tools container is running: sudo podman ps | grep sushy-tools"
        exit 1
    fi

    echo ""
    info "========================================="
    info "Landing Zone VM Provisioned Successfully"
    info "========================================="
    info ""
    info "Access Information:"
    info "  SSH: ssh cloud-user@${CLUSTER_IP}"
    info "  BMC Network IP: ${BMC_IP} (enp1s0)"
    info "  Cluster Network IP: ${CLUSTER_IP} (enp2s0)"
    info ""
    info "Next steps:"
    info "  1. Verify connectivity: make verify-landing-zone"
    info "  2. Install Enclave Lab: make install-enclave (Task 3)"
    info ""
else
    warning "Timeout after ${COUNTER}s - VM is running but SSH not ready"
    info ""
    info "Debugging steps:"
    info "  1. Check console: sudo virsh console $LZ_VM_NAME"
    info "  2. Check if VM got IP: sudo virsh domifaddr $LZ_VM_NAME"
    info "  3. Try SSH manually: ssh cloud-user@${CLUSTER_IP}"
    info "  4. Check network: ping ${CLUSTER_IP}"
    info ""
    warning "The VM may need more time to boot. Wait 1-2 minutes and run:"
    warning "  make verify-landing-zone"
    info ""
    exit 1
fi

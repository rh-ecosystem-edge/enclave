#!/bin/bash
# Install and configure OpenSnitch on the Landing Zone VM in accept-all + log mode.
# OpenSnitch captures every outbound connection with FQDN, protocol, and port.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"
source "${ENCLAVE_DIR}/scripts/lib/ssh.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

require_env_var "DEV_SCRIPTS_PATH"

ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
load_devscripts_config

LZ_VM_NAME="${ENCLAVE_CLUSTER_NAME}_landingzone_0"
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"

CLUSTER_IP=$(get_vm_ip_on_network "$LZ_VM_NAME" "$CLUSTER_NETWORK")
if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    exit 1
fi

setup_ssh_config "$CLUSTER_IP"

info "Installing OpenSnitch on Landing Zone ($CLUSTER_IP)..."

# shellcheck disable=SC2086
ssh $SSH_OPTS "$LZ_SSH" "sudo bash -s" <<'EOF'
set -euo pipefail

# OpenSnitch is not packaged for CentOS Stream 10 in EPEL yet
# Install from GitHub releases instead
OPENSNITCH_VERSION="1.8.0"
OPENSNITCH_RPM="opensnitch-${OPENSNITCH_VERSION}-1.x86_64.rpm"
DOWNLOAD_URL="https://github.com/evilsocket/opensnitch/releases/download/v${OPENSNITCH_VERSION}/${OPENSNITCH_RPM}"

curl -fsSL -o "/tmp/${OPENSNITCH_RPM}" "${DOWNLOAD_URL}"
dnf install -y "/tmp/${OPENSNITCH_RPM}" 2>&1
rm -f "/tmp/${OPENSNITCH_RPM}"

# Write accept-all config (static JSON — no variable expansion needed)
mkdir -p /etc/opensnitchd/rules
cat > /etc/opensnitchd/default-config.json <<'CONF'
{
    "Server": {
        "Address": "unix:///tmp/osui.sock",
        "LogFile": "/var/log/opensnitchd.log",
        "Loggers": [
            {
                "Name": "syslog",
                "Server": "",
                "Protocol": "udp",
                "Format": "rfc5424"
            }
        ]
    },
    "DefaultAction": "allow",
    "DefaultDuration": "always",
    "InterceptUnknown": true,
    "ProcMonitorMethod": "ebpf",
    "LogLevel": 2,
    "Firewall": "nftables",
    "FwOptions": {
        "MonitorInterval": "15s"
    },
    "Rules": {
        "Path": "/etc/opensnitchd/rules/",
        "MonitorInterval": "10s"
    },
    "Ebpf": {
        "ModulesPath": "/usr/lib/opensnitchd/ebpf/"
    },
    "Stats": {
        "MaxEvents": 150,
        "Workers": 4
    }
}
CONF

# Restart the service to pick up the new config (RPM auto-started it with defaults)
systemctl restart opensnitch
systemctl enable opensnitch

# Wait for the daemon to be ready
for i in $(seq 1 10); do
    if systemctl is-active --quiet opensnitch; then
        echo "opensnitch is running"
        exit 0
    fi
    sleep 2
done

echo "ERROR: opensnitch did not start within 20s" >&2
systemctl status opensnitch >&2
exit 1
EOF

success "OpenSnitch installed and running on Landing Zone"

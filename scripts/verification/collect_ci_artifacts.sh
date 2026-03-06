#!/bin/bash
# CI Artifact Collection Script
#
# Collects diagnostics for CI debugging based on collection level
#
# Usage:
#   ./collect_ci_artifacts.sh <level> <output_dir>
#
# Levels:
#   basic        - Essential info only - for all jobs
#   infra        - Infrastructure diagnostics - for infra jobs
#   deployment   - Full deployment diagnostics - for E2E jobs
#   full         - Everything including cluster diagnostics - on failure
#
# Example:
#   ./collect_ci_artifacts.sh infra ./artifacts

set -euo pipefail

# Check arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <level> <output_dir>"
    echo "Levels: basic, infra, deployment, full"
    exit 1
fi

LEVEL="$1"
OUTPUT_DIR="$2"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Track collection warnings/errors
COLLECTION_WARNINGS=0
COLLECTION_ERRORS=0

info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    COLLECTION_WARNINGS=$((COLLECTION_WARNINGS + 1))
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    COLLECTION_ERRORS=$((COLLECTION_ERRORS + 1))
}

# Create base output directories (landing-zone and cluster created on-demand)
mkdir -p "${OUTPUT_DIR}"/{system,libvirt,network,dev-scripts}

info "Starting artifact collection (level: $LEVEL)"
info "Output directory: $OUTPUT_DIR"

#####################################
# SYSTEM COLLECTION FUNCTIONS
#####################################

collect_system_info() {
    info "Collecting system information..."
    {
        echo "=== System Info ==="
        uname -a
        cat /etc/os-release 2>/dev/null || true
        echo ""
        echo "=== Uptime ==="
        uptime
        echo ""
        echo "=== Memory ==="
        free -h
        echo ""
        echo "=== Disk Usage ==="
        df -h
        echo ""
        echo "=== CPU Info ==="
        lscpu | head -20
    } > "${OUTPUT_DIR}/system/system-info.txt" 2>&1
}

collect_system_processes() {
    info "Collecting process information..."
    ps auxf > "${OUTPUT_DIR}/system/processes.txt" 2>&1 || true
    top -b -n 1 > "${OUTPUT_DIR}/system/top-snapshot.txt" 2>&1 || true
}

collect_system_performance() {
    info "Collecting performance metrics..."
    iostat -x 1 3 > "${OUTPUT_DIR}/system/iostat.txt" 2>&1 || true
    vmstat 1 3 > "${OUTPUT_DIR}/system/vmstat.txt" 2>&1 || true
}

collect_system_logs() {
    info "Collecting system logs..."
    {
        echo "=== Recent Journal Errors ==="
        # Filter out harmless virtqemud "End of file" errors during VM shutdowns
        sudo journalctl -p err -n 100 --no-pager | grep -v "virtqemud.*End of file while reading data" || true
        echo ""
        echo "=== libvirt Logs ==="
        sudo journalctl -u libvirtd -u virtqemud -n 200 --no-pager | grep -v "End of file while reading data" || true
    } > "${OUTPUT_DIR}/system/system-logs.txt" 2>&1
}

#####################################
# LIBVIRT COLLECTION FUNCTIONS
#####################################

collect_libvirt_vms() {
    info "Collecting VM information..."
    {
        echo "=== VM List ==="
        sudo virsh list --all
        echo ""
        echo "=== VM Details ==="
        for vm in $(sudo virsh list --all --name); do
            if [ -n "$vm" ]; then
                echo "--- VM: $vm ---"
                sudo virsh dominfo "$vm" 2>&1 || true
                echo ""
                echo "VM State:"
                sudo virsh domstate "$vm" 2>&1 || true
                echo ""
                echo "VM IP Addresses:"
                sudo virsh domifaddr "$vm" 2>&1 || true
                echo ""
            fi
        done
    } > "${OUTPUT_DIR}/libvirt/vms.txt" 2>&1
}

collect_libvirt_vm_xml() {
    info "Collecting VM XML definitions..."
    # Only collect enclave/eci VMs to avoid clutter
    for vm in $(sudo virsh list --all --name | grep -E 'enclave|eci'); do
        if [ -n "$vm" ]; then
            sudo virsh dumpxml "$vm" 2>&1 | sudo tee "${OUTPUT_DIR}/libvirt/vm-${vm}.xml" >/dev/null || true
        fi
    done
}

collect_libvirt_networks() {
    info "Collecting network information..."
    {
        echo "=== Network List ==="
        sudo virsh net-list --all
        echo ""
        echo "=== Network Details ==="
        for net in $(sudo virsh net-list --all --name); do
            if [ -n "$net" ]; then
                echo "--- Network: $net ---"
                sudo virsh net-info "$net" 2>&1 || true
                echo ""
                echo "DHCP Leases:"
                sudo virsh net-dhcp-leases "$net" 2>&1 || true
                echo ""
            fi
        done
    } > "${OUTPUT_DIR}/libvirt/networks.txt" 2>&1
}

collect_libvirt_network_xml() {
    info "Collecting network XML definitions..."
    # Only collect enclave/eci networks
    for net in $(sudo virsh net-list --all --name | grep -E 'enclave|eci'); do
        if [ -n "$net" ]; then
            sudo virsh net-dumpxml "$net" 2>&1 | sudo tee "${OUTPUT_DIR}/libvirt/network-${net}.xml" >/dev/null || true
        fi
    done
}

collect_libvirt_storage() {
    info "Collecting storage pool information..."
    {
        echo "=== Storage Pools ==="
        sudo virsh pool-list --all
        echo ""
        for pool in $(sudo virsh pool-list --all --name); do
            if [ -n "$pool" ]; then
                echo "--- Pool: $pool ---"
                sudo virsh pool-info "$pool" 2>&1 || true
                echo ""
            fi
        done
    } > "${OUTPUT_DIR}/libvirt/storage.txt" 2>&1
}

#####################################
# NETWORK COLLECTION FUNCTIONS
#####################################

collect_network_interfaces() {
    info "Collecting network interface configuration..."
    {
        echo "=== Network Interfaces ==="
        ip addr show
        echo ""
        echo "=== Link Status ==="
        ip link show
        echo ""
        echo "=== Routes ==="
        ip route show
        echo ""
        echo "=== Routing Tables ==="
        ip route show table all
    } > "${OUTPUT_DIR}/network/interfaces.txt" 2>&1
}

collect_network_bridges() {
    info "Collecting bridge information..."
    {
        echo "=== Bridges ==="
        ip link show type bridge
        echo ""
        echo "=== Bridge Details ==="
        for br in $(ip link show type bridge | grep -o '^[0-9]*: [^:]*' | cut -d: -f2 | tr -d ' '); do
            echo "--- Bridge: $br ---"
            ip addr show "$br" 2>&1 || true
            echo ""
            echo "Bridge Links:"
            bridge link show dev "$br" 2>&1 || true
            echo ""
            echo "Bridge FDB:"
            bridge fdb show dev "$br" 2>&1 || true
            echo ""
        done
    } > "${OUTPUT_DIR}/network/bridges.txt" 2>&1
}

collect_network_firewall() {
    info "Collecting firewall configuration..."
    {
        echo "=== Firewall Status ==="
        sudo systemctl status firewalld --no-pager || echo "firewalld not running"
        echo ""
        echo "=== Firewall Zones ==="
        sudo firewall-cmd --list-all-zones 2>&1 || true
        echo ""
        echo "=== iptables Filter Rules ==="
        sudo iptables -L -n -v 2>&1 || true
        echo ""
        echo "=== iptables NAT Rules ==="
        sudo iptables -t nat -L -n -v 2>&1 || true
    } > "${OUTPUT_DIR}/network/firewall.txt" 2>&1
}

collect_network_dns() {
    info "Collecting DNS configuration..."
    {
        echo "=== /etc/resolv.conf ==="
        cat /etc/resolv.conf
        echo ""
        echo "=== DNS Test (google.com) ==="
        nslookup google.com || dig google.com || echo "DNS tools not available"
        echo ""
        echo "=== dnsmasq Status ==="
        sudo systemctl status dnsmasq --no-pager 2>&1 || echo "dnsmasq not running"
        echo ""
        if sudo systemctl is-active dnsmasq >/dev/null 2>&1; then
            echo "=== dnsmasq Configuration ==="
            sudo cat /etc/dnsmasq.conf 2>/dev/null || true
            echo ""
            echo "=== dnsmasq Logs ==="
            sudo journalctl -u dnsmasq -n 100 --no-pager 2>&1 || true
        else
            echo "NOTE: dnsmasq not running is normal - DNS resolution typically handled by:"
            echo "  - systemd-resolved (127.0.0.1:53)"
            echo "  - NetworkManager dnsmasq plugin"
            echo "  - libvirt dnsmasq (for VM networks)"
        fi
    } > "${OUTPUT_DIR}/network/dns.txt" 2>&1
}

#####################################
# DEV-SCRIPTS COLLECTION FUNCTIONS
#####################################

collect_devscripts_config() {
    info "Collecting dev-scripts configuration..."

    if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
        warn "DEV_SCRIPTS_PATH not set, skipping dev-scripts collection"
        return
    fi

    if [ ! -d "$DEV_SCRIPTS_PATH" ]; then
        warn "dev-scripts not found at $DEV_SCRIPTS_PATH"
        return
    fi

    # Collect only active cluster config (not all historical configs)
    if [ -n "${ENCLAVE_CLUSTER_NAME:-}" ]; then
        local cluster_config="$DEV_SCRIPTS_PATH/config_${ENCLAVE_CLUSTER_NAME}.sh"
        if [ -f "$cluster_config" ]; then
            cp "$cluster_config" "${OUTPUT_DIR}/dev-scripts/" 2>&1 || true
        else
            warn "Active cluster config not found: $cluster_config"
        fi
    fi

    # Also collect config_example.sh for reference
    if [ -f "$DEV_SCRIPTS_PATH/config_example.sh" ]; then
        cp "$DEV_SCRIPTS_PATH/config_example.sh" "${OUTPUT_DIR}/dev-scripts/" 2>&1 || true
    fi

    # Collect only active cluster environment file
    if [ -n "${WORKING_DIR:-}" ] && [ -d "$WORKING_DIR" ]; then
        if [ -n "${ENCLAVE_CLUSTER_NAME:-}" ]; then
            local cluster_env="$WORKING_DIR/environment-${ENCLAVE_CLUSTER_NAME}.json"
            if [ -f "$cluster_env" ]; then
                cp "$cluster_env" "${OUTPUT_DIR}/dev-scripts/environment.json" 2>/dev/null || true
                info "Collected environment file: $cluster_env"
            else
                warn "Cluster environment file not found: $cluster_env"
            fi
        fi
        # Fallback: collect most recent environment.json
        if [ ! -f "${OUTPUT_DIR}/dev-scripts/environment.json" ]; then
            local latest_env
            latest_env=$(find "$WORKING_DIR" -maxdepth 1 -name "environment*.json" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
            if [ -n "$latest_env" ] && [ -f "$latest_env" ]; then
                cp "$latest_env" "${OUTPUT_DIR}/dev-scripts/environment.json" 2>/dev/null || true
                info "Collected most recent environment file: $latest_env"
            else
                warn "No environment files found in $WORKING_DIR"
            fi
        fi
    else
        warn "WORKING_DIR not set or does not exist: ${WORKING_DIR:-not set}"
    fi
}

collect_bmc_status() {
    info "Collecting BMC emulation status..."

    # Determine BMC endpoint from environment.json
    local bmc_endpoint=""
    local env_file=""

    # Try to find environment.json in multiple locations
    if [ -n "${ENCLAVE_CLUSTER_NAME:-}" ] && [ -n "${WORKING_DIR:-}" ]; then
        # New structure: cluster-specific working directory
        local cluster_env="$WORKING_DIR/environment-${ENCLAVE_CLUSTER_NAME}.json"
        if [ -f "$cluster_env" ]; then
            env_file="$cluster_env"
        fi
    fi

    # Fallback: search in WORKING_DIR
    if [ -z "$env_file" ] && [ -n "${WORKING_DIR:-}" ]; then
        env_file=$(find "$WORKING_DIR" -maxdepth 1 -name "environment*.json" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    fi

    # Extract BMC endpoint if we found the file
    if [ -n "$env_file" ] && [ -f "$env_file" ]; then
        bmc_endpoint=$(jq -r '.bmc_emulation.sushy_tools.endpoint // empty' "$env_file" 2>/dev/null || true)
        if [ -n "$bmc_endpoint" ]; then
            info "Found BMC endpoint from $env_file: $bmc_endpoint"
        fi
    fi

    # Fallback to common default if not found
    if [ -z "$bmc_endpoint" ]; then
        bmc_endpoint="http://100.64.1.1:8000"
        warn "Could not determine BMC endpoint from environment.json, using default: $bmc_endpoint"
    fi

    {
        echo "=== Sushy-tools Service Status ==="
        sudo systemctl status sushy-emulator --no-pager 2>&1 || echo "sushy-emulator service not found"
        echo ""
        echo "=== Sushy-tools Containers ==="
        sudo podman ps -a --filter "name=sushy-tools" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}" 2>&1 || echo "No sushy-tools containers found"
        echo ""
        echo "=== Sushy-tools Processes ==="
        ps aux | grep -i sushy | grep -v grep || echo "No sushy processes found"
        echo ""
        echo "=== BMC Endpoint Test ($bmc_endpoint) ==="
        # Try HTTPS first, fallback to HTTP
        if ! curl -k -m 5 "${bmc_endpoint/http:/https:}/redfish/v1/Systems" 2>&1; then
            echo "HTTPS failed, trying HTTP..."
            curl -k -m 5 "${bmc_endpoint}/redfish/v1/Systems" 2>&1 || echo "BMC endpoint not accessible"
        fi
        echo ""
        echo "=== Sushy Service Logs ==="
        sudo journalctl -u sushy-emulator -n 100 --no-pager 2>&1 || echo "No sushy service logs"
        echo ""
        echo "=== Sushy Container Logs ==="
        # Collect logs from all sushy-tools containers (cluster-specific naming)
        for container in $(sudo podman ps -a --filter "name=sushy-tools" --format "{{.Names}}" 2>/dev/null); do
            echo "--- Container: $container ---"
            sudo podman logs "$container" 2>&1 || echo "Could not get logs for $container"
            echo ""
        done
        if ! sudo podman ps -a --filter "name=sushy-tools" --format "{{.Names}}" 2>/dev/null | grep -q .; then
            echo "No sushy-tools containers found"
        fi
    } > "${OUTPUT_DIR}/dev-scripts/bmc-status.txt" 2>&1
}

#####################################
# LANDING ZONE COLLECTION FUNCTIONS
#####################################

get_landing_zone_ip() {
    local lz_ip=""

    if [ -n "${WORKING_DIR:-}" ]; then
        local env_file
        env_file=$(ls -t "$WORKING_DIR"/environment*.json 2>/dev/null | head -1)
        if [ -f "$env_file" ]; then
            lz_ip=$(jq -r '.landing_zone.ip // empty' "$env_file" 2>/dev/null || true)
        fi
    fi

    echo "$lz_ip"
}

test_lz_ssh() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    ssh $ssh_opts cloud-user@"$lz_ip" "echo connected" >/dev/null 2>&1
}

collect_lz_system_info() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    mkdir -p "${OUTPUT_DIR}/landing-zone"
    info "Collecting Landing Zone system information..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        echo '=== OS Info ==='
        cat /etc/os-release
        echo ''
        echo '=== Kernel ==='
        uname -a
        echo ''
        echo '=== Hostname ==='
        hostname
        echo ''
        echo '=== Memory ==='
        free -h
        echo ''
        echo '=== Disk ==='
        df -h
        echo ''
        echo '=== Network Interfaces ==='
        ip addr show
        echo ''
        echo '=== Routes ==='
        ip route show
    " > "${OUTPUT_DIR}/landing-zone/system-info.txt" 2>&1 || warn "Could not collect LZ system info"
}

collect_lz_dns_config() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting Landing Zone DNS configuration..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        echo '=== /etc/resolv.conf ==='
        cat /etc/resolv.conf
        echo ''
        echo '=== DNS Test (google.com) ==='
        nslookup google.com 2>&1 || dig google.com 2>&1 || echo 'DNS tools not available'
        echo ''
        echo '=== DNS Test (quay.io) ==='
        nslookup quay.io 2>&1 || dig quay.io 2>&1 || echo 'DNS tools not available'
    " > "${OUTPUT_DIR}/landing-zone/dns.txt" 2>&1 || warn "Could not collect LZ DNS config"
}

collect_lz_deployment_logs() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    mkdir -p "${OUTPUT_DIR}/landing-zone"
    info "Collecting deployment logs from Landing Zone..."

    # Tar all deployment logs on remote
    ssh $ssh_opts cloud-user@"$lz_ip" "cd /home/cloud-user/enclave && tar czf /tmp/deployment-logs-${TIMESTAMP}.tar.gz deployment_*.log 2>/dev/null" || warn "No deployment logs found"

    # Copy and extract
    if scp $ssh_opts cloud-user@"$lz_ip":/tmp/deployment-logs-${TIMESTAMP}.tar.gz "${OUTPUT_DIR}/landing-zone/" 2>/dev/null; then
        (cd "${OUTPUT_DIR}/landing-zone" && tar xzf deployment-logs-${TIMESTAMP}.tar.gz && rm deployment-logs-${TIMESTAMP}.tar.gz) || true
    fi
}

collect_lz_config_files() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting configuration files from Landing Zone..."

    # NOTE: vars.yaml is NOT collected here due to credential exposure risk
    # It contains pull secrets and Quay admin password in plain text
    # For debugging, check cluster logs or use must-gather script which sanitizes it

    # OpenShift install log
    scp $ssh_opts cloud-user@"$lz_ip":/home/cloud-user/ocp-cluster/.openshift_install.log "${OUTPUT_DIR}/landing-zone/" 2>/dev/null || warn "Could not collect .openshift_install.log"

    # agent-based installer log
    scp $ssh_opts cloud-user@"$lz_ip":/home/cloud-user/.openshift_install.log "${OUTPUT_DIR}/landing-zone/openshift_install_agent.log" 2>/dev/null || true
}

collect_lz_services() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting service status from Landing Zone..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        echo '=== Failed Services ==='
        systemctl list-units --type=service --state=failed
        echo ''
        echo '=== httpd Status ==='
        systemctl status httpd --no-pager 2>&1 || echo 'httpd not running'
        echo ''
        echo '=== dnsmasq Status ==='
        systemctl status dnsmasq --no-pager 2>&1 || echo 'dnsmasq not running'
        echo ''
        echo '=== Podman Containers ==='
        podman ps -a
        echo ''
        echo '=== Podman Images ==='
        podman images | head -20
    " > "${OUTPUT_DIR}/landing-zone/services.txt" 2>&1 || warn "Could not collect LZ services"
}

collect_lz_registry() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting registry information from Landing Zone..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        echo '=== Quay Registry Container ==='
        podman ps -a | grep -i quay || echo 'No Quay containers found'
        echo ''
        echo '=== Registry Logs (last 100 lines) ==='
        podman logs quay-registry 2>&1 | tail -100 || echo 'Registry not running'
        echo ''
        echo '=== Registry Storage ==='
        du -sh /var/lib/containers/storage 2>&1 || true
    " > "${OUTPUT_DIR}/landing-zone/registry.txt" 2>&1 || warn "Could not collect registry info"
}

#####################################
# CLUSTER COLLECTION FUNCTIONS
#####################################

check_kubeconfig_exists() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    ssh $ssh_opts cloud-user@"$lz_ip" "test -f /home/cloud-user/ocp-cluster/auth/kubeconfig" 2>/dev/null
}

collect_cluster_kubeconfig() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    mkdir -p "${OUTPUT_DIR}/cluster"
    info "Collecting kubeconfig..."
    scp $ssh_opts cloud-user@"$lz_ip":/home/cloud-user/ocp-cluster/auth/kubeconfig "${OUTPUT_DIR}/cluster/" 2>/dev/null || warn "Could not collect kubeconfig"
}

collect_cluster_status() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting cluster status..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig

        echo '=== Cluster Version ==='
        oc get clusterversion -o yaml
        echo ''
        echo '=== Nodes ==='
        oc get nodes -o wide
        echo ''
        echo '=== Node Conditions ==='
        oc get nodes -o json | jq -r '.items[] | \"\\(.metadata.name): \\(.status.conditions[] | select(.status==\\\"True\\\") | .type)\"'
        echo ''
        echo '=== Cluster Operators ==='
        oc get co
        echo ''
        echo '=== Degraded Operators ==='
        oc get co -o json | jq -r '.items[] | select(.status.conditions[] | select(.type==\"Degraded\" and .status==\"True\")) | .metadata.name'
        echo ''
        echo '=== Machine Config Pools ==='
        oc get mcp
    " > "${OUTPUT_DIR}/cluster/cluster-status.txt" 2>&1 || warn "Could not collect cluster status"
}

collect_cluster_events() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting cluster events..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig

        echo '=== Recent Events (last 100) ==='
        oc get events --all-namespaces --sort-by='.lastTimestamp' | tail -100
        echo ''
        echo '=== Warning Events ==='
        oc get events --all-namespaces --field-selector type=Warning --sort-by='.lastTimestamp' | tail -50
    " > "${OUTPUT_DIR}/cluster/events.txt" 2>&1 || warn "Could not collect cluster events"
}

collect_cluster_pods() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting pod information..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig

        echo '=== All Pods ==='
        oc get pods --all-namespaces -o wide
        echo ''
        echo '=== Pods Not Running/Succeeded ==='
        oc get pods --all-namespaces --field-selector status.phase!=Running,status.phase!=Succeeded
        echo ''
        echo '=== Pending Pods ==='
        oc get pods --all-namespaces --field-selector status.phase=Pending
        echo ''
        echo '=== Failed Pods ==='
        oc get pods --all-namespaces --field-selector status.phase=Failed
    " > "${OUTPUT_DIR}/cluster/pods.txt" 2>&1 || warn "Could not collect pod info"
}

collect_cluster_problem_pod_logs() {
    local lz_ip="$1"
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -q"

    info "Collecting logs from problem pods..."
    ssh $ssh_opts cloud-user@"$lz_ip" "
        export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig
        mkdir -p /tmp/problem-pods-${TIMESTAMP}

        # Get pods that are not Running/Succeeded
        oc get pods --all-namespaces --field-selector status.phase!=Running,status.phase!=Succeeded -o json | \
        jq -r '.items[] | \"\\(.metadata.namespace)/\\(.metadata.name)\"' | \
        head -20 | \
        while read pod; do
            ns=\${pod%/*}
            name=\${pod#*/}
            echo \"Collecting logs for \$ns/\$name\"
            oc logs -n \$ns \$name --all-containers --prefix --tail=500 > /tmp/problem-pods-${TIMESTAMP}/\${ns}_\${name}.log 2>&1 || true
            oc describe pod -n \$ns \$name > /tmp/problem-pods-${TIMESTAMP}/\${ns}_\${name}_describe.txt 2>&1 || true
        done

        # Tar it up
        cd /tmp && tar czf problem-pods-${TIMESTAMP}.tar.gz problem-pods-${TIMESTAMP}/ 2>/dev/null
    " 2>&1 || warn "Could not collect problem pod logs"

    # Copy and extract
    if scp $ssh_opts cloud-user@"$lz_ip":/tmp/problem-pods-${TIMESTAMP}.tar.gz "${OUTPUT_DIR}/cluster/" 2>/dev/null; then
        (cd "${OUTPUT_DIR}/cluster" && tar xzf problem-pods-${TIMESTAMP}.tar.gz && rm problem-pods-${TIMESTAMP}.tar.gz) || true
    fi
}

#####################################
# COLLECTION LEVEL ORCHESTRATION
#####################################

collect_basic() {
    info "=== BASIC COLLECTION ==="
    collect_system_info
    collect_system_processes
    info "✓ Basic collection complete"
}

collect_infra() {
    info "=== INFRASTRUCTURE COLLECTION ==="
    collect_system_performance
    collect_system_logs
    collect_libvirt_vms
    collect_libvirt_vm_xml
    collect_libvirt_networks
    collect_libvirt_network_xml
    collect_libvirt_storage
    collect_network_interfaces
    collect_network_bridges
    collect_network_firewall
    collect_network_dns
    collect_devscripts_config
    collect_bmc_status
    info "✓ Infrastructure collection complete"
}

collect_deployment() {
    info "=== DEPLOYMENT COLLECTION ==="

    local lz_ip
    lz_ip=$(get_landing_zone_ip)

    if [ -z "$lz_ip" ]; then
        warn "Landing Zone IP not found, skipping LZ collection"
        return
    fi

    info "Landing Zone IP: $lz_ip"

    if ! test_lz_ssh "$lz_ip"; then
        warn "Cannot SSH to Landing Zone, skipping LZ collection"
        return
    fi

    collect_lz_system_info "$lz_ip"
    collect_lz_dns_config "$lz_ip"
    collect_lz_deployment_logs "$lz_ip"
    collect_lz_config_files "$lz_ip"
    collect_lz_services "$lz_ip"
    collect_lz_registry "$lz_ip"

    info "✓ Deployment collection complete"
}

collect_full() {
    info "=== FULL COLLECTION (includes cluster) ==="

    local lz_ip
    lz_ip=$(get_landing_zone_ip)

    if [ -z "$lz_ip" ]; then
        warn "Landing Zone IP not found, skipping cluster collection"
        return
    fi

    if ! test_lz_ssh "$lz_ip"; then
        warn "Cannot SSH to Landing Zone, skipping cluster collection"
        return
    fi

    if ! check_kubeconfig_exists "$lz_ip"; then
        warn "Kubeconfig not found, skipping cluster collection"
        return
    fi

    collect_cluster_kubeconfig "$lz_ip"
    collect_cluster_status "$lz_ip"
    collect_cluster_events "$lz_ip"
    collect_cluster_pods "$lz_ip"
    collect_cluster_problem_pod_logs "$lz_ip"

    info "✓ Full collection complete"
}

#####################################
# MAIN EXECUTION
#####################################

case "$LEVEL" in
    basic)
        collect_basic
        ;;
    infra)
        collect_basic
        collect_infra
        ;;
    deployment)
        collect_basic
        collect_infra
        collect_deployment
        ;;
    full)
        collect_basic
        collect_infra
        collect_deployment
        collect_full
        ;;
    *)
        error "Invalid level: $LEVEL"
        echo "Valid levels: basic, infra, deployment, full"
        exit 1
        ;;
esac

# Create summary
{
    echo "=== Artifact Collection Summary ==="
    echo "Timestamp: $TIMESTAMP"
    echo "Level: $LEVEL"
    echo "Output Directory: $OUTPUT_DIR"
    echo ""
    echo "=== Collection Status ==="
    if [ "$COLLECTION_ERRORS" -eq 0 ] && [ "$COLLECTION_WARNINGS" -eq 0 ]; then
        echo "✓ All collections successful"
    else
        [ "$COLLECTION_WARNINGS" -gt 0 ] && echo "⚠️  Warnings: $COLLECTION_WARNINGS"
        [ "$COLLECTION_ERRORS" -gt 0 ] && echo "❌ Errors: $COLLECTION_ERRORS"
    fi
    echo ""
    echo "=== Collected Files ==="
    find "$OUTPUT_DIR" -type f -exec ls -lh {} \; | awk '{print $9, $5}' | sort
    echo ""
    echo "=== Total Size ==="
    du -sh "$OUTPUT_DIR"
    echo ""
    echo "=== Directory Structure ==="
    tree -L 2 "$OUTPUT_DIR" 2>/dev/null || find "$OUTPUT_DIR" -type d | sort
} > "${OUTPUT_DIR}/collection-summary.txt"

info "✓ Artifact collection complete"
if [ "$COLLECTION_WARNINGS" -gt 0 ]; then
    warn "Collection completed with $COLLECTION_WARNINGS warning(s)"
fi
if [ "$COLLECTION_ERRORS" -gt 0 ]; then
    error "Collection completed with $COLLECTION_ERRORS error(s)"
fi
info "Summary: ${OUTPUT_DIR}/collection-summary.txt"

# Print summary to stdout
cat "${OUTPUT_DIR}/collection-summary.txt"

# Note: Exit 0 even with warnings/errors - artifact collection is best-effort
# The actual CI job should fail based on test results, not artifact collection
exit 0

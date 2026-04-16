#!/bin/bash
# Generate Enclave Lab config/global.yaml, config/certificates.yaml and
# config/cloud_infra.yaml from infrastructure metadata
#
# This script reads environment.json (created in Task 1) and generates
# config/global.yaml, config/certificates.yaml and config/cloud_infra.yaml
# configuration files for Enclave Lab deployment.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

# Validate required environment variables
require_env_var "DEV_SCRIPTS_PATH"
require_command "jq"

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration
load_devscripts_config

# Configuration
ensure_working_dir
CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Try cluster-specific environment file first, fall back to legacy location
ENVIRONMENT_JSON="${WORKING_DIR}/environment-${CLUSTER_NAME}.json"
if [ ! -f "$ENVIRONMENT_JSON" ]; then
    ENVIRONMENT_JSON="${WORKING_DIR}/environment.json"
fi

GLOBAL_VARS_OUTPUT="${WORKING_DIR}/config/global.yaml"
CERTS_VARS_OUTPUT="${WORKING_DIR}/config/certificates.yaml"
CLOUD_INFRA_VARS_OUTPUT="${WORKING_DIR}/config/cloud_infra.yaml"

info "Generating Enclave Lab configuration from infrastructure metadata..."

# Check if environment.json exists
require_file "$ENVIRONMENT_JSON" "Environment metadata not found: $ENVIRONMENT_JSON - Run 'make environment' first to create infrastructure"

# Extract information from environment.json
BMC_CIDR=$(jq -r '.networks.bmc.cidr' "$ENVIRONMENT_JSON")
CLUSTER_CIDR=$(jq -r '.networks.cluster.cidr' "$ENVIRONMENT_JSON")
BMC_ENDPOINT=$(jq -r '.bmc_emulation.sushy_tools.endpoint' "$ENVIRONMENT_JSON")

# Extract BMC base URI (host:port) from full endpoint URL
# BMC_ENDPOINT is like "http://100.64.1.1:8000", we need "100.64.1.1:8000"
BMC_BASEURI=$(echo "$BMC_ENDPOINT" | sed 's|http://||' | sed 's|https://||')

# Calculate network parameters
CLUSTER_PREFIX=$(echo "$CLUSTER_CIDR" | sed 's|.*/||')
CLUSTER_NETWORK=$(echo "$CLUSTER_CIDR" | sed 's|/.*||')
CLUSTER_GATEWAY=$(echo "$CLUSTER_NETWORK" | awk -F. '{print $1"."$2"."$3".1"}')

# Calculate VIPs (use higher IPs to avoid conflicts)
API_VIP=$(echo "$CLUSTER_NETWORK" | awk -F. '{print $1"."$2"."$3".100"}')
INGRESS_VIP=$(echo "$CLUSTER_NETWORK" | awk -F. '{print $1"."$2"."$3".101"}')

# Calculate Landing Zone BMC IP (.2 in BMC network)
LZ_BMC_IP=$(echo "$BMC_CIDR" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".2"}')

# Get master count
MASTER_COUNT=$(jq -r '.vms.masters | length' "$ENVIRONMENT_JSON")

# Get base domain from config or use default
BASE_DOMAIN="${CLUSTER_DOMAIN:-${CLUSTER_NAME}.lab}"

# Get first master IP as rendezvous IP (bootstrap) before generating config
RENDEZVOUS_IP=$(jq -r '.vms.masters[0].networks.cluster.ip' "$ENVIRONMENT_JSON")
if [ "$RENDEZVOUS_IP" = "null" ] || [ -z "$RENDEZVOUS_IP" ] || [ "$RENDEZVOUS_IP" = "unknown" ]; then
    # If IP not available, calculate it (starting from .20 to avoid conflicts with VIPs and LZ)
    RENDEZVOUS_IP=$(echo "$CLUSTER_NETWORK" | awk -F. '{print $1"."$2"."$3".20"}')
fi

info "Configuration parameters:"
info "  Cluster Name: $CLUSTER_NAME"
info "  Base Domain: $BASE_DOMAIN"
info "  Cluster Network: $CLUSTER_CIDR"
info "  BMC Network: $BMC_CIDR"
info "  API VIP: $API_VIP"
info "  Ingress VIP: $INGRESS_VIP"
info "  Rendezvous IP: $RENDEZVOUS_IP"
info "  Masters: $MASTER_COUNT"

# Generate config/global.yaml
mkdir -p "$(dirname "$GLOBAL_VARS_OUTPUT")"
cat > "$GLOBAL_VARS_OUTPUT" <<EOF
---
# Enclave Lab Configuration
# Auto-generated from infrastructure metadata
# Generated: $(date -Iseconds)

# ============================================================================
# Base Configuration
# ============================================================================
workingDir: "/home/cloud-user"

# ============================================================================
# Network Configuration
# ============================================================================
# Cluster Network Settings (install-config.yaml parameters)
baseDomain: ${BASE_DOMAIN}
clusterName: ${CLUSTER_NAME}
machineNetwork: ${CLUSTER_CIDR}
apiVIP: ${API_VIP}
ingressVIP: ${INGRESS_VIP}

# Network Infrastructure
defaultDNS: ${CLUSTER_GATEWAY}
defaultGateway: ${CLUSTER_GATEWAY}
defaultPrefix: ${CLUSTER_PREFIX}

# First control plane node IP (in the machineNetwork)
rendezvousIP: ${RENDEZVOUS_IP}

# LZ IP where HTTP server serves the installation ISO to IPMI nodes
lzBmcIP: ${LZ_BMC_IP}

# ============================================================================
# Quay Registry Configuration
# ============================================================================
quayUser: quayadmin
quayPassword: SuperPrivate123!

# ============================================================================
# Quay Backend Storage Configuration
# ============================================================================
# Option 1: External S3/RadosGW storage (RECOMMENDED for production)
# Option 2: Local storage (NOT recommended for production)
quayBackend: LocalStorage
quayBackendLocalStorageConfiguration:
  storage_path: /datastorage/registry

# ============================================================================
# Storage Backend
# ============================================================================
# Storage backend for block devices for quay database and assisted installer
# Options: lvms or odf
storage_plugin: lvms

# ============================================================================
# Storage Configuration (LVMS) (optional)
# ============================================================================
lvmsConfig: {}

# ============================================================================
# OpenShift Deployment Configuration (optional)
# ============================================================================
# Leaving default values

# ============================================================================
# Pull Secret and SSH Public Key Configuration
# ============================================================================
# Obtain from: https://console.redhat.com/openshift/install/pull-secret
# Pull secret will be read from pullSecretPath
pullSecret:
  auths: {}
pullSecretPath: "{{ workingDir }}/config/pull-secret.json"

# SSH public key path for cluster nodes
sshPubPath: "{{ workingDir }}/.ssh/id_rsa.pub"

# ============================================================================
# Cluster Hosts Configuration (Agent Hosts)
# ============================================================================
agent_hosts:
EOF

# Add each master VM to agent_hosts
for i in $(seq 0 $((MASTER_COUNT - 1))); do
    info "Processing master $i..."

    # Get master information from environment.json
    MASTER_NAME=$(jq -r ".vms.masters[$i].name" "$ENVIRONMENT_JSON")
    MASTER_MAC=$(jq -r ".vms.masters[$i].networks.cluster.mac" "$ENVIRONMENT_JSON")
    MASTER_IP=$(jq -r ".vms.masters[$i].networks.cluster.ip" "$ENVIRONMENT_JSON")

    # Get libvirt domain UUID (sushy-tools uses libvirt UUIDs as system IDs)
    MASTER_UUID=$(sudo virsh domuuid "$MASTER_NAME" 2>/dev/null || echo "")
    if [ -z "$MASTER_UUID" ]; then
        error "Could not get UUID for VM: $MASTER_NAME"
        exit 1
    fi

    # Redfish base URI (host:port only, no protocol or path)
    # sushy-tools uses libvirt domain UUID as system ID in the path

    # If IP not available from DHCP, calculate it (starting from .20)
    if [ "$MASTER_IP" = "null" ] || [ -z "$MASTER_IP" ] || [ "$MASTER_IP" = "unknown" ]; then
        MASTER_IP=$(echo "$CLUSTER_NETWORK" | awk -F. -v i=$i '{print $1"."$2"."$3"."20+i}')
    fi

    # Normalize master name (remove cluster prefix if present)
    MASTER_SHORT_NAME="master-$(printf "%02d" $i)"

    # Default root disk for libvirt VMs
    ROOT_DISK="/dev/vda"

    cat >> "$GLOBAL_VARS_OUTPUT" <<EOF
  - name: ${CLUSTER_NAME}-${MASTER_SHORT_NAME}
    macAddress: ${MASTER_MAC}
    ipAddress: ${MASTER_IP}
    redfish: ${BMC_BASEURI}
    bmcSystemId: ${MASTER_UUID}
    rootDisk: "${ROOT_DISK}"
    redfishUser: admin
    redfishPassword: password
EOF
done

# Generate self-signed SSL certificates for CI
CLUSTER_FQDN="${CLUSTER_NAME}.${BASE_DOMAIN}"
CERT_DIR=$(mktemp -d)

info "Generating self-signed SSL certificates for ${CLUSTER_FQDN}..."

# Generate CA
openssl req -x509 -newkey rsa:2048 -keyout "${CERT_DIR}/ca.key" -out "${CERT_DIR}/ca.crt" \
    -days 365 -nodes -subj "/CN=${CLUSTER_FQDN} CA" 2>/dev/null

# Generate API certificate (api.<cluster>.<domain>)
openssl req -newkey rsa:2048 -keyout "${CERT_DIR}/api.key" -out "${CERT_DIR}/api.csr" \
    -nodes -subj "/CN=api.${CLUSTER_FQDN}" \
    -addext "subjectAltName=DNS:api.${CLUSTER_FQDN}" 2>/dev/null
openssl x509 -req -in "${CERT_DIR}/api.csr" -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" \
    -CAcreateserial -out "${CERT_DIR}/api.crt" -days 365 \
    -copy_extensions copyall 2>/dev/null

# Generate Ingress certificate (*.apps.<cluster>.<domain>)
openssl req -newkey rsa:2048 -keyout "${CERT_DIR}/ingress.key" -out "${CERT_DIR}/ingress.csr" \
    -nodes -subj "/CN=*.apps.${CLUSTER_FQDN}" \
    -addext "subjectAltName=DNS:*.apps.${CLUSTER_FQDN}" 2>/dev/null
openssl x509 -req -in "${CERT_DIR}/ingress.csr" -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" \
    -CAcreateserial -out "${CERT_DIR}/ingress.crt" -days 365 \
    -copy_extensions copyall 2>/dev/null

# Read cert contents for YAML embedding
API_KEY=$(cat "${CERT_DIR}/api.key")
API_CERT=$(cat "${CERT_DIR}/api.crt")
INGRESS_KEY=$(cat "${CERT_DIR}/ingress.key")
INGRESS_CERT=$(cat "${CERT_DIR}/ingress.crt")
CA_CERT=$(cat "${CERT_DIR}/ca.crt")

rm -rf "${CERT_DIR}"

# Generate config/certificates.yaml file
cat > "$CERTS_VARS_OUTPUT" <<EOF
---
# SSL Certificates Configuration
# Auto-generated self-signed certificates for CI/testing
# Generated: $(date -Iseconds)

# Note: For production, replace with valid certificates
# API Certificate for api.${CLUSTER_FQDN}
sslAPICertificateKey: |
$(echo "$API_KEY" | sed 's/^/  /')

sslAPICertificateFullChain: |
$(echo "$API_CERT" | sed 's/^/  /')

# Ingress Certificate for *.apps.${CLUSTER_FQDN}
sslIngressCertificateKey: |
$(echo "$INGRESS_KEY" | sed 's/^/  /')

sslIngressCertificateFullChain: |
$(echo "$INGRESS_CERT" | sed 's/^/  /')

# Root CA Certificate
sslCACertificate: |
$(echo "$CA_CERT" | sed 's/^/  /')
EOF

info "✓ Self-signed certificates generated for api.${CLUSTER_FQDN} and *.apps.${CLUSTER_FQDN}"

# Generate config/cloud_infra.yaml file
cat > "$CLOUD_INFRA_VARS_OUTPUT" <<EOF
---
# Cloud Infrastructure Configuration
# Auto-generated from infrastructure metadata
# Generated: $(date -Iseconds)

# ============================================================================
# Discovery Hosts Configuration
# ============================================================================
# Discovery hosts for cloud infrastructure (CaaS)
# These are worker nodes that will be discovered and added to the cluster
# Set to an empty list if no discovery hosts are needed:
discovery_hosts: []
EOF

info "✓ Configuration files generated: $GLOBAL_VARS_OUTPUT, $CERTS_VARS_OUTPUT and $CLOUD_INFRA_VARS_OUTPUT"
echo ""
info "Configuration summary:"
info "  Global vars file: $GLOBAL_VARS_OUTPUT"
info "  Certificates vars file: $CERTS_VARS_OUTPUT"
info "  Cloud infra vars file: $CLOUD_INFRA_VARS_OUTPUT"
info "  Cluster: $CLUSTER_NAME.$BASE_DOMAIN"
info "  Network: $CLUSTER_CIDR"
info "  Masters: $MASTER_COUNT nodes"
info "  API VIP: $API_VIP"
info "  Ingress VIP: $INGRESS_VIP"
info "  Rendezvous IP: $RENDEZVOUS_IP"
echo ""
# Calculate worker IP range for informational message
WORKER_IP_START=$(echo "$CLUSTER_NETWORK" | awk -F. '{print $1"."$2"."$3".20"}')
WORKER_IP_END=$(echo "$CLUSTER_NETWORK" | awk -F. -v count=$MASTER_COUNT '{print $1"."$2"."$3"."20+count-1}')
info "Generated configuration uses:"
info "  - Worker IPs: ${WORKER_IP_START}-${WORKER_IP_END} (will be assigned during deployment)"
info "  - Storage: LVMS with /dev/vda root disk"
info "  - Registry: LocalStorage"
info "  - Pull secret: Embedded in config/global.yaml (written to pullSecretPath at runtime)"
info "  - SSL certificates: Self-signed (generated for CI/testing)"
echo ""
info "Review config/global.yaml, config/certificates.yaml and config/cloud_infra.yaml"
info "and adjust if needed before running Enclave Lab"
echo ""

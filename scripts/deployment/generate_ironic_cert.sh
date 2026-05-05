#!/bin/bash
# Generate a TLS certificate for the Ironic ISO server, signed by the CA
# produced by generate_ironic_ca.sh
#
# Reads lzBmcIP from config/global.yaml on the Landing Zone to set the
# certificate SAN. Key and CSR are generated locally on the CI runner and
# the CSR is signed with the CA key present in the ironic-ca working directory.
#
# Exports ENCLAVE_IRONIC_CERT and ENCLAVE_IRONIC_KEY to GITHUB_ENV for
# use by subsequent workflow steps (picked up by deploy_phase.sh).
#
# Usage: ./generate_ironic_cert.sh
# Required env: DEV_SCRIPTS_PATH, WORKING_DIR

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

require_env_var "DEV_SCRIPTS_PATH"
require_env_var "WORKING_DIR"

IRONIC_CA_DIR="${WORKING_DIR}/ironic-ca"
CA_KEY="${IRONIC_CA_DIR}/ca.key"
CA_CRT="${IRONIC_CA_DIR}/ca.crt"

if [ ! -f "$CA_KEY" ] || [ ! -f "$CA_CRT" ]; then
    error "CA key not found at ${CA_KEY} or CA cert not found at ${CA_CRT}"
    error "Run generate_ironic_ca.sh before this script"
    exit 1
fi

# Determine cluster name for SSH access
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration
load_devscripts_config

LZ_VM_NAME="${ENCLAVE_CLUSTER_NAME}_landingzone_0"

# Get Landing Zone IP
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"
LZ_IP=$(get_vm_ip_on_network "$LZ_VM_NAME" "$CLUSTER_NETWORK")

if [ -z "$LZ_IP" ]; then
    error "Could not determine Landing Zone IP address"
    exit 1
fi

setup_ssh_config "$LZ_IP"

if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone at $LZ_IP"
    exit 1
fi

# Read lzBmcIP and optional lzBmcHostname from config/global.yaml on the Landing Zone
LZ_BMC_IP=$(ssh_exec "awk '/^lzBmcIP:/ {print \$2}' ${LZ_ENCLAVE_DIR}/config/global.yaml")

if [ -z "$LZ_BMC_IP" ]; then
    error "Could not read lzBmcIP from config/global.yaml on Landing Zone"
    exit 1
fi

LZ_BMC_HOSTNAME=$(ssh_exec "awk '/^lzBmcHostname:/ {print \$2}' ${LZ_ENCLAVE_DIR}/config/global.yaml" 2>/dev/null || echo "")

info "Generating TLS certificate for Ironic ISO server"

# Work in a temp directory; cleaned up on exit
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# Generate server key and CSR
openssl genrsa -out "${WORK_DIR}/server.key" 2048 2>/dev/null
openssl req -new \
    -key "${WORK_DIR}/server.key" \
    -subj "/CN=ironic-iso-server" \
    -out "${WORK_DIR}/server.csr" \
    2>/dev/null

# Write SAN extension - include DNS SAN when lzBmcHostname is configured so that
# publicly trusted (e.g. Let's Encrypt) certs can also be used
if [[ -n "$LZ_BMC_HOSTNAME" ]]; then
    info "SAN: DNS:${LZ_BMC_HOSTNAME},IP:${LZ_BMC_IP}"
    echo "subjectAltName=DNS:${LZ_BMC_HOSTNAME},IP:${LZ_BMC_IP}" > "${WORK_DIR}/san.ext"
else
    info "SAN: IP:${LZ_BMC_IP}"
    echo "subjectAltName=IP:${LZ_BMC_IP}" > "${WORK_DIR}/san.ext"
fi

# Sign with CA; serial file goes into the temp dir to avoid writing to the
# sudo-owned SUSHY_DIR
openssl x509 -req \
    -in "${WORK_DIR}/server.csr" \
    -CA "$CA_CRT" \
    -CAkey "$CA_KEY" \
    -CAserial "${WORK_DIR}/ca.srl" \
    -CAcreateserial \
    -out "${WORK_DIR}/server.crt" \
    -days 1 \
    -extfile "${WORK_DIR}/san.ext" \
    2>/dev/null

CERT_CONTENT=$(cat "${WORK_DIR}/server.crt")
KEY_CONTENT=$(cat "${WORK_DIR}/server.key")

# Export to GITHUB_ENV for subsequent steps, or print for local use
if [ -n "${GITHUB_ENV:-}" ]; then
    {
        echo "ENCLAVE_IRONIC_CERT<<EOF"
        echo "$CERT_CONTENT"
        echo "EOF"
    } >> "$GITHUB_ENV"
    {
        echo "ENCLAVE_IRONIC_KEY<<EOF"
        echo "$KEY_CONTENT"
        echo "EOF"
    } >> "$GITHUB_ENV"
    success "Ironic ISO server certificate exported to GITHUB_ENV"
else
    info "GITHUB_ENV not set - printing certificate to stdout (local use)"
    echo "ENCLAVE_IRONIC_CERT:"
    echo "$CERT_CONTENT"
    echo ""
    echo "ENCLAVE_IRONIC_KEY:"
    echo "$KEY_CONTENT"
fi

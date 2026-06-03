#!/bin/bash
# Create the AAP plugin config file on the Landing Zone
#
# Writes config/plugins/aap.yaml on the Landing Zone so Ansible can load
# aapLicenseFile when the aap plugin is enabled.
#
# The license file is expected to be pre-installed on the Landing Zone at
# /etc/enclave-ci/aap-license.zip by the CI infrastructure team.
#
# Usage: ./create_aap_plugin_config_on_lz.sh
# Environment:
#   DEV_SCRIPTS_PATH      - Path to dev-scripts (required)
#   ENCLAVE_CLUSTER_NAME  - Cluster name (default: enclave-test)
#   AAP_LICENSE_FILE      - License path on the Landing Zone
#                           (default: /etc/enclave-ci/aap-license.zip)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"
source "${ENCLAVE_DIR}/scripts/lib/ssh.sh"

require_env_var "DEV_SCRIPTS_PATH"

ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
load_devscripts_config

CLUSTER_NAME="${CLUSTER_NAME:-enclave-test}"
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"
CLUSTER_IP=$(get_vm_ip_on_network "$LZ_VM_NAME" "$CLUSTER_NETWORK")

if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    exit 1
fi

setup_ssh_config "$CLUSTER_IP"

AAP_LICENSE_FILE="${AAP_LICENSE_FILE:-/etc/enclave-ci/aap-license.zip}"
AAP_PLUGIN_CONFIG="${LZ_ENCLAVE_DIR}/config/plugins/aap.yaml"

info "Writing AAP plugin config to Landing Zone: ${AAP_PLUGIN_CONFIG}"

PLUGIN_CONFIG_DIR="$(dirname "${AAP_PLUGIN_CONFIG}")"
AAP_CONFIG_CONTENT="---
aapLicenseFile: ${AAP_LICENSE_FILE}
"

# shellcheck disable=SC2086,SC2029  # SSH_OPTS needs word splitting; paths expand client-side intentionally
ssh $SSH_OPTS "$LZ_SSH" "mkdir -p ${PLUGIN_CONFIG_DIR}"
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh $SSH_OPTS "$LZ_SSH" "cat > ${AAP_PLUGIN_CONFIG}" <<< "${AAP_CONFIG_CONTENT}"

success "AAP plugin config written: aapLicenseFile: ${AAP_LICENSE_FILE}"

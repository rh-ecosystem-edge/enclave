#!/bin/bash
# Collect OpenSnitch connection logs from the Landing Zone VM.
# Dumps journald output for opensnitchd as JSON and copies it to artifacts/egress/.

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

OUTPUT_DIR="${EGRESS_OUTPUT_DIR:-artifacts/egress}"
mkdir -p "$OUTPUT_DIR"

info "Collecting OpenSnitch logs from Landing Zone ($CLUSTER_IP)..."

LOCAL_LOG="${OUTPUT_DIR}/opensnitch-connections.json.gz"

# OpenSnitch logs connection decisions to syslog/journald (not to LogFile)
# LogFile contains daemon operational logs only
# shellcheck disable=SC2086
ssh $SSH_OPTS "$LZ_SSH" \
    "journalctl -u opensnitch -o json --no-pager 2>/dev/null | gzip" \
    > "$LOCAL_LOG"

LINE_COUNT=$(zcat "$LOCAL_LOG" 2>/dev/null | wc -l || echo 0)
info "Collected ${LINE_COUNT} log entries (compressed)"

if [ "${LINE_COUNT}" -eq 0 ]; then
    warning "No OpenSnitch log entries found — daemon may not have captured any connections"
fi

# Also grab daemon log file for debugging
# shellcheck disable=SC2086
ssh $SSH_OPTS "$LZ_SSH" \
    "gzip -c /var/log/opensnitchd.log 2>/dev/null || true" \
    > "${OUTPUT_DIR}/opensnitchd-daemon.log.gz"

success "Egress logs saved to ${OUTPUT_DIR}/ (gzip compressed)"

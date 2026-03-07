#!/bin/bash
# Landing Zone collection: ansible, openshift-install, oc-mirror, registry, discovery ISO, host info, config.
# Intended to be sourced from gather.sh (uses COLLECTION_DIR, ENCLAVE_DIR, GLOBAL_VARS_FILE, workingDir, getValue, log_*, ERRORS, WARNINGS).

# 1. Gather Ansible bootstrap logs
log_info " Gathering Ansible bootstrap logs..."
mkdir -p "${COLLECTION_DIR}/ansible-logs"
if [ -d "${ENCLAVE_DIR}/logs" ]; then
    cp -r "${ENCLAVE_DIR}/logs"/* "${COLLECTION_DIR}/ansible-logs/" 2>/dev/null && \
        log_info "  OK Collected Ansible bootstrap logs" || \
        log_warning "Partial failure copying Ansible logs"
else
    log_warning "Ansible logs not found: ${ENCLAVE_DIR}/logs"
    echo "NOT_FOUND" > "${COLLECTION_DIR}/ansible-logs/.not_found"
fi

# 2. Gather OpenShift installation logs
log_info " Gathering OpenShift installation logs..."
mkdir -p "${COLLECTION_DIR}/openshift-install"
# shellcheck disable=SC2154  # workingDir is set by parent script (gather.sh)
if [ -f "${workingDir}/ocp-cluster/.openshift_install.log" ]; then
    cp "${workingDir}/ocp-cluster/.openshift_install.log" "${COLLECTION_DIR}/openshift-install/" 2>/dev/null && \
        log_info "  OK Collected OpenShift installation log" || \
        log_warning "Failed to copy OpenShift install log"
else
    log_warning "OpenShift install log not found"
    echo "NOT_FOUND" > "${COLLECTION_DIR}/openshift-install/.not_found"
fi

# 3. Gather OC-Mirror logs
log_info " Gathering OC-Mirror logs..."
mkdir -p "${COLLECTION_DIR}/oc-mirror"
if ls "${workingDir}/logs/oc-mirror.progress."*.log 1>/dev/null 2>&1; then
    cp "${workingDir}/logs/oc-mirror.progress."*.log "${COLLECTION_DIR}/oc-mirror/" 2>/dev/null && \
        log_info "  OK Collected OC-Mirror logs ($(ls ${workingDir}/logs/oc-mirror.progress.*.log 2>/dev/null | wc -l) files)" || \
        log_warning "Failed to copy some OC-Mirror logs"
else
    log_warning "No OC-Mirror logs found"
    echo "NOT_FOUND" > "${COLLECTION_DIR}/oc-mirror/.not_found"
fi

# 4. Gather Mirror Registry (Quay) status
log_info " Gathering Mirror Registry status..."
mkdir -p "${COLLECTION_DIR}/registry"

# Container status
podman ps -a | grep quay > "${COLLECTION_DIR}/registry/container-status.txt" 2>&1 || \
    echo "No Quay containers found" > "${COLLECTION_DIR}/registry/container-status.txt"

# Quay container logs
if podman container exists quay-app 2>/dev/null; then
    podman logs quay-app > "${COLLECTION_DIR}/registry/quay-app.log" 2>&1 || \
        log_warning "Failed to get quay-app logs"
    podman inspect quay-app > "${COLLECTION_DIR}/registry/quay-app-inspect.json" 2>&1 || \
        log_warning "Failed to inspect quay-app"
    log_info "  OK Collected Quay container logs"
fi

# PostgreSQL logs
if podman container exists quay-postgres 2>/dev/null; then
    podman logs quay-postgres --tail 500 > "${COLLECTION_DIR}/registry/quay-postgres.log" 2>&1 || true
fi

# Redis logs
if podman container exists quay-redis 2>/dev/null; then
    podman logs quay-redis --tail 500 > "${COLLECTION_DIR}/registry/quay-redis.log" 2>&1 || true
fi

# Registry health check
QUAY_HOST=$(getValue .quayHostname)
if [[ -z "$QUAY_HOST" || "$QUAY_HOST" == "null" ]]; then
    QUAY_HOST="mirror.$(getValue .baseDomain)"
fi
curl -k "https://${QUAY_HOST}:8443/health/instance" > "${COLLECTION_DIR}/registry/health.json" 2>&1 || \
    echo '{"status":"unavailable"}' > "${COLLECTION_DIR}/registry/health.json"

# 5. Gather discovery ISO information
log_info " Gathering discovery ISO information..."
mkdir -p "${COLLECTION_DIR}/discovery-iso"

if [ -d "/var/www/html/assisted" ]; then
    ls -lh /var/www/html/assisted/ > "${COLLECTION_DIR}/discovery-iso/iso-listing.txt" 2>&1 || true
    log_info "  OK Collected ISO information"
else
    echo "NOT_FOUND" > "${COLLECTION_DIR}/discovery-iso/.not_found"
fi

# 7. Gather host information
log_info " Gathering host information..."
mkdir -p "${COLLECTION_DIR}/host-info"

cat > "${COLLECTION_DIR}/host-info/system-info.txt" <<EOF
Hostname: $(hostname)
Date: $(date)
Uptime: $(uptime)

Disk Usage:
$(df -h)

Memory:
$(free -h)

Network Interfaces:
$(ip addr)

Podman Containers:
$(podman ps -a)

Systemd Services:
$(systemctl list-units --type=service --state=running | head -20)
EOF

log_info "  OK Collected host information"

# 8. Gather configuration files (sanitized)
log_info " Gathering configuration files..."
mkdir -p "${COLLECTION_DIR}/config"

if [ -f "${GLOBAL_VARS_FILE}" ]; then
    # Sanitize sensitive fields using Python to properly handle multi-line YAML structures
    python3 <<PYTHON_SCRIPT > "${COLLECTION_DIR}/config/global.yaml" 2>/dev/null || true
import sys
import yaml

# Read the YAML file
with open('${GLOBAL_VARS_FILE}', 'r') as f:
    data = yaml.safe_load(f)

# Redact sensitive fields at root level
if 'quayPassword' in data:
    data['quayPassword'] = 'REDACTED'
if 'redfishPassword' in data:
    data['redfishPassword'] = 'REDACTED'
if 'secret_key' in data:
    data['secret_key'] = 'REDACTED'
if 'access_key' in data:
    data['access_key'] = 'REDACTED'

# Redact pullSecret completely (it's a complex nested structure)
if 'pullSecret' in data:
    data['pullSecret'] = 'REDACTED'

# Redact passwords in agent_hosts array
if 'agent_hosts' in data and isinstance(data['agent_hosts'], list):
    for host in data['agent_hosts']:
        if isinstance(host, dict):
            if 'redfishPassword' in host:
                host['redfishPassword'] = 'REDACTED'
            if 'password' in host:
                host['password'] = 'REDACTED'

# Redact passwords in discovery_hosts array (if exists)
if 'discovery_hosts' in data and isinstance(data['discovery_hosts'], list):
    for host in data['discovery_hosts']:
        if isinstance(host, dict):
            if 'redfishPassword' in host:
                host['redfishPassword'] = 'REDACTED'
            if 'password' in host:
                host['password'] = 'REDACTED'

# Write sanitized YAML
yaml.dump(data, sys.stdout, default_flow_style=False, sort_keys=False)
PYTHON_SCRIPT

    # Fallback to sed if Python fails
    if [ ! -s "${COLLECTION_DIR}/config/global.yaml" ]; then
        log_warning "Python sanitization failed, using sed fallback"
        sed -e 's/\(quayPassword:\).*/\1 REDACTED/' \
            -e 's/\(redfishPassword:\).*/\1 REDACTED/' \
            -e 's/\(pullSecret:\).*/\1 REDACTED/' \
            -e 's/\(secret_key:\).*/\1 REDACTED/' \
            -e 's/\(access_key:\).*/\1 REDACTED/' \
            -e 's/\(auth:\).*/\1 REDACTED/' \
            "${GLOBAL_VARS_FILE}" > "${COLLECTION_DIR}/config/global.yaml" 2>/dev/null || true
    fi

    log_info "  OK Collected configuration (sanitized)"
fi

CLOUD_INFRA_FILE="${ENCLAVE_DIR}/config/cloud_infra.yaml"
if [ -f "${CLOUD_INFRA_FILE}" ]; then
    python3 <<PYTHON_SCRIPT > "${COLLECTION_DIR}/config/cloud_infra.yaml" 2>/dev/null || true
import sys
import yaml

with open('${CLOUD_INFRA_FILE}', 'r') as f:
    data = yaml.safe_load(f)

if 'discovery_hosts' in data and isinstance(data['discovery_hosts'], list):
    for host in data['discovery_hosts']:
        if isinstance(host, dict):
            if 'redfishPassword' in host:
                host['redfishPassword'] = 'REDACTED'
            if 'password' in host:
                host['password'] = 'REDACTED'

yaml.dump(data, sys.stdout, default_flow_style=False, sort_keys=False)
PYTHON_SCRIPT

    if [ ! -s "${COLLECTION_DIR}/config/cloud_infra.yaml" ]; then
        log_warning "Python sanitization failed for cloud_infra.yaml, using sed fallback"
        sed -e 's/\(redfishPassword:\).*/\1 REDACTED/' \
            "${CLOUD_INFRA_FILE}" > "${COLLECTION_DIR}/config/cloud_infra.yaml" 2>/dev/null || true
    fi

    log_info "  OK Collected cloud infra configuration (sanitized)"
fi

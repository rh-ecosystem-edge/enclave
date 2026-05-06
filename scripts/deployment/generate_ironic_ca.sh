#!/bin/bash
# Generate a CA for signing the Ironic ISO server TLS certificate
#
# Runs on the CI runner before the infrastructure is created, so that
# start_sushy_tools.sh can install the CA into the sushy-tools container
# trust store at startup time.
#
# The CA cert is written to the sushy-tools working directory, which is
# mounted inside the container at /root/sushy/. The CA private key is kept
# in a separate directory that is NOT mounted into the container, so it is
# not exposed inside the sushy-tools container.
#
# Usage: ./generate_ironic_ca.sh
# Required env: WORKING_DIR

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"

require_env_var "WORKING_DIR"

SUSHY_DIR="${WORKING_DIR}/virtualbmc/sushy-tools"
IRONIC_CA_DIR="${WORKING_DIR}/ironic-ca"

# Create directories if they don't exist yet
if [ ! -d "$SUSHY_DIR" ]; then
    info "Creating sushy-tools directory: $SUSHY_DIR"
    sudo mkdir -p "$SUSHY_DIR"
fi
sudo mkdir -p "$IRONIC_CA_DIR"

info "Generating CA for Ironic ISO server..."

sudo openssl req -new -x509 -nodes -days 365 \
    -subj "/CN=enclave-ironic-iso-ca" \
    -keyout "${IRONIC_CA_DIR}/ca.key" \
    -out "${IRONIC_CA_DIR}/ca.crt" \
    2>/dev/null

# Make CA key and cert readable only by the current user.
# Both stay in ironic-ca/, which is NOT mounted into any container, so
# they are never SELinux-relabeled by a podman :z volume mount.
sudo chown "$(id -u):$(id -g)" "${IRONIC_CA_DIR}/ca.key" "${IRONIC_CA_DIR}/ca.crt"
chmod 600 "${IRONIC_CA_DIR}/ca.key"
chmod 644 "${IRONIC_CA_DIR}/ca.crt"

# Copy the CA cert into the sushy-tools directory so the container can
# read it via its volume mount.  The copy will be SELinux-relabeled when
# the container starts; that is intentional and only affects the copy.
sudo cp "${IRONIC_CA_DIR}/ca.crt" "${SUSHY_DIR}/ca.crt"

success "Ironic ISO server CA generated"
info "  CA cert: ${IRONIC_CA_DIR}/ca.crt (authoritative; not in container volume)"
info "  CA cert: ${SUSHY_DIR}/ca.crt (copy for container; will be SELinux-relabeled)"
info "  CA key:  ${IRONIC_CA_DIR}/ca.key (not mounted into container)"


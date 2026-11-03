#!/bin/bash
# Deploy the application plugins listed in ENABLED_PLUGINS on the Landing Zone.
#
# Reads ENABLED_PLUGINS (comma-separated), skips the storage plugins lvms/odf
# (deployed via the operators phase and setup-ceph), validates the remaining
# descriptors up front, and deploys them in ascending plugin.yaml `order` so
# prerequisites deploy before dependents (the order the deploy-plugins playbook
# uses). Each plugin is deployed via deploy_plugin.sh.
#
# With --check, only parse and validate the requested plugins (no deployment),
# so a bad ENABLED_PLUGINS fails fast before the long cluster phases.
#
# Usage: ENABLED_PLUGINS="trust-manager,rhbk,osac" ./deploy_enabled_plugins.sh [--check]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/ci/lib/output.sh"

CHECK_ONLY=false
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=true
fi

ENABLED_PLUGINS="${ENABLED_PLUGINS:-}"

if [ -z "${ENABLED_PLUGINS}" ]; then
    info "ENABLED_PLUGINS is empty; no plugins to deploy"
    exit 0
fi

# Split on commas, trim whitespace, drop storage plugins (handled elsewhere).
plugins=()
IFS=',' read -ra _raw <<< "${ENABLED_PLUGINS}"
for _p in "${_raw[@]}"; do
    _p="${_p//[[:space:]]/}"
    case "${_p}" in
        "" | lvms | odf) continue ;;
    esac
    plugins+=("${_p}")
done

if [ "${#plugins[@]}" -eq 0 ]; then
    info "No application plugins to deploy (ENABLED_PLUGINS holds only storage/empty entries)"
    exit 0
fi

# Validate descriptors up front so a typo fails fast rather than silently
# skipping later plugins.
for _p in "${plugins[@]}"; do
    if [ ! -f "${ENCLAVE_DIR}/plugins/${_p}/plugin.yaml" ]; then
        error "Plugin not found: plugins/${_p}/plugin.yaml (from ENABLED_PLUGINS)"
        exit 1
    fi
done

if [ "${CHECK_ONLY}" = true ]; then
    info "ENABLED_PLUGINS descriptors validated: ${plugins[*]}"
    exit 0
fi

# Order by the plugin.yaml `order` field (default 999), parsed as YAML so quoted
# keys and non-source-literal forms are handled, then deploy.
ordered="$(printf '%s\n' "${plugins[@]}" | ENCLAVE_DIR="${ENCLAVE_DIR}" python3 -c '
import os, sys, yaml

root = os.environ["ENCLAVE_DIR"]
names = [line.strip() for line in sys.stdin if line.strip()]


def order(name):
    with open(os.path.join(root, "plugins", name, "plugin.yaml")) as fh:
        data = yaml.safe_load(fh) or {}
    try:
        return int(data.get("order", 999))
    except (TypeError, ValueError):
        return 999


for name in sorted(names, key=order):
    print(name)
')"

info "Deploying plugins in order: $(echo "${ordered}" | tr '\n' ' ')"
while IFS= read -r _p; do
    [ -n "${_p}" ] || continue
    "${SCRIPT_DIR}/deploy_plugin.sh" "${_p}"
done <<< "${ordered}"

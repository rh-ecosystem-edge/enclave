#!/bin/bash
# Refresh the checksum of one Ansible collection in ansible_collections.sha256
#
# Queries the Ansible Galaxy v3 API for the published sha256 of a specific
# collection version and rewrites its line in ansible_collections.sha256.
# Avoids downloading the collection tarball since Galaxy already publishes
# its checksum as metadata.
#
# Usage:
#   ./update_ansible_collection_sha256.sh <namespace.name> <version>
#
# Example:
#   ./update_ansible_collection_sha256.sh community.crypto 3.2.2

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <namespace.name> <version>" >&2
    exit 1
fi

FQCN="$1"
NAMESPACE="${FQCN%%.*}"
NAME="${FQCN#*.}"
VERSION="$2"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"
SHA256_FILE="${ENCLAVE_DIR}/ansible_collections.sha256"

META_FILE="$(mktemp)"
trap 'rm -f "$META_FILE"' EXIT

curl -sf \
    "https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/${NAMESPACE}/${NAME}/versions/${VERSION}/" \
    -o "$META_FILE"

python3 - "$NAMESPACE" "$NAME" "$META_FILE" "$SHA256_FILE" <<'PYEOF'
import json
import sys

namespace, name, meta_file, sha256_file = sys.argv[1:5]

with open(meta_file) as f:
    artifact = json.load(f)["artifact"]

try:
    with open(sha256_file) as f:
        lines = f.readlines()
except FileNotFoundError:
    lines = []

prefix = f"*collections/{namespace}-{name}-"
lines = [line for line in lines if prefix not in line]
lines.append(f"{artifact['sha256']} *collections/{artifact['filename']}\n")

with open(sha256_file, "w") as f:
    f.writelines(lines)
PYEOF

echo "✅ Updated checksum for ${NAMESPACE}.${NAME} ${VERSION} in ${SHA256_FILE}"

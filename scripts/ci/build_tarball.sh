#!/usr/bin/env bash
# Build and optionally push the enclave distribution tarball.
#
# Usage:
#   scripts/ci/build_tarball.sh build          # Build and validate only
#   scripts/ci/build_tarball.sh build-push     # Build, validate, and push to Quay

set -euo pipefail

ACTION="${1:-build}"

TAG="${TARBALL_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"
TARBALL="enclave.tar.gz"
MAX_SIZE=1073741824  # 1GB

# --- Build ---

cleanup() {
    rm -f .version /tmp/tarball-contents.txt
}
trap cleanup EXIT

echo "Building distribution tarball..."
echo -n "$TAG" > .version

tar --exclude='.git' --exclude='.gitignore' --exclude='.github' --exclude='scripts' \
    --exclude='Makefile.ci' --exclude="$TARBALL" \
    -czvf "/tmp/$TARBALL" .
mv "/tmp/$TARBALL" .

echo ""
echo "Validating tarball..."

# Check size
SIZE=$(stat -c%s "$TARBALL")
echo "Tarball size: $(numfmt --to=iec-i --suffix=B "$SIZE")"
if [ "$SIZE" -gt "$MAX_SIZE" ]; then
    echo "Error: Tarball exceeds 1GB"
    exit 1
fi

# Extract file list
tar -tzf "$TARBALL" > /tmp/tarball-contents.txt

# Check required files
REQUIRED_FILES=(".version" "Makefile")
for file in "${REQUIRED_FILES[@]}"; do
    if ! grep -q "^\./${file}$" /tmp/tarball-contents.txt; then
        echo "Error: Required file '${file}' not found in tarball"
        head -20 /tmp/tarball-contents.txt
        exit 1
    fi
    echo "  Found ${file}"
done

# Check required directories (only if they exist in source)
REQUIRED_DIRS=("playbooks" "operators" "configs")
for dir in "${REQUIRED_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        if ! grep -q "^\./${dir}/" /tmp/tarball-contents.txt; then
            echo "Error: Required directory '${dir}/' not found in tarball"
            head -20 /tmp/tarball-contents.txt
            exit 1
        fi
        echo "  Found ${dir}/"
    fi
done

# Check excluded paths are absent
EXCLUDED_PATHS=(".git/" ".github/" "Makefile.ci" "scripts/")
for path in "${EXCLUDED_PATHS[@]}"; do
    if grep -q "^\./${path}" /tmp/tarball-contents.txt; then
        echo "Error: Excluded path '${path}' found in tarball"
        exit 1
    fi
    echo "  ${path} correctly excluded"
done

# Validate file counts for critical directories
echo "Validating file counts..."
for dir in "${REQUIRED_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        SOURCE_COUNT=$(find "$dir" -type f | wc -l)
        TARBALL_COUNT=$(grep "^\./${dir}/" /tmp/tarball-contents.txt | grep -v '/$' | wc -l)
        echo "  ${dir}/: source=${SOURCE_COUNT}, tarball=${TARBALL_COUNT}"
        if [ "$SOURCE_COUNT" -ne "$TARBALL_COUNT" ]; then
            echo "Error: File count mismatch in ${dir}/"
            echo "  Expected: ${SOURCE_COUNT} files"
            echo "  Found in tarball: ${TARBALL_COUNT} files"
            exit 1
        fi
    fi
done

echo "Tarball validation passed"

# --- Push (optional) ---

if [ "$ACTION" = "build-push" ]; then
    if [ -z "${QUAY_USER:-}" ] || [ -z "${QUAY_TOKEN:-}" ]; then
        echo "Error: QUAY_USER and QUAY_TOKEN must be set"
        exit 1
    fi

    echo "$QUAY_TOKEN" | podman login quay.io -u "$QUAY_USER" --password-stdin

    echo "Pushing tarball with tag: $TAG"
    oras push "quay.io/edge-infrastructure/enclave:${TAG}" \
        "${TARBALL}:application/vnd.oci.image.layer.v1.tar+gzip"

    rm -f "$TARBALL"
    echo "Tarball pushed successfully"
elif [ "$ACTION" = "build" ]; then
    echo "Tarball built: $TARBALL"
else
    echo "Unknown action: $ACTION"
    echo "Usage: $0 build|build-push"
    exit 1
fi

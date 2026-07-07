#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${REPO_DIR}/out"
SPEC_FILE="${SCRIPT_DIR}/enclave.spec"

VERSION=$(grep '^version' "${REPO_DIR}/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
if [[ -z "${VERSION}" ]]; then
    echo "ERROR: Could not extract version from pyproject.toml"
    exit 1
fi

echo "=== Building Enclave RPM ==="
echo "  Version: ${VERSION}"
echo ""

WORK_DIR=$(mktemp -d)
cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

# --- Step 1: Create source tarball ---
echo "[1/3] Creating source tarball..."
git -C "${REPO_DIR}" archive \
    --format=tar.gz \
    --prefix="enclave-${VERSION}/" \
    --output="${WORK_DIR}/enclave-${VERSION}.tar.gz" \
    HEAD

# --- Step 2: Build RPM with Mock inside podman ---
echo "[2/3] Building RPM (Mock inside podman)..."
mkdir -p "${OUT_DIR}"

podman run --rm --privileged \
    -v "${WORK_DIR}:/work:z" \
    -v "${SCRIPT_DIR}:/specs:z" \
    -v "${OUT_DIR}:/out:z" \
    registry.fedoraproject.org/fedora:42 \
    bash -c "
        set -euo pipefail

        echo '  Installing mock and rpm-build...'
        dnf install -y mock rpm-build 2>/dev/null >/dev/null

        # Mock needs a non-root user
        useradd -m mockbuilder
        usermod -aG mock mockbuilder

        RPMBUILD_DIR=\$(mktemp -d)
        mkdir -p \${RPMBUILD_DIR}/{SOURCES,SPECS,RPMS,BUILD,SRPMS}
        cp /work/enclave-${VERSION}.tar.gz \${RPMBUILD_DIR}/SOURCES/
        cp /specs/enclave.spec \${RPMBUILD_DIR}/SPECS/

        # Build SRPM first
        echo '  Building SRPM...'
        rpmbuild -bs \\
            --define \"_topdir \${RPMBUILD_DIR}\" \\
            --define \"enclave_version ${VERSION}\" \\
            \${RPMBUILD_DIR}/SPECS/enclave.spec

        SRPM=\$(find \${RPMBUILD_DIR}/SRPMS -name '*.src.rpm' | head -1)
        echo \"  SRPM: \$(basename \${SRPM})\"

        # Rebuild with Mock
        echo '  Building RPM with Mock (centos-stream-10-x86_64)...'
        su - mockbuilder -c \"mock -r centos-stream-10-x86_64 \\
            --define 'enclave_version ${VERSION}' \\
            --rebuild \${SRPM} \\
            --resultdir /out\"

        echo '  Build complete.'
    "

# --- Step 3: Generate checksums ---
echo "[3/3] Generating checksums..."
for rpm in "${OUT_DIR}/"enclave-*.rpm; do
    [[ -f "${rpm}" ]] || continue
    sha256sum "${rpm}" > "${rpm}.sha256"
done

echo ""
echo "Build complete. Artifacts in ${OUT_DIR}/:"
ls -lh "${OUT_DIR}/"enclave-* 2>/dev/null || echo "  (no artifacts found)"

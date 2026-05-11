#!/bin/sh -e

UV_VERSION="0.11.13"
UV_PREFIX="$HOME/.local"

UV_INSTALLER=$(mktemp)
curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o "$UV_INSTALLER"
UV_UNMANAGED_INSTALL="$UV_PREFIX/bin" sh "$UV_INSTALLER"
rm -f "$UV_INSTALLER"

"$UV_PREFIX/bin/uv" tool install . --with-executables-from ansible-core

# Download Ansible Collections to check the checksum
# Check if the 'collections' directory exists and has files in it
if [ -d "collections" ] && [ "$(ls -A collections)" ] && sha256sum --status -c ansible_collections.sha256; then
    echo "Collections already exist. Skipping download."
else
    echo "Collections not found or empty. Downloading..."
    ansible-galaxy collection download --download-path collections --requirements-file ansible_collections.txt
fi

# Check if checksum is correct
cat ansible_collections.sha256 | sha256sum -c

# Install downloaded Ansible Collections
cd collections
ansible-galaxy collection install -r requirements.yml --offline

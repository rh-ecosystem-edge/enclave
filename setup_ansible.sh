#!/bin/sh -e
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
cd ..

# Install Python libraries required
pip3 install -r ansible_pip_requirements.txt --disable-pip-version-check

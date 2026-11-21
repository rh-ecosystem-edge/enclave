#!/bin/bash

set -e

bash "$(dirname -- "${BASH_SOURCE[0]}")/scripts/validation/check_tmp_exec.sh"

# Install prerequisites
# podman is held at 5.x: with podman 6 rootless commands fail because the run
# root resolves to /run/containers/storage, which is not writable by the user.
dnf install -y \
    bind-utils \
    curl \
    jq \
    httpd \
    httpd-tools \
    ipcalc \
    lsof \
    make \
    nmstate \
    openssl \
    'podman-5.*' \
    python3 \
    rsync \
    skopeo \
    tar \
    unzip \
    vim

# Install AWS CLI v2 from official installer
AWS_CLI_VERSION=2.38.4
AWSCLI_TMP=$(mktemp -d)
trap 'rm -rf "${AWSCLI_TMP}"' EXIT
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" -o "${AWSCLI_TMP}/awscliv2.zip"
unzip -q "${AWSCLI_TMP}/awscliv2.zip" -d "${AWSCLI_TMP}"
"${AWSCLI_TMP}/aws/install" --update

systemctl enable --now httpd
mkdir -p /var/www/html
chmod 755 /var/www/html

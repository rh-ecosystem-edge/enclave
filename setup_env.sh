#!/bin/bash

set -e

# Install prerequisites
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
    podman \
    python3 \
    rsync \
    skopeo \
    tar \
    unzip \
    vim

# Install AWS CLI v2 from official installer
AWS_CLI_VERSION=2.34.53
AWSCLI_TMP=$(mktemp -d)
trap 'rm -rf "${AWSCLI_TMP}"' EXIT
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" -o "${AWSCLI_TMP}/awscliv2.zip"
unzip -q "${AWSCLI_TMP}/awscliv2.zip" -d "${AWSCLI_TMP}"
"${AWSCLI_TMP}/aws/install"

systemctl enable --now httpd
mkdir -p /var/www/html
chmod 755 /var/www/html

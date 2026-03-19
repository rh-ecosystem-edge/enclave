#!/bin/sh

# Install prerequisites
dnf install -y \
    ansible-core \
    awscli2 \
    bind-utils \
    curl \
    httpd \
    httpd-tools \
    ipcalc \
    lsof \
    nmstate \
    openssl \
    podman \
    python3-pip \
    tar \
    vim

systemctl enable --now httpd

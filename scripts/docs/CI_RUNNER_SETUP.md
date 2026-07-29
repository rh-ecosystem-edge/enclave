# GitHub Actions Self-Hosted Runner Setup Guide

This guide covers setting up a GitHub Actions self-hosted runner on the Enclave Lab CI machine.

## Prerequisites

- Dedicated CI machine with:
  - 64GB+ RAM
  - 1TB+ disk space
  - CentOS Stream / RHEL / Fedora
  - libvirt/KVM installed and configured
  - Podman installed
- GitHub repository admin access
- dev-scripts repository cloned and configured

## Architecture Overview

The self-hosted runner:
- Runs as a systemd service on the CI machine
- Executes GitHub Actions workflows from the Enclave Lab repository
- Has access to libvirt for VM management
- Uses dev-scripts for infrastructure provisioning
- Runs in isolated environment with proper permissions

## Installation Steps

### Step 1: Create Runner User

Create a dedicated user for the GitHub Actions runner:

```bash
# Create github-runner user
sudo useradd -m -s /bin/bash github-runner

# Add to libvirt group for VM management
sudo usermod -aG libvirt github-runner

# Allow passwordless sudo (required for virsh commands)
echo "github-runner ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/github-runner
sudo chmod 0440 /etc/sudoers.d/github-runner
```

### Step 2: Install Runner Software

1. Go to GitHub repository Settings → Actions → Runners → New self-hosted runner

2. Follow GitHub's instructions to download and configure the runner:

```bash
# Switch to runner user
sudo su - github-runner

# Create a directory for the runner
mkdir actions-runner && cd actions-runner

# Download the latest runner (check GitHub for current version)
curl -o actions-runner-linux-x64-2.311.0.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.311.0/actions-runner-linux-x64-2.311.0.tar.gz

# Extract the installer
tar xzf ./actions-runner-linux-x64-2.311.0.tar.gz

# Configure the runner
# Use the token from GitHub's setup page
./config.sh --url https://github.com/YOUR-ORG/enclave --token YOUR-TOKEN

# When prompted:
# - Runner name: enclave-runner-01 (or similar)
# - Runner group: Default
# - Labels: self-hosted,enclave
# - Work folder: _work (default)
```

### Step 3: Install as Systemd Service

```bash
# Still as github-runner user
cd ~/actions-runner

# Install the service
sudo ./svc.sh install github-runner

# Start the service
sudo ./svc.sh start

# Check status
sudo ./svc.sh status

# Enable auto-start on boot
sudo systemctl enable actions.runner.YOUR-ORG-enclave.enclave-runner-01.service
```

### Step 4: Configure Runner Environment

Create environment file for the runner:

```bash
# Create .env file in runner directory
cat > ~/actions-runner/.env <<EOF
# dev-scripts configuration
DEV_SCRIPTS_PATH=/home/github-runner/dev-scripts
WORKING_DIR=/opt/dev-scripts

# Pull secret path (will be set from GitHub secrets)
PULL_SECRET_PATH=/home/github-runner/.pull-secret.json
EOF

# Secure the file
chmod 600 ~/actions-runner/.env
```

### Step 5: Clone and Configure dev-scripts

```bash
# Clone dev-scripts repository
cd /home/github-runner
git clone https://github.com/openshift-metal3/dev-scripts.git

# Create working directory
sudo mkdir -p /opt/dev-scripts
sudo chown github-runner:github-runner /opt/dev-scripts
```

### Step 6: Verify Runner Setup

Check that the runner appears in GitHub:

1. Go to GitHub repository Settings → Actions → Runners
2. You should see your runner with status "Idle"
3. Labels should include: `self-hosted`, `enclave`

### Step 7: Configure GitHub Repository Secrets

Add the following secrets in GitHub repository Settings → Secrets and variables → Actions:

Required secrets:
- `DEV_SCRIPTS_PATH`: `/home/github-runner/dev-scripts`
- `WORKING_DIR`: `/opt/dev-scripts`
- `PULL_SECRET`: Your OpenShift pull secret (JSON format)

Additional secrets required for real TLS certificate runs (`cert-type` dispatch input):
- `HETZNER_API_TOKEN`: Hetzner DNS API token (used for DNS-01 challenge record creation)
- `ZEROSSL_EAB_KID`: ZeroSSL External Account Binding key ID
- `ZEROSSL_EAB_HMAC_KEY`: ZeroSSL External Account Binding HMAC key
- `ACME_EMAIL`: Email address registered with ZeroSSL
- `ENCLAVE_BASE_DOMAIN`: Public DNS domain used for SAN generation (e.g. `example.com`)

## Verification

Test that the runner can execute basic commands:

```bash
# Check libvirt access
sudo virsh list --all

# Check dev-scripts
ls -la $DEV_SCRIPTS_PATH

# Check disk space
df -h /opt/dev-scripts

# Check memory
free -g
```

## Maintenance and Troubleshooting

See [CI_RUNNER_MAINTENANCE.md](CI_RUNNER_MAINTENANCE.md) for:
- Restarting and updating runners
- Re-registering OOM-killed or expired runners
- Checking logs and diagnosing offline runners
- Cleaning up stale VMs and disk space

## Security Considerations

- Runner runs as dedicated user (`github-runner`)
- Has sudo access only for required operations
- Secrets are stored in GitHub and injected at runtime
- Pull secrets never exposed in logs
- Artifacts have limited retention (7 days)
- Runner is isolated from production systems

## Uninstallation

To remove the runner:

```bash
# Stop and uninstall service
cd ~/actions-runner
sudo ./svc.sh stop
sudo ./svc.sh uninstall

# Remove runner from GitHub
./config.sh remove --token YOUR-TOKEN

# Clean up
cd ~
rm -rf actions-runner

# Remove user (optional)
sudo userdel -r github-runner
```

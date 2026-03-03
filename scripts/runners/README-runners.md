# GitHub Actions Self-Hosted Runners

Scripts to manage self-hosted GitHub Actions runners for the enclave project.

## Scripts

- **setup_github_runners_podman.sh** - One-time podman setup for runner machines (run first!)
- **install_runner_ci_requirements.sh** - Install all CI prerequisites (optional, for full CI capability)
- **action-runners-setup.sh** - Set up multiple GitHub Actions runners
- **action-runners-cleanup.sh** - Remove all runners

## Complete Setup (New Runner Machine)

### Step 1: Configure Podman for GitHub Actions

**Run once per machine** to configure podman for container jobs:

```bash
sudo bash scripts/runners/setup_github_runners_podman.sh
```

This script:
- Removes Docker (if present) and installs podman
- Enables system podman socket
- Creates `/var/run/docker.sock` symlink for GitHub Actions compatibility
- Configures all runner services to use podman
- Sets proper permissions

### Step 2: Install CI Prerequisites (Optional)

**Only needed if runners will execute full CI workflows** (libvirt, dev-scripts, etc.):

```bash
sudo bash scripts/runners/install_runner_ci_requirements.sh
```

This installs:
- Development tools (git, make, gcc, etc.)
- Python, Ansible, and CI tools
- Libvirt, KVM, and networking tools
- Container tools (podman, buildah, skopeo)
- GitHub CLI

**Note:** Skip this step if runners only need basic validation jobs.

### Step 3: Install GitHub Actions Runners

#### 3.1 Get a Registration Token

Go to: https://github.com/rh-ecosystem-edge/enclave/settings/actions/runners/new

Click "New self-hosted runner" and copy the token from the configuration command.

**Note:** Tokens expire in 1 hour!

#### 3.2 Run Setup Script

```bash
cd ~/go/src/github.com/rh-ecosystem-edge/enclave
./scripts/runners/action-runners-setup.sh <TOKEN> [NUM_RUNNERS]
```

**Examples:**
```bash
# Create 5 runners (default: 64)
./scripts/runners/action-runners-setup.sh AABBCCDDEE112233445566 5

# Create 10 runners
./scripts/runners/action-runners-setup.sh AABBCCDDEE112233445566 10
```

Runner names use hostname prefix: `<hostname>-runner-01`, `<hostname>-runner-02`, etc.
- On `enclave-3.edgeinfra.cloud` → `enclave-3-runner-01`, `enclave-3-runner-02`, ...
- On `myserver.example.com` → `myserver-runner-01`, `myserver-runner-02`, ...

### Step 4: Verify Setup

Check runners in GitHub:
https://github.com/rh-ecosystem-edge/enclave/settings/actions/runners

Check systemd services:
```bash
sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.*.service
```

View logs:
```bash
# View specific runner logs
sudo journalctl -u actions.runner.rh-ecosystem-edge-enclave.enclave-3-runner-01.service -f

# View all runner logs
sudo journalctl -u 'actions.runner.rh-ecosystem-edge-enclave.*' -f
```

Test podman:
```bash
# As github-runner user
docker ps  # Uses podman
podman ps
```

## Remove Runners

### Option 1: Full Cleanup (with GitHub unregistration)

Get a removal token (same process as setup), then:

```bash
./scripts/runners/action-runners-cleanup.sh <TOKEN>
```

This will:
- Stop all systemd services
- Uninstall services
- Unregister runners from GitHub
- Remove all runner directories

### Option 2: Local Cleanup Only (no GitHub unregistration)

```bash
./scripts/runners/action-runners-cleanup.sh
```

This will:
- Stop all systemd services
- Uninstall services
- Remove all runner directories
- Runners will appear as "offline" in GitHub settings

**Note:** You can manually remove offline runners from GitHub settings.

## Runner Configuration

Each runner is configured with:
- **Name:** `<hostname>-runner-01`, `<hostname>-runner-02`, etc.
- **Labels:** `self-hosted`, `pr-validation`
- **Service:** Runs as systemd service under github-runner user
- **Base directory:** `~/action-runners/runner-N/`
- **Container runtime:** Podman (via `/var/run/docker.sock`)

## Workflow Integration

The runners are used by `.github/workflows/pr-validation.yml`:

```yaml
jobs:
  shellcheck:
    runs-on: [self-hosted, pr-validation]
    container:
      image: quay.io/eerez/enclave-lab-ci:latest
```

Jobs run in parallel across available runners using podman for container isolation.

## Architecture

### Podman Setup

- **System podman socket:** `/run/podman/podman.sock` (enabled via systemd)
- **Docker compatibility:** `/var/run/docker.sock` → symlink to podman socket
- **Permissions:** Socket is world-accessible (666) for GitHub Actions
- **No Docker:** Pure podman setup, Docker removed if present

### Runner Services

Each runner runs as a systemd service:
```
/etc/systemd/system/actions.runner.rh-ecosystem-edge-enclave.<hostname>-runner-XX.service
```

Environment configuration:
```
/etc/systemd/system/actions.runner.*.service.d/podman-environment.conf
```

Contains:
```ini
[Service]
Environment="DOCKER_HOST=unix:///var/run/docker.sock"
```

## Troubleshooting

### Container jobs fail with "permission denied"

If you see:
```
Error: statfs /var/run/docker.sock: permission denied
```

Re-run the podman setup:
```bash
sudo bash scripts/runners/setup_github_runners_podman.sh
```

### Runner not appearing in GitHub

- Check token hasn't expired (1 hour limit)
- Verify runner service is running: `sudo systemctl status actions.runner.*`
- Check logs: `sudo journalctl -u actions.runner.* -f`

### Service won't start

```bash
# Check service status
sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.enclave-3-runner-01.service

# View detailed logs
sudo journalctl -u actions.runner.rh-ecosystem-edge-enclave.enclave-3-runner-01.service -n 50

# Restart service
sudo systemctl restart actions.runner.rh-ecosystem-edge-enclave.enclave-3-runner-01.service
```

### Podman socket issues

Check socket permissions:
```bash
ls -la /run/podman/podman.sock
ls -la /var/run/docker.sock

# Test access as github-runner
sudo -u github-runner docker ps
sudo -u github-runner podman ps
```

Socket should be:
```
srw-rw-rw-. 1 root root 0 /run/podman/podman.sock
lrwxrwxrwx. 1 root root 23 /var/run/docker.sock -> /run/podman/podman.sock
```

### Remove specific runner

```bash
cd ~/action-runners/runner-1
sudo ./svc.sh stop
sudo ./svc.sh uninstall
./config.sh remove --token <TOKEN>
cd ..
rm -rf runner-1
```

### Update runner version

1. Remove all runners: `./scripts/runners/action-runners-cleanup.sh <TOKEN>`
2. Edit `action-runners-setup.sh` and update `RUNNER_VERSION`
3. Re-run setup: `./scripts/runners/action-runners-setup.sh <TOKEN>`

### Reconfigure podman after runner restart

If runners lose podman access after reboot:
```bash
sudo bash scripts/runners/setup_github_runners_podman.sh
```

This is idempotent and safe to run multiple times.

## Quick Reference

### Fresh Machine Setup
```bash
# 1. Configure podman (required)
sudo bash scripts/runners/setup_github_runners_podman.sh

# 2. Install CI tools (optional, only for full CI workflows)
sudo bash scripts/runners/install_runner_ci_requirements.sh

# 3. Install runners
./scripts/runners/action-runners-setup.sh <TOKEN> 5
```

### Common Commands
```bash
# View all runners
sudo systemctl list-units 'actions.runner.*'

# Restart all runners
sudo systemctl restart 'actions.runner.*'

# View all runner logs
sudo journalctl -u 'actions.runner.*' -f

# Test podman
sudo -u github-runner docker ps
sudo -u github-runner podman ps
```

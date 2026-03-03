# GitHub Actions Self-Hosted Runners

Scripts to manage self-hosted GitHub Actions runners for PR validation.

## Scripts

- **action-runners-setup.sh** - Set up multiple runners
- **action-runners-cleanup.sh** - Remove all runners

## Setup Runners

### 1. Get a Registration Token

Go to: https://github.com/rh-ecosystem-edge/enclave/settings/actions/runners/new

Click "New self-hosted runner" and copy the token from the configuration command.

**Note:** Tokens expire in 1 hour!

### 2. Run Setup Script

```bash
cd ~/go/src/github.com/rh-ecosystem-edge/enclave
./scripts/runners/action-runners-setup.sh <TOKEN> [NUM_RUNNERS]
```

**Examples:**
```bash
# Create 6 runners (default)
./scripts/runners/action-runners-setup.sh AABBCCDDEE112233445566

# Create 10 runners
./scripts/runners/action-runners-setup.sh AABBCCDDEE112233445566 10
```

### 3. Verify Runners

Check runners in GitHub:
https://github.com/rh-ecosystem-edge/enclave/settings/actions/runners

Check systemd services:
```bash
sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.pr-validation-*.service
```

View logs:
```bash
# View specific runner logs
sudo journalctl -u actions.runner.rh-ecosystem-edge-enclave.pr-validation-01.service -f

# View all runner logs
sudo journalctl -u 'actions.runner.rh-ecosystem-edge-enclave.pr-validation-*' -f
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
- **Name:** `pr-validation-01`, `pr-validation-02`, etc.
- **Labels:** `self-hosted`, `pr-validation`
- **Service:** Runs as systemd service under current user
- **Base directory:** `~/action-runners/runner-N/`

## Workflow Integration

The runners are used by `.github/workflows/pr-validation.yml`:

```yaml
jobs:
  shellcheck:
    runs-on: [self-hosted, pr-validation]
    container:
      image: ubuntu:22.04
```

All 6 validation jobs run in parallel across available runners.

## Troubleshooting

### Runner not appearing in GitHub
- Check token hasn't expired (1 hour limit)
- Verify runner service is running: `sudo systemctl status actions.runner.*`
- Check logs: `sudo journalctl -u actions.runner.* -f`

### Service won't start
```bash
# Check service status
sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.pr-validation-01.service

# View detailed logs
sudo journalctl -u actions.runner.rh-ecosystem-edge-enclave.pr-validation-01.service -n 50

# Restart service
cd ~/action-runners/runner-1
sudo ./svc.sh stop
sudo ./svc.sh start
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

1. Remove all runners: `./scripts/action-runners-cleanup.sh <TOKEN>`
2. Edit `action-runners-setup.sh` and update `RUNNER_VERSION`
3. Re-run setup: `./scripts/action-runners-setup.sh <TOKEN>`

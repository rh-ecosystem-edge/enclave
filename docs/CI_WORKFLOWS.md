# GitHub Actions Workflows Guide

This guide explains how to use the GitHub Actions CI/CD workflows for Enclave Lab.

## Overview

Enclave Lab uses GitHub Actions for automated testing and validation with four main workflows:

1. **PR Validation** - Fast code quality checks on every PR
2. **Infrastructure Verification** - Test infrastructure setup
3. **E2E Connected Mode** - Full cluster deployment testing
4. **Cleanup** - Infrastructure maintenance

## Workflow 1: PR Validation

**Purpose**: Fast code quality checks that run on every pull request

**Trigger**: Automatic on PR open, update, or reopen

**Duration**: ~5-10 minutes

**Runs on**: GitHub-hosted runners (no CI machine needed)

### What It Checks

- ✅ Shell script syntax (shellcheck)
- ✅ YAML formatting (yamllint)
- ✅ Ansible playbook syntax (ansible-lint)
- ✅ Makefile syntax

### How to Use

This workflow runs automatically on every PR. No manual action needed.

To test locally before pushing:
```bash
make validate
```

### Viewing Results

1. Go to your PR on GitHub
2. Click "Checks" tab
3. View "Code Quality Checks" results
4. If failed, click to see detailed error messages

### Fixing Failures

Common fixes:
```bash
# Fix shell script issues
shellcheck scripts/*.sh

# Fix YAML formatting
yamllint .

# Fix Ansible issues
ansible-lint playbooks/

# Fix all at once
make validate
```

---

## Workflow 2: Infrastructure Verification

**Purpose**: Test infrastructure creation without full cluster deployment

**Trigger**:
- Manual dispatch (Actions tab)
- PR with `test-infra` label

**Duration**: ~60-75 minutes

**Runs on**: Self-hosted runner (CI machine)

### What It Does

1. ✅ Pre-flight checks (resources, permissions)
2. ✅ Create infrastructure (`make environment`)
3. ✅ Provision Landing Zone (`make provision-landing-zone`)
4. ✅ Install Enclave Lab in connected mode (`make install-enclave`)
5. ✅ Verify installation
6. ✅ Collect artifacts (logs, environment metadata)
7. ✅ Optional cleanup

### How to Use

**Option 1: Manual Dispatch**

1. Go to Actions tab on GitHub
2. Select "Infrastructure Verification"
3. Click "Run workflow"
4. Choose cleanup strategy:
   - `on_failure` (recommended): Clean up only if test fails
   - `always`: Clean up after every run
   - `never`: Keep infrastructure for debugging

**Option 2: PR Label**

1. Add label `test-infra` to your PR
2. Workflow runs automatically
3. Remove label to prevent re-runs

### Cleanup Strategies

- **on_failure** (recommended): Keeps successful infrastructure for reuse, cleans failures
- **always**: Always cleans up, ensures fresh state
- **never**: Manual cleanup required, good for debugging

### Viewing Results

1. Go to Actions tab
2. Click on the workflow run
3. View step-by-step progress
4. Download artifacts (logs, environment.json)

### Artifacts Collected

- `environment.json` - Infrastructure metadata
- `vm-status.txt` - Virtual machine status
- `network-status.txt` - Network configuration
- `deployment.log` - Enclave Lab installation log

---

## Workflow 3: E2E Connected Mode

**Purpose**: Full end-to-end cluster deployment testing

**Trigger**:
- Manual dispatch (Actions tab)
- PR with `test-e2e` label
- Weekly schedule (Sunday 2 AM UTC)

**Duration**: ~90-120 minutes

**Runs on**: Self-hosted runner (CI machine)

### What It Does

1. ✅ Pre-flight checks
2. ✅ Create/reuse infrastructure
3. ✅ Provision Landing Zone
4. ✅ Install Enclave Lab (connected mode)
5. ✅ Deploy OpenShift cluster (`make deploy-cluster`)
6. ✅ Verify cluster health
7. ✅ Collect artifacts (kubeconfig, logs)
8. ✅ Optional cleanup

### How to Use

**Option 1: Manual Dispatch**

1. Go to Actions tab
2. Select "E2E Connected Mode"
3. Click "Run workflow"
4. Configure options:
   - **cleanup_strategy**: `on_failure` / `always` / `never`
   - **reuse_infrastructure**: `true` / `false`

**Option 2: PR Label**

1. Add label `test-e2e` to your PR
2. Workflow runs automatically

**Option 3: Scheduled**

Runs automatically every Sunday at 2 AM UTC for regression testing.

### Reuse Infrastructure

**Enabled (recommended)**:
- Reuses existing VMs if available
- Faster runs
- Good for iterative testing

**Disabled**:
- Creates fresh infrastructure every time
- Slower but ensures clean state
- Good for validating from scratch

### Cluster Verification

The workflow checks:
- ✅ Nodes are ready
- ✅ Cluster operators are available
- ✅ No degraded operators
- ✅ Kubeconfig is accessible

### Artifacts Collected

- `environment.json` - Infrastructure metadata
- `vm-status.txt` - Virtual machine status
- `network-status.txt` - Network configuration
- `deployment.log` - Deployment logs
- `kubeconfig` - Cluster access configuration
- `config/global.yaml` - Enclave Lab configuration

### Accessing the Cluster

After successful E2E run:

1. Download `kubeconfig` artifact
2. Use it to access the cluster:
   ```bash
   export KUBECONFIG=./kubeconfig
   oc get nodes
   oc get co
   ```

---

## Workflow 4: Cleanup

**Purpose**: Infrastructure cleanup and maintenance

**Trigger**:
- Manual dispatch (Actions tab)
- Weekly schedule (Sunday 4 AM UTC)

**Duration**: ~5-15 minutes

**Runs on**: Self-hosted runner (CI machine)

### Cleanup Levels

**Standard** (default):
- Runs `make clean`
- Removes Enclave test infrastructure
- Quick and safe

**Deep**:
- Standard cleanup
- Force destroy remaining VMs
- Remove networks
- Clean working directory

**Full**:
- Deep cleanup
- Stop sushy-tools containers
- Remove libvirt pools
- Clean dangling interfaces
- Nuclear option for stuck infrastructure

### How to Use

**Manual Cleanup**:

1. Go to Actions tab
2. Select "Cleanup Infrastructure"
3. Click "Run workflow"
4. Choose cleanup level

**Scheduled Cleanup**:

Runs automatically every Sunday at 4 AM UTC with standard cleanup.

### When to Use Each Level

- **Standard**: Regular cleanup after tests
- **Deep**: Infrastructure is stuck or behaving oddly
- **Full**: Complete reset needed, things are really broken

---

## Common Workflows

### Testing a PR

1. Create PR
2. PR Validation runs automatically
3. If changes are significant, add `test-infra` label
4. If testing cluster deployment, add `test-e2e` label

### Debugging Failed Tests

1. Check workflow logs in Actions tab
2. Download artifacts
3. Look for error messages in logs
4. If infrastructure is stuck, run cleanup workflow

### Weekly Regression Testing

E2E workflow runs automatically every Sunday at 2 AM UTC.
Reviews results Monday morning.

### Infrastructure Maintenance

Cleanup workflow runs automatically every Sunday at 4 AM UTC.
Prevents resource accumulation.

---

## Best Practices

### For Developers

✅ **DO**:
- Run `make validate` locally before pushing
- Use `test-infra` label for infrastructure changes
- Use `test-e2e` label sparingly (long running)
- Check workflow results before asking for review

❌ **DON'T**:
- Push without running validation
- Add `test-e2e` to every PR (expensive)
- Leave failed workflows without investigation
- Ignore cleanup failures

### For Reviewers

✅ **DO**:
- Check that PR Validation passed
- Request `test-infra` for infrastructure changes
- Request `test-e2e` for significant changes
- Review workflow artifacts if tests fail

---

## Troubleshooting

See [CI_TROUBLESHOOTING.md](CI_TROUBLESHOOTING.md) for detailed troubleshooting guide.

### Quick Fixes

**Workflow stuck in queue**:
- Check if another workflow is running
- Workflows queue on shared CI machine

**Workflow failed at pre-flight**:
- Check GitHub secrets are configured
- Verify runner is online (Settings → Actions → Runners)

**Infrastructure creation failed**:
- Check CI machine has enough resources
- Run cleanup workflow (deep level)

**Can't access cluster**:
- Download kubeconfig artifact
- Check deployment.log for errors
- SSH to Landing Zone for debugging

---

## Monitoring

### Check Runner Status

Settings → Actions → Runners
- Runner should show "Idle" when not running workflows
- If "Offline", check runner service on CI machine

### Check Workflow History

Actions tab → Select workflow → View runs
- See success/failure trends
- Download artifacts from past runs
- Review timing data

### Resource Usage

Monitor CI machine:
```bash
# Disk space
df -h /opt/dev-scripts

# RAM usage
free -g

# Running VMs
sudo virsh list --all

# Active networks
sudo virsh net-list --all
```

---

## Next Steps

- [CI Runner Setup](CI_RUNNER_SETUP.md) - Set up the self-hosted runner
- [CI Troubleshooting](CI_TROUBLESHOOTING.md) - Fix common issues
- [Deployment Guide](DEPLOYMENT_GUIDE.md) - Full deployment documentation

# Red Hat Sovereign Enclave

The Red Hat Sovereign Enclave (RHSE) is an optionally disconnected, infrastructure platform that delivers a cloud-like experience based on OpenShift. It consumes standards-based bare metal hosts and simplifies deployment by the Infrastructure Operator, requiring only low-touch participation.

RHSE provisions and maintains a local point of management (including ACM and Quay) with controls on the ingress of software and related artifacts into the environment.

This is an Open Source project. Contributions are welcome! (Contribution guide coming soon)

Check [Topo.png](docs/Topo.png) for expected hardware setup and [ArchMap.png](docs/ArchMap.png) for intended deployment model.

## Quick Start

1. Generate configuration files from the example files and fill in your values:

```bash
cp config/global.example.yaml config/global.yaml
cp config/certificates.example.yaml config/certificates.yaml
cp config/cloud_infra.example.yaml config/cloud_infra.yaml
vim config/global.yaml        # Fill in your cluster, network, and hardware settings
vim config/certificates.yaml  # Fill in your SSL certificates
vim config/cloud_infra.yaml   # Fill in your discovery hosts (or leave discovery_hosts: [])
```

2. Run the bootstrap script:

```bash
bash bootstrap.sh
```

## Advanced Usage

### Using Custom Variables Files

By default, Enclave Lab uses `config/global.yaml` in the repository root for all configuration. However, you can provide your own custom variables file:

**Default behavior:**
```bash
make deploy-cluster
# Uses config/global.yaml in repo root
```

**With custom vars file:**
```bash
VARS_FILE=config/custom-global.yaml make deploy-cluster
```

**Common use cases:**
- Testing different cluster configurations without modifying `config/global.yaml`
- Managing multiple environment configurations (dev, staging, prod)
- CI/CD pipelines with environment-specific variables
- Sharing a base configuration with per-deployment overrides

**Example:**
```bash
# Create custom vars for development environment
cp config/global.yaml config/dev-global.yaml
vim config/dev-global.yaml  # Modify as needed

# Deploy with custom vars
VARS_FILE=config/dev-global.yaml make deploy-cluster
```

**Note:** The custom vars file must be relative to the enclave directory on the Landing Zone (e.g., `config/custom-global.yaml`). If you need to use an absolute path, set `VARS_FILE=/absolute/path/to/global.yaml`.

### Deployment Modes

Enclave Lab supports two deployment modes: **connected** and **disconnected**.

#### Connected Mode

Skips mirror registry setup for faster deployments in environments with internet connectivity.

**When to use:**
- Development and testing environments
- Sites with reliable internet access to Red Hat registries
- Faster iteration during development

**Usage:**
```bash
ENCLAVE_DEPLOYMENT_MODE=connected make deploy-cluster
```

**What happens:**
- Phase 1: Download binaries and content
- Phase 2: **SKIPPED** (no mirror registry or image mirroring)
- Phase 3: Deploy cluster (pulls from upstream registries)
- Phase 4: Post-installation configuration
- Phase 5: Install and configure operators
- Phase 6: Day-2 operations (Clair, ACM policies, model config)
- Phase 7: Configure hardware discovery

#### Disconnected Mode (Default)

Full air-gapped deployment with local mirror registry for production environments.

**When to use:**
- Production edge deployments
- Air-gapped or restricted network environments
- Compliance requirements for disconnected operation
- Full validation before production

**Usage:**
```bash
make deploy-cluster
# or explicitly:
ENCLAVE_DEPLOYMENT_MODE=disconnected make deploy-cluster
```

**What happens:**
- Phase 1: Download binaries and content
- Phase 2: Create local Quay registry and mirror all required images
- Phase 3: Deploy cluster (uses local mirror registry)
- Phase 4: Post-installation configuration
- Phase 5: Install and configure operators
- Phase 6: Day-2 operations (Clair, ACM policies, model config)
- Phase 7: Configure hardware discovery

## Documentation

Comprehensive documentation is available in the `docs/` folder:

- **[Deployment Guide](docs/DEPLOYMENT_GUIDE.md)**: Complete guide on what gets deployed, prerequisites, and deployment workflow
- **[Configuration Reference](docs/CONFIGURATION_REFERENCE.md)**: Detailed explanation of all configuration variables with examples

> **Note on Host Discovery Management:** While Phase 7 allows you to configure hardware discovery through the Enclave configuration as a one-time convenience, **Red Hat Advanced Cluster Management (ACM) is the recommended approach for managing bare metal host discovery and lifecycle operations** in production. For adding, removing, or modifying nodes after initial deployment, use ACM. See the [Deployment Guide](docs/DEPLOYMENT_GUIDE.md#discovering-new-nodes) for details.

## Local Development & Testing

### Prerequisites

- **dev-scripts** installed and configured
- **libvirt/KVM** with sufficient resources (64GB+ RAM recommended)
- **Required tools**: shellcheck, yamllint, ansible-lint, make, jq
- **Environment variables**:
  - `DEV_SCRIPTS_PATH`: Path to your dev-scripts installation

### Make Targets Reference

#### Validation Targets
```bash
make validate              # Run all validation checks
make validate-shell        # Validate shell scripts with shellcheck
make validate-yaml         # Validate YAML files with yamllint
make validate-ansible      # Validate Ansible playbooks with ansible-lint
make validate-makefile     # Validate Makefile syntax
```

#### Infrastructure Targets
```bash
make environment                     # Create test infrastructure
make provision-landing-zone          # Provision Landing Zone VM
make verify-landing-zone             # Verify Landing Zone VM configuration
make install-enclave                 # Install Enclave Lab
make verify-enclave-installation     # Verify Enclave Lab installation
make deploy-cluster                  # Deploy OpenShift cluster (all phases)
make verify                          # Verify infrastructure setup
make clean                           # Clean up all infrastructure
```

#### Verification Targets
```bash
make verify-cluster                  # Verify OpenShift cluster deployment
make verify-cleanup                  # Verify infrastructure cleanup
```

#### Helper Targets
```bash
make generate-cluster-name           # Generate unique cluster name (auto-called)
make setup-working-dir               # Setup cluster-specific working directory
make collect-step-logs               # Collect logs from dev-scripts and cluster
make preflight-checks                # Run pre-flight environment checks
make collect-artifacts-basic         # Collect basic artifacts
make collect-artifacts-deployment    # Collect deployment artifacts
make collect-artifacts-full          # Collect all artifacts
```

#### Local CI Testing Targets
```bash
make ci-flow-connected               # Run full CI flow locally (connected mode)
make ci-flow-disconnected            # Run full CI flow locally (disconnected mode)
```

#### Deploy Individual Phases (for granular control)
```bash
make deploy-cluster-prepare          # Phase 1: Download binaries
make deploy-cluster-mirror           # Phase 2: Mirror registry (disconnected)
make deploy-cluster-install          # Phase 3: Deploy cluster
make deploy-cluster-post-install     # Phase 4: Cluster configuration
make deploy-cluster-operators        # Phase 5: Install operators
make deploy-cluster-day2             # Phase 6: Day-2 operations
make deploy-cluster-discovery        # Phase 7: Configure hardware discovery
```

### Common Testing Workflows

#### Quick Validation (Before Every Commit)
```bash
# ALWAYS run this before committing
make validate
```

#### Run Full CI Flow Locally

You can run the complete CI workflow locally to test changes before pushing:

**Automatic cluster name generation:**
```bash
export DEV_SCRIPTS_PATH=/path/to/dev-scripts
export BASE_WORKING_DIR=/opt/clusters

# Connected mode (faster for development)
make ci-flow-connected

# Disconnected mode (full validation)
make ci-flow-disconnected
```

**With custom cluster name:**
```bash
export ENCLAVE_CLUSTER_NAME=my-test-cluster
export DEV_SCRIPTS_PATH=/path/to/dev-scripts
export BASE_WORKING_DIR=/opt/clusters

make ci-flow-connected
```

The `ci-flow-*` targets automatically:
1. Run preflight checks
2. Setup working directory (with auto-generated cluster name if needed)
3. Create infrastructure
4. Provision Landing Zone
5. Install Enclave Lab
6. Deploy cluster
7. Verify cluster deployment

**Benefits:**
- Test the same flow that runs in GitHub Actions
- Debug issues locally before CI runs
- Faster iteration than waiting for CI
- Full control over environment

See [Local CI Testing Guide](docs/LOCAL_TESTING.md) for detailed usage.

#### Test Infrastructure Creation
```bash
export DEV_SCRIPTS_PATH=/path/to/dev-scripts

# Create VMs, networks, and BMC emulation
make environment

# Verify infrastructure
virsh list --all
virsh net-list
```

#### Test Landing Zone Provisioning
```bash
# Provision CentOS Stream 10 and verify
# Automatically runs verify-landing-zone after provisioning
make provision-landing-zone
```

#### Test Enclave Installation

**Connected Mode (Recommended for Development):**
```bash
# No mirroring, uses upstream registries
ENCLAVE_DEPLOYMENT_MODE=connected make install-enclave
```

**Disconnected Mode (Production Validation):**
```bash
# Full mirroring to local registry
make install-enclave
```

#### Test Cluster Deployment
```bash
# Deploy OpenShift cluster
make deploy-cluster
```

#### Full End-to-End Test
```bash
# Complete workflow from scratch

# 1. Create infrastructure
export DEV_SCRIPTS_PATH=/path/to/dev-scripts
make environment

# 2. Provision Landing Zone
make provision-landing-zone

# 3. Install Enclave Lab - Choose mode:
ENCLAVE_DEPLOYMENT_MODE=connected make install-enclave  # Connected mode
# OR
make install-enclave  # Disconnected mode

# 4. Deploy cluster
make deploy-cluster

# 5. Clean up when done
make clean
```

### Component-Specific Testing

#### Test Only Validation
```bash
# All validators
make validate

# Individual validators
make validate-shell      # Only shell scripts
make validate-yaml       # Only YAML files
make validate-ansible    # Only Ansible playbooks
make validate-makefile   # Only Makefile syntax
```

#### Test Only Infrastructure
```bash
# Create infrastructure and stop
make environment

# Manual verification
virsh list --all              # Check VMs created
virsh net-list               # Check networks active
curl http://100.64.1.1:8000/redfish/v1/Systems  # Check BMC emulation
```

#### Test Only Landing Zone
```bash
# Assumes infrastructure exists from 'make environment'
make provision-landing-zone

# Manual verification
ssh cloud-user@<landing-zone-ip>
# Check: CentOS Stream 10, podman installed, network configured
```

#### Test Only Enclave Installation
```bash
# Assumes Landing Zone is provisioned
make install-enclave
make verify-enclave-installation

# Manual verification
ssh cloud-user@<landing-zone-ip>
ls -la /home/cloud-user/enclave
cat /home/cloud-user/enclave/config/global.yaml
cat /home/cloud-user/enclave/config/certificates.yaml
cat /home/cloud-user/enclave/config/cloud_infra.yaml
```

#### Test Only Cluster Deployment
```bash
# Assumes Enclave Lab is installed
make deploy-cluster

# Monitor progress
ssh cloud-user@<landing-zone-ip>
tail -f /home/cloud-user/enclave/deployment.log
```

### Troubleshooting Local Tests

**Infrastructure creation fails:**
```bash
# Check dev-scripts path
echo $DEV_SCRIPTS_PATH
ls -la $DEV_SCRIPTS_PATH

# Check libvirt
sudo systemctl status libvirtd
virsh list --all

# Check resources
free -h  # Need 64GB+ RAM
df -h    # Need 100GB+ disk
```

**Landing Zone provisioning fails:**
```bash
# Check VM state
virsh list --all | grep landingzone

# Check console
virsh console enclave-test_landingzone_0

# Check logs on Landing Zone
ssh cloud-user@<ip> journalctl -xef
```

**Enclave installation fails:**
```bash
# Check Landing Zone connectivity
ping <landing-zone-ip>
ssh cloud-user@<landing-zone-ip>

# Check installation logs
make verify-enclave-installation

# Re-run with fresh state
ssh cloud-user@<landing-zone-ip>
rm -rf /home/cloud-user/enclave
# Then re-run: make install-enclave
```

**Cluster deployment fails:**
```bash
# Check deployment logs
ssh cloud-user@<landing-zone-ip>
tail -100 /home/cloud-user/enclave/deployment.log

# Check cluster state
ssh cloud-user@<landing-zone-ip>
export KUBECONFIG=/home/cloud-user/enclave/auth/kubeconfig
oc get nodes
oc get co  # Cluster operators
```

**Cleanup issues:**
```bash
# Force cleanup
make clean

# Manual cleanup if needed
cd $DEV_SCRIPTS_PATH
CONFIG=config_enclave.sh make clean

# Nuclear option (removes everything)
virsh list --all | grep enclave-test | awk '{print $2}' | xargs -I {} virsh destroy {}
virsh list --all | grep enclave-test | awk '{print $2}' | xargs -I {} virsh undefine {}
```

### Development Best Practices

✅ **DO:**
- Run `make validate` before every commit
- Test in connected mode for faster iteration during development
- Use disconnected mode for final validation before PR
- Clean up infrastructure regularly to free resources
- Check logs when tests fail before asking for help
- Test your changes in isolation first

❌ **DON'T:**
- Skip validation (CI will catch it anyway)
- Commit without testing locally
- Leave infrastructure running when not in use
- Test in disconnected mode for every small change
- Forget to set required environment variables

**Typical Development Cycle:**
1. Make code changes
2. Run `make validate` ✅
3. Test specific component if needed
4. Commit and push
5. GitHub Actions validates automatically

## Continuous Integration

Enclave Lab uses GitHub Actions for automated testing and validation with four main workflows. All CI workflows use the same Makefile targets that you can run locally, ensuring consistency between local testing and CI.

### Available Workflows

#### 1. PR Validation (Automatic)

Every pull request automatically runs:
- ✅ Shell script validation (shellcheck)
- ✅ YAML linting (yamllint)
- ✅ Ansible playbook validation (ansible-lint)
- ✅ Makefile syntax checking

**Test locally before pushing:**
```bash
make validate
```

Runs on GitHub-hosted runners for fast feedback.

#### 2. Infrastructure Verification (Manual/Label)

Tests infrastructure setup without full cluster deployment:
- Create test infrastructure
- Provision Landing Zone
- Install Enclave Lab (connected mode)
- Verify installation

**Trigger**: Manual or add `test-infra` label to PR

#### 3. E2E Connected Mode (Manual/Label/Scheduled)

Full end-to-end cluster deployment testing:
- Complete infrastructure setup
- Deploy OpenShift cluster
- Verify cluster health
- Collect artifacts (kubeconfig, logs)

**Trigger**: Manual, add `test-e2e` label to PR, or weekly schedule (Sunday 2 AM UTC)

#### 4. Cleanup (Manual/Scheduled)

Infrastructure cleanup and maintenance:
- Standard: Clean Enclave infrastructure
- Deep: Force remove stuck resources
- Full: Complete reset

**Trigger**: Manual or weekly schedule (Sunday 4 AM UTC)

### CI Documentation

For detailed information about using and troubleshooting CI workflows:
- **[CI Workflows Guide](docs/CI_WORKFLOWS.md)**: How to use each workflow
- **[CI Runner Setup](docs/CI_RUNNER_SETUP.md)**: Self-hosted runner configuration
- **[CI Troubleshooting](docs/CI_TROUBLESHOOTING.md)**: Common issues and solutions

### Branch Protection

The `main` branch is protected and requires:
- ✅ PR validation checks to pass
- ✅ Code review approval
- ✅ Up-to-date branch with main

### Automated Code Review

Pull requests are automatically reviewed by [CodeRabbit AI](https://coderabbit.ai) for:
- JIRA task ID validation in PR titles and commits
- Security checks (credentials, hardcoded paths, secrets)
- Best practices and code quality

CodeRabbit provides inline suggestions and auto-generates JIRA issue links in PR summaries.

## Architecture

- **Topology**: See `Topology.pdf` for hardware setup and network configuration
- **Architecture**: See `ArchMap.png` for deployment model and component relationships
- **Makefile**: Automation targets for testing and deployment
- **Scripts**: Helper scripts for provisioning, installation, and verification
- **Playbooks**: Modular Ansible playbooks for deployment phases

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. **Run `make validate`** to ensure code quality
5. Test your changes locally (see Local Development & Testing section)
6. Commit with clear messages
7. Push and create a Pull Request
8. Automated validation will run on your PR

## Support

- **Documentation**: Check `docs/` folder for detailed guides.

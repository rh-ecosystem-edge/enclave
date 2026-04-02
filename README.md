# Red Hat Sovereign Enclave

[![Nightly E2E - Connected Mode](https://github.com/rh-ecosystem-edge/enclave/actions/workflows/nightly-e2e-connected.yml/badge.svg?branch=main)](https://github.com/rh-ecosystem-edge/enclave/actions/workflows/nightly-e2e-connected.yml)
[![Nightly E2E - Disconnected Mode](https://github.com/rh-ecosystem-edge/enclave/actions/workflows/nightly-e2e-disconnected.yml/badge.svg?branch=main)](https://github.com/rh-ecosystem-edge/enclave/actions/workflows/nightly-e2e-disconnected.yml)
[![Build and Push Tarball](https://github.com/rh-ecosystem-edge/enclave/actions/workflows/build-push-tarball.yml/badge.svg?branch=main)](https://github.com/rh-ecosystem-edge/enclave/actions/workflows/build-push-tarball.yml)

The Red Hat Sovereign Enclave (RHSE) is an optionally disconnected, infrastructure platform that delivers a cloud-like experience based on OpenShift. It consumes standards-based bare metal hosts and simplifies deployment by the Infrastructure Operator, requiring only low-touch participation.

RHSE provisions and maintains a local point of management (including ACM and Quay) with controls on the ingress of software and related artifacts into the environment.

This is an Open Source project. Contributions are welcome! See the [Contributing](#contributing) section below.

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
GLOBAL_VARS=config/custom-global.yaml make deploy-cluster
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
GLOBAL_VARS=config/dev-global.yaml make deploy-cluster
```

**Note:** The custom vars file must be relative to the enclave directory on the Landing Zone (e.g., `config/custom-global.yaml`). If you need to use an absolute path, set `GLOBAL_VARS=/absolute/path/to/global.yaml`.

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
make deploy-cluster-connected
# or equivalently:
DISCONNECTED=false make deploy-cluster
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
# Disconnected is the default (DISCONNECTED=true)
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

There are two Makefiles:
- **`Makefile`** — runs directly on the Landing Zone (deploy, bootstrap, sync)
- **`Makefile.ci`** — CI infrastructure and validation targets (use `make -f Makefile.ci <target>`)

#### Landing Zone Targets (`Makefile`)

```bash
# Deployment
make deploy-cluster                  # Deploy OpenShift cluster (all phases)
make deploy-cluster-connected        # Deploy in connected mode (DISCONNECTED=false)
make deploy-cluster-prepare          # Phase 1: Download binaries
make deploy-cluster-mirror           # Phase 2: Mirror registry (disconnected)
make deploy-cluster-install          # Phase 3: Deploy cluster
make deploy-cluster-post-install     # Phase 4: Cluster configuration
make deploy-cluster-operators        # Phase 5: Install operators
make deploy-cluster-day2             # Phase 6: Day-2 operations
make deploy-cluster-discovery        # Phase 7: Configure hardware discovery
make deploy-plugin PLUGIN=<name>     # Deploy a single plugin

# Setup & utilities
make bootstrap                       # Bootstrap the Landing Zone
make sync                            # Sync configuration
make setup                           # Install system packages and Ansible deps
make validate-config                 # Validate configuration files
make validate-schema                 # Validate configuration against JSON schemas
```

#### CI Targets (`Makefile.ci`)

```bash
# Validation
make -f Makefile.ci validate              # Run all validation checks
make -f Makefile.ci validate-shell        # Validate shell scripts with shellcheck
make -f Makefile.ci validate-yaml         # Validate YAML files with yamllint
make -f Makefile.ci validate-ansible      # Validate Ansible playbooks with ansible-lint
make -f Makefile.ci validate-makefile     # Validate Makefile syntax

# Infrastructure
make -f Makefile.ci environment                     # Create test infrastructure
make -f Makefile.ci provision-landing-zone          # Provision Landing Zone VM
make -f Makefile.ci install-enclave                 # Install Enclave Lab
make -f Makefile.ci clean                           # Clean up all infrastructure

# Verification
make -f Makefile.ci verify-cluster                  # Verify OpenShift cluster deployment
make -f Makefile.ci verify-cleanup                  # Verify infrastructure cleanup

# Full CI flows
make -f Makefile.ci ci-flow-connected               # Run full CI flow locally (connected)
make -f Makefile.ci ci-flow-disconnected            # Run full CI flow locally (disconnected)

# Helpers
make -f Makefile.ci collect-artifacts-full          # Collect all artifacts
make -f Makefile.ci preflight-checks                # Run pre-flight environment checks
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
DISCONNECTED=false make install-enclave
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
DISCONNECTED=false make install-enclave  # Connected mode
# OR
make install-enclave  # Disconnected mode

# 4. Deploy cluster
make deploy-cluster

# 5. Clean up when done
make clean
```

For component-specific testing, troubleshooting, and development best practices, see [Local Testing Guide](docs/LOCAL_TESTING.md).

## Continuous Integration

GitHub Actions workflows:

1. **PR Validation** (automatic) — shellcheck, yamllint, ansible-lint, Makefile syntax. Test locally: `make -f Makefile.ci validate`
2. **Infrastructure Verification** (manual / `test-infra` label) — infra setup without full cluster deploy
3. **E2E Connected Mode** (manual / `test-e2e` label / weekly) — full end-to-end cluster deployment
4. **Cleanup** (manual / weekly) — infrastructure teardown

The `main` branch requires passing PR validation, code review approval, and an up-to-date branch.

Pull requests are automatically reviewed by [CodeRabbit AI](https://coderabbit.ai) for security and code quality.

For details see **[CI Workflows Guide](docs/CI_WORKFLOWS.md)** and **[CI Runner Setup](docs/CI_RUNNER_SETUP.md)**.

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

# Scripts Directory

This directory contains all automation scripts for deploying and managing OpenShift clusters using the Enclave framework with Agent-Based Installer (ABI).

## Directory Structure

The scripts are organized into logical subdirectories based on their function in the deployment lifecycle:

```
scripts/
├── lib/            - Shared utility libraries (sourced by other scripts)
├── setup/          - Initial setup and configuration
├── infrastructure/ - Infrastructure provisioning and network setup
├── deployment/     - Cluster deployment and installation
├── verification/   - Validation, testing, and artifact collection
├── cleanup/        - Teardown and resource cleanup
├── utils/          - Standalone utility scripts
└── diagnostics/    - Troubleshooting and log collection (must-gather)
```

## Subdirectories

### `lib/` - Shared Utilities

Reusable library functions sourced by other scripts to eliminate code duplication:

- **`output.sh`** - Color codes and logging functions (`info`, `error`, `warning`, `success`)
- **`validation.sh`** - Environment variable validation and prerequisite checks
- **`config.sh`** - Configuration file loading and JSON parsing utilities
- **`network.sh`** - Network operations (IP detection, subnet calculations, BMC port mapping)
- **`ssh.sh`** - SSH connection setup and remote command execution helpers
- **`common.sh`** - Common patterns (directory detection, working directory construction)

**Usage Example:**
```bash
#!/bin/bash
set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"

# Use utility functions
require_env_var "DEV_SCRIPTS_PATH"
info "Starting process..."
success "Operation completed"
```

---

### `setup/` - Initial Setup & Configuration

Scripts for initial environment setup, prerequisite validation, and configuration generation.

| Script | Purpose |
|--------|---------|
| `preflight_checks.sh` | Validate environment variables and system resources before workflow execution |
| `validate_prerequisites.sh` | Check all required tools are installed (jq, ansible, virsh, etc.) |
| `setup_working_dir.sh` | Create and initialize cluster working directory structure |
| `configure_devscripts.sh` | Generate dev-scripts configuration file with network settings |
| `allocate_subnet.sh` | Atomic subnet allocation for parallel CI runs (file locking) |
| `generate_cluster_name.sh` | Generate unique cluster name based on environment |

**Typical Workflow:**
```bash
make preflight-checks        # Validate environment
make setup-working-dir       # Create working directory
make configure-devscripts    # Generate dev-scripts config
```

---

### `infrastructure/` - Infrastructure Provisioning

Scripts for creating VMs, networks, and infrastructure components.

| Script | Purpose |
|--------|---------|
| `provision_landing_zone.sh` | Create Landing Zone VM using dev-scripts |
| `verify_networks.sh` | Validate libvirt networks are configured correctly |
| `start_sushy_tools.sh` | Start Redfish BMC emulator (sushy-tools) for cluster VMs |
| `generate_environment_json.sh` | Generate infrastructure metadata (IPs, MACs, networks) |
| `generate_enclave_vars.sh` | Generate Enclave Lab configuration files (global.yaml, certificates.yaml, cloud_infra.yaml) |

**Key Concepts:**
- **Landing Zone**: VM that runs Enclave Lab ansible playbooks
- **BMC Emulation**: sushy-tools provides Redfish API for VM power control
- **environment.json**: Contains all infrastructure metadata (networks, VMs, IPs)

**Typical Workflow:**
```bash
make environment             # Create all infrastructure
make verify-networks         # Validate network configuration
make start-sushy-tools       # Start BMC emulator
```

---

### `deployment/` - Cluster Deployment

Scripts for deploying OpenShift clusters on the provisioned infrastructure.

| Script | Purpose |
|--------|---------|
| `install_enclave.sh` | Install Enclave Lab on Landing Zone VM and generate configuration |
| `deploy_cluster.sh` | Deploy full OpenShift cluster (connected or disconnected mode) |
| `deploy_phase.sh` | Deploy specific phase of deployment (for phased workflows) |

**Deployment Modes:**
- **Connected**: Pull images directly from quay.io/registry.redhat.io (~55 min)
- **Disconnected**: Mirror images to local Quay registry (~110 min, air-gapped)

**Typical Workflow:**
```bash
make install-enclave         # Install Enclave Lab on Landing Zone
make deploy-cluster          # Deploy cluster (full workflow)
# OR for phased deployment:
make deploy-cluster-prepare  # Download binaries
make deploy-cluster-connected # Deploy without mirroring
```

---

### `verification/` - Validation & Testing

Scripts for verifying deployments, collecting logs, and running validation checks.

| Script | Purpose |
|--------|---------|
| `verify_infrastructure.sh` | Validate infrastructure (VMs, networks, sushy-tools) |
| `verify_landing_zone.sh` | Verify Landing Zone VM is accessible and configured |
| `verify_enclave_installation.sh` | Verify Enclave Lab is installed correctly on Landing Zone |
| `verify_cluster.sh` | Verify OpenShift cluster is deployed and healthy |
| `validate.sh` | Run all validation checks in sequence |
| `collect_ci_artifacts.sh` | Collect logs and artifacts for CI (GitHub Actions) |
| `collect_step_logs.sh` | Collect logs from specific deployment step |

**Usage:**
```bash
make verify-infrastructure   # Check VMs and networks
make verify-landing-zone     # Check Landing Zone
make verify-cluster          # Check cluster deployment
make verify                  # Run all verification checks
```

---

### `cleanup/` - Teardown & Resource Cleanup

Scripts for cleaning up clusters, VMs, and allocated resources.

| Script | Purpose |
|--------|---------|
| `cleanup.sh` | Clean up all resources for a cluster (VMs, networks, storage, firewall) |
| `cleanup_orphaned_resources.sh` | Clean up leftover resources from failed/interrupted workflows |
| `verify_cleanup.sh` | Verify all resources were cleaned up successfully |

**Cleanup includes:**
- Libvirt VMs and networks
- Storage pools and volumes
- Firewall rules (BMC ports)
- Subnet allocations
- Working directories

**Usage:**
```bash
make clean                   # Full cleanup
make verify-cleanup          # Verify cleanup succeeded
```

---

### `utils/` - Utility Scripts

Standalone utility scripts used by other scripts.

| Script | Purpose |
|--------|---------|
| `get_landing_zone_ip.sh` | Get Landing Zone VM IP address (with dynamic subnet detection) |
| `with_libvirt_lock.sh` | Execute command with exclusive libvirt lock (prevents race conditions) |

**Example:**
```bash
# Get Landing Zone IP
LZ_IP=$(./scripts/utils/get_landing_zone_ip.sh)

# Run command with exclusive lock
./scripts/utils/with_libvirt_lock.sh virsh net-create network.xml
```

---

### `diagnostics/` - Troubleshooting & Log Collection

Must-gather scripts for collecting diagnostic information from deployments.

| Script | Purpose |
|--------|---------|
| `gather.sh` | Collect all diagnostic data (infrastructure + cluster) |
| `gather_lz.sh` | Collect logs from Landing Zone VM |
| `gather_cluster.sh` | Collect logs from OpenShift cluster |
| `README.md` | Documentation for diagnostic collection |

**Usage:**
```bash
# Collect all diagnostics
./scripts/diagnostics/gather.sh

# Outputs tarball: must-gather-<cluster>-<timestamp>.tar.gz
```

---

## Script Conventions

### Logging (from `lib/output.sh`)

All scripts use standardized logging functions:

```bash
info "Informational message"      # Green, to stderr
warning "Warning message"          # Yellow, to stderr
error "Error message"              # Red, to stderr
success "Success message"          # Green with ✓, to stderr
output "Data or summary"           # Plain text, to stdout (for GitHub Actions)
```

**Note**: Logging functions write to stderr to avoid interfering with data output captured in `$(command)` substitutions.

### Environment Variables

Common environment variables used across scripts:

| Variable | Purpose |
|----------|---------|
| `ENCLAVE_CLUSTER_NAME` | Cluster name (default: `enclave-test`) |
| `DEV_SCRIPTS_PATH` | Path to dev-scripts installation |
| `WORKING_DIR` | Cluster working directory |
| `BASE_WORKING_DIR` | Base directory for all clusters |
| `ENCLAVE_DEPLOYMENT_MODE` | Deployment mode: `connected` or `disconnected` |
| `ENCLAVE_SUBNET_ID` | Allocated subnet ID (for parallel CI) |
| `ENCLAVE_BMC_NETWORK` | BMC network CIDR (e.g., `100.64.3.0/24`) |
| `ENCLAVE_CLUSTER_NETWORK` | Cluster network CIDR (e.g., `192.168.3.0/24`) |

### Error Handling

Scripts use `set -euo pipefail` for strict error handling:
- `set -e`: Exit on error
- `set -u`: Exit on undefined variable
- `set -o pipefail`: Fail on pipe errors

---

## CI/CD Integration

Scripts are used by GitHub Actions workflows:

- `.github/workflows/e2e-deployment.yml` - End-to-end deployment workflow (connected and disconnected modes)

Custom GitHub Actions in `.github/actions/`:
- `allocate-subnet` - Allocate unique subnet for parallel runs
- `preflight-checks` - Run pre-flight validation
- `collect-artifacts` - Collect logs and artifacts

---

## Development Guidelines

### Adding New Scripts

1. Place script in appropriate subdirectory based on function
2. Use shared utilities from `scripts/lib/` instead of duplicating code
3. Follow naming convention: `verb_noun.sh` (e.g., `verify_cluster.sh`)
4. Include header comment with description and usage
5. Use `set -euo pipefail` for error handling
6. Add to Makefile if it should be a make target

### Example Script Template

```bash
#!/bin/bash
# Brief description of what this script does
#
# Usage: ./script_name.sh [OPTIONS]
#
# Environment Variables:
#   VAR_NAME - Description

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"

# Validate required environment variables
require_env_var "REQUIRED_VAR"

# Main logic
info "Starting operation..."
# ... script logic ...
success "Operation completed"
```

---

## Troubleshooting

### Common Issues

**Script not found after reorganization:**
- Check Makefile - paths updated to new subdirectory structure
- Use `scripts/subdirectory/script.sh` instead of `scripts/script.sh`

**Import errors (cannot find utility library):**
- Ensure `ENCLAVE_DIR` is set correctly relative to script location
- Check that library files exist in `scripts/lib/`

**Permission denied:**
- Ensure scripts have execute permission: `chmod +x scripts/path/to/script.sh`

### Getting Help

- Check script header comments for usage information
- Review `docs/LOCAL_TESTING.md` for local testing workflows
- Review `docs/CI_WORKFLOWS.md` for CI workflow documentation
- See individual README files in subdirectories (e.g., `diagnostics/README.md`)

---

## Related Documentation

- [LOCAL_TESTING.md](../docs/LOCAL_TESTING.md) - Local testing workflows
- [CI_WORKFLOWS.md](../docs/CI_WORKFLOWS.md) - CI workflow documentation
- [diagnostics/README.md](diagnostics/README.md) - Diagnostic collection guide

# AGENTS.md

This file provides guidance when working with code in this repository.

## Project Overview

Red Hat Sovereign Enclave (RHSE) is an infrastructure automation framework for deploying OpenShift Container Platform (OCP) on bare metal, supporting both connected and disconnected (air-gapped) environments. It uses Ansible as the primary automation engine, shell scripts for infrastructure management, and a 7-phase deployment pipeline.

## Local Development Environment

Ansible and all required tools are available in a local venv:

```bash
source .venv/bin/activate
```

Activate before running any validation or deployment commands locally.

## Validation Commands

Run these before submitting changes:

```bash
make -f Makefile.ci validate              # Run all validators
make -f Makefile.ci validate-shell        # Shellcheck on all shell scripts
make -f Makefile.ci validate-yaml         # Yamllint on all YAML files
make -f Makefile.ci validate-ansible      # Ansible-lint on playbooks
make -f Makefile.ci validate-json-schema  # JSON schema validation
make -f Makefile.ci validate-tags         # Validate Ansible playbook tags
make -f Makefile.ci validate-templates    # Validate Jinja2 template rendering
make -f Makefile.ci validate-makefile     # Makefile syntax check
```

## Deployment Commands

```bash
# Full deployment (disconnected mode, default)
make deploy-cluster

# Full deployment (connected mode, skips mirror registry)
ENCLAVE_DEPLOYMENT_MODE=connected make deploy-cluster

# Custom configuration file
VARS_FILE=config/custom-global.yaml make deploy-cluster

# Individual deployment phases
make deploy-cluster-prepare       # Phase 1: Download binaries & content
make deploy-cluster-mirror        # Phase 2: Setup local mirror registry
make deploy-cluster-install       # Phase 3: Deploy OCP cluster
make deploy-cluster-post-install  # Phase 4: Cluster configuration
make deploy-cluster-operators     # Phase 5: Install operators
make deploy-cluster-day2          # Phase 6: Advanced features
make deploy-cluster-discovery     # Phase 7: Hardware discovery
```

## Local Testing Infrastructure

```bash
make environment                  # Create test VMs, networks, BMC emulation (libvirt/KVM)
make provision-landing-zone       # Provision Landing Zone VM
make verify-landing-zone          # Verify Landing Zone config
make install-enclave              # Install Enclave on Landing Zone
make verify-enclave-installation  # Verify installation
make verify                       # Verify infrastructure setup
make clean                        # Teardown infrastructure

# Full CI flow simulation
make ci-flow-connected            # Connected mode E2E
make ci-flow-disconnected         # Disconnected mode E2E
```

Requires `DEV_SCRIPTS_PATH` environment variable pointing to a dev-scripts installation.

## Architecture

### 7-Phase Deployment Pipeline

Orchestrated by `playbooks/main.yaml`:

1. **Prepare** (`01-prepare.yaml`): Downloads OpenShift binaries and content images
2. **Mirror** (`02-mirror.yaml`): Sets up local Quay registry and mirrors images (disconnected only)
3. **Deploy** (`03-deploy.yaml`): Agent-Based Installer (ABI) deployment of OCP
4. **Post-Install** (`04-post-install.yaml`): SSL certificates, DNS, registry configuration
5. **Operators** (`05-operators.yaml`): Installs 18+ Red Hat operators
6. **Day-2** (`06-day2.yaml`): Advanced features, Clair, ACM policies, ML model config
7. **Discovery** (`07-configure-discovery.yaml`): Ironic and hardware discovery setup

### Key Directories

- `playbooks/tasks/` - 27 reusable Ansible task files shared across phases
- `playbooks/templates/` - Jinja2 templates for generating cluster configs
- `operators/` - Per-operator directories with subscription and config manifests
- `defaults/` - YAML defaults for operators, catalogs, binaries, images
- `config/` - User-provided configuration (copy from `*.example.yaml` files)
- `scripts/lib/` - Shared Bash libraries: `output.sh`, `config.sh`, `network.sh`, `ssh.sh`, `validation.sh`, `common.sh`

### Configuration System

User configuration lives in `config/` (gitignored). Start from examples:

```bash
cp config/global.example.yaml config/global.yaml
cp config/certificates.example.yaml config/certificates.yaml
cp config/cloud_infra.example.yaml config/cloud_infra.yaml
```

Defaults in `defaults/` are merged with user config. The `defaults/operators.yaml` defines which operators are installed and their configurations.

### Operator Configuration Pattern

Each operator in `operators/<name>/` contains:
- `subscription.yaml` - OLM Subscription manifest
- `operator_group.yaml` (if needed) - OperatorGroup manifest
- Additional config manifests specific to the operator

Operators are enabled/disabled and configured via `defaults/operators.yaml`.

### Shell Script Organization

`scripts/` is organized by function:
- `setup/` - Environment prerequisites and validation
- `infrastructure/` - VM provisioning, BMC emulation (sushy-tools), network setup
- `deployment/` - Cluster deployment orchestration
- `verification/` - Health checks and artifact collection
- `cleanup/` - Infrastructure teardown
- `utils/` - Concurrency control (`with_libvirt_lock.sh`), IP detection
- `diagnostics/` - Troubleshooting and log collection

### Deployment Modes

- **Disconnected** (default): Full air-gapped with local Quay mirror registry; mirrors all required images locally before deployment
- **Connected**: Skips mirror phase; pulls directly from Red Hat registries; used for faster development iteration

## Code Quality Rules

- Shell scripts: Must pass `shellcheck` (no exceptions)
- YAML: 200-character line limit; `yamllint` with `.yamllint.yml` config
- Ansible: `ansible-lint` with `basic` profile; config in `.ansible-lint`
- Avoid trailing whitespace and lines with only whitespace/tabs

## Ansible Collections

Defined in `requirements.yml`. Install with:

```bash
ansible-galaxy collection install -r requirements.yml
```

Required: `ansible.utils`, `kubernetes.core`, `containers.podman`, `community.crypto`, `community.general`

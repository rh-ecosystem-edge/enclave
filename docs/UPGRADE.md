# Enclave Upgrade Guide

## Overview

An Enclave upgrade is the process of updating the deployed infrastructure to a new Enclave release. Each Enclave tarball is distributed with a tested combination of:

1. **Enclave automation** (playbooks, scripts, configuration)
2. **Landing Zone components** (mirror-registry, metal3 stack, packages, binaries, etc.)
3. **Management Cluster OpenShift version** (e.g., 4.20.8)
4. **Operator versions** (ACM, Quay, etc.)

The tarball contains all version definitions in `defaults/*.yaml` files and is the artifact as-is—a complete, tested release.

### The Upgrade Process

Upgrading to a new Enclave release follows this sequence:

1. **Deploy new Enclave tarball** - Extract new release to Landing Zone, preserving your `config/*.yaml` customizations
2. **Sync/Mirror new content** - Run sync process to download and mirror new OpenShift and operator images to local registry (disconnected mode)
3. **Upgrade Landing Zone components** - Update components to the versions specified in the new tarball
4. **Upgrade the management cluster** - Update OpenShift to the version specified in the new tarball
5. **Upgrade operators** - Update operators to the versions specified in the new tarball

**Key Principle**: Each Enclave tarball is a versioned release with a pre-defined set of component versions. Upgrades move from one tarball release to another.

**Important**: Components must be upgraded in order—tarball first, then sync content, then landing zone, then cluster, then operators.

---

## Upgrading to a New Enclave Release

Each Enclave tarball release includes:
- Automation code (playbooks, scripts)
- Tested version matrix (OpenShift, operators, control binaries) in `defaults/*.yaml`
- Bug fixes and new features
- Updated documentation

### Upgrade Steps

1. **Obtain new tarball** - Download the new Enclave release tarball
2. **Backup configurations** - Save your `config/*.yaml` files (these contain your site-specific settings)
3. **Extract tarball** - Deploy to Landing Zone, replacing automation and defaults
4. **Restore configurations** - Copy your `config/*.yaml` files back (or merge if needed)
5. **Validate configuration** - Ensure your configs are compatible with new release
6. **Sync content** - Run sync process to mirror new versions (disconnected mode)
    ```sh
    ./sync.sh
    ```
7. **Execute upgrade script** - Run the upgrade automation to apply configuration migrations and updates. Currently executes the `playbooks/upgrade.yaml` playbook. Future versions will also automate steps 8 and 9 (cluster and operator upgrades):
    ```sh
    ./upgrade.sh
    ```
8. **Upgrade management cluster** - Update OpenShift to the version in the tarball:
    ```sh
    enclave reconcile mgmt-cluster-version --use-defaults
    ```
9. **Upgrade operators** - Update operators to the versions in the tarball:
    ```sh
    enclave reconcile operator-versions --use-defaults
    ```

---

## Resources

- [Enclave Deployment Guide](DEPLOYMENT_GUIDE.md)
- [Configuration Reference](CONFIGURATION_REFERENCE.md)
- [OpenShift Upgrade Documentation](https://docs.openshift.com/container-platform/latest/updating/index.html)
- [Red Hat Life Cycle Policy](https://access.redhat.com/support/policy/updates/openshift)

---

**Note**: This is a high-level guide. Consult OpenShift and operator-specific documentation for detailed upgrade procedures and troubleshooting.

---

## Version-Specific Upgrade Notes

### Upgrading from 0.1.0 to 0.1.1

This section documents changes, migrations, and operator version updates when upgrading from Enclave 0.1.0 to 0.1.1.

#### What's Changed

**Management Cluster Version Update:**
- **OpenShift**: 4.20.8 → 4.20.21

**Operator Version Updates:**
- **Quay**: 3.15.3 → 3.15.5
- **Multicluster Engine (MCE)**: 2.10.1 → 2.10.3
- **Advanced Cluster Management (ACM)**: 2.15.1 → 2.15.3

**Architecture Changes:**
- **Plugin Catalog Source Migration**: Foundation plugins (ODF, LVMS) now use dedicated catalog sources instead of the shared core catalog source
  - Before: All operators used `cs-redhat-operator-index-v4-20` (or equivalent mirrored catalog)
  - After: Foundation plugins have plugin-specific catalog sources (e.g., `cs-mirror-redhat-operators-odf-v4-20`)
  - This change improves plugin isolation and allows independent catalog management per plugin
- **Per-Plugin Configuration System**: plugins global configuration migrated to per-plugin config files under `config/plugins/`
- **Operator Source Definitions Removed**: The explicit `source:` field has been removed from most operators in `defaults/operators.yaml` as catalog sources are now derived automatically
- **MCE Subscription Management**: ACM is prevented from changing the MCE subscription installPlanApproval to Automatic (keeps it as Manual for upgrade control)
- **clair-import**: Standalone addon plugin for Clair security scanning (migrated from inline integration)

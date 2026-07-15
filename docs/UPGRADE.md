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
7. **Execute upgrade script** - Run the upgrade automation to apply configuration migrations, then upgrade the management cluster and operators to the versions pinned in the tarball. This executes the `playbooks/upgrade.yaml` playbook, which runs migrations followed by `enclave reconcile mgmt-cluster-version --use-defaults` and `enclave reconcile operator-versions --use-defaults`:
    ```sh
    ./upgrade.sh
    ```
8. **(Optional) Run cluster/operator upgrades separately** - By default `upgrade.yaml` performs both the management cluster and operator upgrades. To skip either step (e.g. to control upgrade timing independently) pass `upgrade_mgmt_cluster=false` and/or `upgrade_operators=false`, then run the corresponding command manually when ready:
    ```sh
    ansible-playbook playbooks/upgrade.yaml -e workingDir=<your global.yaml workingDir variable> -e upgrade_mgmt_cluster=false -e upgrade_operators=false

    WORKING_DIR=<your global.yaml workingDir variable>
    export KUBECONFIG=$WORKING_DIR/ocp-cluster/auth/kubeconfig
    enclave reconcile mgmt-cluster-version --use-defaults
    enclave reconcile operator-versions --use-defaults
    ```

---

## Migration System

Enclave uses a versioned migration system to apply one-time operational changes to an existing
cluster as part of an upgrade. Migrations are Ansible task files named with a UTC
timestamp prefix (`YYYYMMDDHHMMSS_description.yaml`) and live under `playbooks/tasks/migrations/`.

### How it works

A control file on the Landing Zone host records the filename of every migration that has already
been applied (one per line, append-only). The default path is
`<workingDir>/migrations/tasks.txt`; override it by setting `migrationTasksFilePath` in
`config/global.yaml`. When `upgrade.sh` runs, the migration runner computes the set of pending
migrations (all files minus those in the control file) and applies them in lexicographic
(timestamp) order.

Each migration is recorded in the control file only after it succeeds. If a migration fails,
Ansible stops and the migration is not recorded, so the next upgrade attempt will retry from that
migration.

### Fresh installs

`bootstrap.sh` seeds the control file with all migration filenames present at install time. This
ensures that migrations already baked into the release are not re-applied on the first upgrade.

If the control file is absent (a pre-migration-system installation), all migrations are run on
the next `upgrade.sh` execution. They are designed to be safe to run on an up-to-date enclave
installation.

### Adding a migration

Create a new file under `playbooks/tasks/migrations/` with a UTC timestamp prefix:

```sh
# Use the current UTC time as the filename prefix
date -u +%Y%m%d%H%M%S
# e.g. 20261231235959_my_change.yaml
```

The file should be a plain Ansible task list. If the migration only applies under certain
conditions (e.g., only in disconnected mode), add a `when:` condition inside the file using
an Ansible `block:`. Migrations are immutable once merged — modify behavior with a new migration,
not by editing an existing one.

CI validates that all migration timestamps are monotonically increasing, not in the future,
unique, and that existing merged files have not been modified:

```sh
make -f Makefile.ci validate-migrations
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
  - After: Foundation plugins have plugin-specific catalog sources (e.g., `cs-redhat-operator-index-odf-v4-20`)
  - This change improves plugin isolation and allows independent catalog management per plugin
- **Per-Plugin Configuration System**: plugins global configuration migrated to per-plugin config files under `config/plugins/`
- **Operator Source Definitions Removed**: The explicit `source:` field has been removed from most operators in `defaults/operators.yaml` as catalog sources are now derived automatically
- **MCE Subscription Management**: ACM is prevented from changing the MCE subscription installPlanApproval to Automatic (keeps it as Manual for upgrade control)
- **clair-import**: Standalone addon plugin for Clair security scanning (migrated from inline integration)

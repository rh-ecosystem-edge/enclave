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
1. **Sync/Mirror new content** - Run sync process to download and mirror new OpenShift and operator images to local registry (disconnected mode)
1. **Upgrade Landing Zone components** - Update components to the versions specified in the new tarball (includes Metal3 stack)
1. **Upgrade the management cluster** - Update OpenShift to the version specified in the new tarball
1. **Upgrade foundation plugin operators** - Update the operators of enabled foundation plugins (e.g. LVMS, ODF) to the versions specified in the new tarball. Their versions follow the OpenShift minor version, so they are upgraded after the cluster
1. **Upgrade operators** - Update operators to the versions specified in the new tarball

**Key Principle**: Each Enclave tarball is a versioned release with a pre-defined set of component versions. Upgrades move from one tarball release to another.

**Important**: Follow the order above. The management cluster upgrade precedes foundation plugin operators and then core operators.

### Planning the move to OpenShift 4.22

On the public amd64 `stable-4.22` graph, the direct update from 4.21.32 to 4.22.22 is conditional because of `VSphereConfigCloudControllerCrash`. The graph contains ordinary edges to 4.22.22 from 4.21.39 through 4.21.42. The `stable-4.21` graph has no path from 4.21.32 to those intermediates that uses only ordinary edges, so verify available updates on the actual cluster before choosing a route. [4.21 stable graph](https://api.openshift.com/api/upgrades_info/v1/graph?channel=stable-4.21&arch=amd64) · [4.22 stable graph](https://api.openshift.com/api/upgrades_info/v1/graph?channel=stable-4.22&arch=amd64)

Keep the normal upgrade checks enabled. Use the cluster's update recommendations to determine whether the direct conditional update is recommended for its configuration. If the cluster recommends an intermediate patch on its current y-stream, add that patch to the effective `openshift_versions` list and select it with `enclave reconcile mgmt-cluster-version --version X.Y.Z`. On a disconnected deployment, sync after adding an intermediate so the local mirror and OSUS can offer it. Wait for that update to complete, then check the target channel and the cluster's upgrade eligibility again. For clusters still on 4.20, plan the move through 4.21 first.

During a staged update, keep 4.22.22 as the default in the platform list used by sync so the mirrored catalog aliases follow the bundled catalogs. Select an intermediate explicitly with the management-cluster command above, or set `upgrade_operators=false` if you use the upgrade playbook with an intermediate as its target. Reconcile foundation and core operators after the cluster has completed its 4.22 update.

On Azure or vSphere, check the `managedBootImages` configuration before updating from 4.21 to 4.22. If it is unset, explicitly disable managed boot images for compute nodes or provide the documented `ack-4.21-boot-image-opt-out-in-4.22` acknowledgment to accept the new default. The signature acknowledgment that sync handles for 4.20 to 4.21 covers a separate gate. [Red Hat's 4.22 update preparation guide](https://github.com/openshift/openshift-docs/blob/enterprise-4.22/modules/update-preparing-azure-vsphere-ack.adoc)

If the cluster runs the CNV plugin, plan its OpenShift Virtualization updates through each minor release. Update OpenShift to the next minor before updating OpenShift Virtualization to that minor. [Red Hat's OpenShift Virtualization upgrade guidance](https://github.com/openshift/openshift-docs/blob/enterprise-4.22/modules/conditional-virt-about-upgrading-virt.adoc)

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

    Before running `./sync.sh`, ensure `defaultDNS` in `config/global.yaml` matches
    one of the detected Landing Zone DNS addresses (`/etc/resolv.conf` nameserver
    entries, or `resolvectl status` **Current DNS Server** entries when using the
    active systemd-resolved stub). Align the configuration with the intended
    deployment DNS before proceeding.

    ```sh
    ./sync.sh
    ```

7. **Execute upgrade script** - Run the upgrade automation to apply configuration migrations, upgrade Landing Zone components (Metal3 stack), then upgrade operators and the management cluster to the versions pinned in the tarball.

    ```sh
    ./upgrade.sh
    ```

    **Metal3 Upgrade Phase**: Upgrades Ironic and Baremetal Operator in persistent mode (`metal3_persistent: true`) to match the management cluster OpenShift version. For intermediate version requirements, use the migration system.

    **Management Cluster Upgrade Phase**: For a minor version upgrade (for example 4.20 → 4.21), `enclave reconcile mgmt-cluster-version` first checks that ClusterVersion doesn't report `Upgradeable=False`. Administrator acknowledgments in the `admin-acks` ConfigMap must be given beforehand. It then switches the update channel to the target minor, keeping the current prefix (`stable-4.20` → `stable-4.21`); the channel must be offered for the current release. With `--dry-run`, the channel switch is only logged, and whether the target version is offered isn't checked until the channel is actually switched. If the target version isn't offered, the error shows the cluster's update upstream, channel and `RetrievedUpdates` status.


8. **(Optional) Run cluster/operator upgrades separately** - By default `upgrade.yaml` performs both the management cluster and operator upgrades (including plugin operators). To skip either step (e.g. to control upgrade timing independently) pass `upgrade_mgmt_cluster=false` and/or `upgrade_operators=false`, then run the corresponding command manually when ready:

    ```sh
    ansible-playbook playbooks/upgrade.yaml -e workingDir=<your global.yaml workingDir variable> -e upgrade_mgmt_cluster=false -e upgrade_operators=false

    WORKING_DIR=<your global.yaml workingDir variable>
    export KUBECONFIG=$WORKING_DIR/ocp-cluster/auth/kubeconfig
    enclave reconcile mgmt-cluster-version --use-defaults
    enclave reconcile plugin-operator-versions --plugin=<storage plugin, e.g. lvms>
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

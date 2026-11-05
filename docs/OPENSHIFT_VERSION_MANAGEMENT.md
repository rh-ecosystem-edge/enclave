# OpenShift Version Management Contract

This document defines the contract between Red Hat Sovereign Enclave (RHSE) and enclave consumers for managing OpenShift versions.

## Executive Summary

- **Enclave provides**: A tested y-stream release range via `defaults/platforms.yaml`
- **Consumers manage**: Version lifecycle via optional `config/platforms.yaml` override
- **Why**: Reduce dependency on enclave releases for urgent version updates, CVE patches, and fleet management

## Motivation

Previously, OpenShift version management was tightly coupled to enclave releases. Any version change—whether adding a z-stream CVE fix, removing a deprecated version, or adjusting available versions for fleet management—required an enclave release.

This created operational friction:
- **CVE response delays**: Security patches required waiting for the next enclave release
- **Version deprecation lag**: Versions removed from the OpenShift graph lingered in enclave
- **Fleet management inflexibility**: Consumers couldn't control version availability per their policies
- **Release coupling**: Version updates became enclave release dependencies

The override mechanism shifts version management responsibility to consumers while maintaining enclave's tested baseline.

## Version Management Roles

### Enclave Team Responsibility

- **Maintains**: `defaults/platforms.yaml`
- **Defines**: Tested y-stream release range
- **Updates**: With each Enclave release based on validation and testing

The enclave team validates OpenShift versions against the Enclave stack (operators, plugins, and experiences) and publishes tested versions in `defaults/platforms.yaml`. Every version in that list is part of the Enclave-tested baseline. The current defaults list 4.20.21 and 4.20.32, with 4.20.32 marked as the default; the examples below assume that baseline unless stated otherwise. The `default: true` marker selects the default for a fresh management cluster installation and for `--use-defaults`; other listed versions remain available as explicit choices.

**Example** `defaults/platforms.yaml`:
```yaml
---
openshift_versions:
  - version: 4.20.21
  - version: 4.20.32
    default: true
```

This signals that the listed versions have been tested with this Enclave release, with 4.20.32 selected as the default.

### Consumer Responsibility

- **Manages**: `config/platforms.yaml` (optional override)
- **Controls**: Version lifecycle for their deployment
- **Decides**: When to add z-streams, remove versions, or adjust fleet policy

Consumers can set `openshift_versions` in `config/platforms.yaml` to replace the tested list. If the field is omitted, enclave keeps using the defaults. Ansible upgrade playbooks can also read options such as `allow_not_recommended_upgrades` from this file.

**Example** `config/platforms.yaml`:
```yaml
---
openshift_versions:
  - version: 4.20.21
  - version: 4.20.32
    default: true
  - version: 4.20.35  # CVE z-stream added urgently
```

This allows the consumer to add version 4.20.35 for a critical CVE fix without waiting for an enclave release.

## Override Mechanism

### How It Works

1. **Enclave loads defaults**: `defaults/platforms.yaml` defines `openshift_versions`
2. **Config loads override** (if present): `config/platforms.yaml` may define `openshift_versions`
3. **Override takes precedence**: If the config defines `openshift_versions`, it replaces the default list
4. **Both systems use result**: Ansible playbooks and Python CLI use the final merged value

Both files use the same `openshift_versions` field. The optional config file is loaded after defaults, so its value takes precedence when provided.

### Validation and Scope

The same `openshift_versions` list is used by management-cluster version selection and ACM managed-cluster image-set configuration. Its `default: true` entry selects the management-cluster default; every listed version is an allowed explicit choice. The list does not configure versions for downstream products such as OpenShift AI or OpenShift Virtualization.

Ansible validates the config structure against `schemas/platforms.yaml` and requires exactly one `default: true` entry. The Python CLI checks the version-list structure and the single default before selecting a version. Neither path checks whether a version is present in the OpenShift update graph, whether an upgrade path exists from a particular cluster, or whether an overridden version is compatible with the Enclave stack or downstream products. The override only makes a version selectable in Enclave; the management-cluster upgrade command still checks OpenShift's available updates. `allow_not_recommended_upgrades` separately permits versions reported as conditional updates. Enclave does not monitor the update graph or automate these consumer checks.

## Use Cases

### 1. Urgent CVE Z-Stream Fix

**Scenario**: OpenShift publishes 4.20.35 with a critical CVE fix. The consumer needs it deployed immediately, but the next enclave release is weeks away.

**Action**:
```bash
cp config/platforms.example.yaml config/platforms.yaml
# Edit config/platforms.yaml to add version 4.20.35
```

```yaml
---
openshift_versions:
  - version: 4.20.21
  - version: 4.20.32
    default: true
  - version: 4.20.35  # CVE fix
```

**Result**: Version 4.20.35 is immediately available for cluster upgrades via `enclave reconcile mgmt-cluster-version --version 4.20.35`.

### 2. Remove Deprecated Version

**Scenario**: Red Hat removes 4.20.21 from the upgrade graph. Enclave's `defaults/platforms.yaml` still lists it because the next release hasn't happened yet.

**Action**: Create `config/platforms.yaml` with only the valid versions:

```yaml
---
openshift_versions:
  - version: 4.20.32
    default: true
  - version: 4.20.35
```

**Result**: Version 4.20.21 is no longer offered, preventing invalid upgrade attempts.

### 3. Multi-Cluster Version Rollout

**Scenario**: A consumer manages 50 clusters via ACM. They want to stage version rollouts: dev clusters first, then staging, then production.

**Action**: Keep the same version list in each environment. The list controls which versions ACM can offer; ACM controls which clusters receive an upgrade and when. Upgrade development clusters first, then promote the same configuration and release package to staging and production after validation.

**Shared `config/platforms.yaml`**:

```yaml
openshift_versions:
  - version: 4.20.32
    default: true
  - version: 4.20.35
```

After the development rollout succeeds, select 4.20.35 for production clusters through ACM. The shared config does not need to change.

## Constraints and Responsibilities

### Consumer Constraints

When using `config/platforms.yaml`, consumers accept these responsibilities:

1. **Version availability**: Confirm each version is published and available from the configured source
2. **Compatibility**: Verify versions work with the current enclave release (enclave only tests what's in `defaults/platforms.yaml`)
3. **Schema compliance**: Maintain exactly one `default: true` version
4. **Upgrade paths**: Confirm the OpenShift update graph offers a supported path from each cluster's current version to the requested target. A version being listed does not imply that every listed version can upgrade to every other listed version.
5. **Testing**: Validate new versions in non-production environments before wider rollout

### Enclave Constraints

Enclave makes no compatibility or support guarantees about versions not listed in `defaults/platforms.yaml`:

- **No validation**: Versions in consumer overrides are not tested by the enclave team
- **No support obligation**: Issues with non-default versions may require consumer-led troubleshooting
- **No upgrade path guarantee**: Enclave does not validate upgrade paths for overridden versions

**This is intentional**. The override mechanism trades enclave's validation for consumer autonomy. Consumers gain flexibility; enclave maintains a tested baseline.

## Version Selection Behavior

### Management Cluster Installation

During a fresh disconnected deployment:
- **Fresh install** (`fresh: true`): Only the **default** version's release images are mirrored
- **Default version** is used for the management cluster
- Non-default versions are **not mirrored** during fresh install (to reduce mirror time and storage)

### Managed Cluster Deployments (ACM)

The ACM `ClusterImageSet` resources and mirrored release images are based on the effective `openshift_versions` list. On a fresh disconnected install, Enclave narrows that list to the default version, so only that version is mirrored and configured for ACM.

To mirror the full configured list after installation, run `bash sync.sh` from the Enclave checkout. The script runs the relevant phases with `fresh=false`, mirrors the configured releases, and reconciles ACM `ClusterImageSet` resources. Do not add `fresh` to `config/global.yaml`; that file's schema does not allow it. Removing or changing versions in the config takes effect in ACM only when the sync runs again; it does not change versions already running on managed clusters. A version in a `ClusterImageSet` is available as an install choice; it does not guarantee an upgrade path from an existing cluster version.

### Cluster Upgrades

The `enclave reconcile mgmt-cluster-version` command:
- Validates requested versions against the `openshift_versions` list
- Rejects versions not in the list
- Supports `--use-defaults` (use the default version), `--latest` (highest semver), or `--version X.Y.Z`

`--latest` can select a different version after the configured list changes. Use `--version` in repeatable automation when the target must stay pinned.

## Migration Path

### Existing Deployments

No action required. Existing deployments continue using `defaults/platforms.yaml` as before.

### Adopting the Override

1. **Review** current `defaults/platforms.yaml`
2. **Copy** `config/platforms.example.yaml` to `config/platforms.yaml`
3. **Edit** `openshift_versions` with your desired versions
4. **Validate** the local configuration from the Enclave checkout with `make validate-schema`
5. The override is read on the next Enclave run. In a disconnected deployment, if the management-cluster target release is not already mirrored, run `bash sync.sh` first to mirror it. Skip this step when the release is already mirrored. Then upgrade an existing management cluster with `enclave reconcile mgmt-cluster-version --version X.Y.Z` (or `--use-defaults`). For non-default managed-cluster choices in disconnected environments, run `bash sync.sh` to mirror the configured releases and update ACM `ClusterImageSet` resources.

### Reverting the Override

Remove `openshift_versions` from `config/platforms.yaml` or remove the file. Later Enclave runs then use `defaults/platforms.yaml`. This does not change a management or managed cluster that is already running; rerun the relevant deployment or ACM configuration playbooks to update available choices.

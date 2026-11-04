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

The enclave team validates OpenShift versions against the Enclave stack (operators, plugins, and experiences) and publishes tested versions in `defaults/platforms.yaml`. Every version in that list is part of the Enclave-tested baseline. The `default: true` marker selects the default for a fresh management cluster installation and for `--use-defaults`; other listed versions remain available as explicit choices.

**Example** `defaults/platforms.yaml`:
```yaml
---
openshift_versions:
  - version: 4.20.21
  - version: 4.20.32
    default: true
```

This signals: "Enclave v2.5.0 has been tested with the listed OpenShift versions, with 4.20.32 selected as the default."

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

### 3. Fleet Management Policy

**Scenario**: An organization's change control policy requires a 30-day validation window before allowing new OpenShift versions in production.

**Action**: Use `config/platforms.yaml` to control when versions become available, independent of when enclave or Red Hat publishes them.

```yaml
---
openshift_versions:
  - version: 4.20.32  # Validated, approved for production
    default: true
  # Version 4.20.35 published but not yet validated—intentionally omitted
```

**Result**: New versions enter the fleet on the organization's schedule, not Red Hat's or enclave's.

### 4. Multi-Cluster Version Control

**Scenario**: A consumer manages 50 clusters via ACM. They want to stage version rollouts: dev clusters first, then staging, then production.

**Action**: Promote the same Enclave release and version configuration through environments at different times when consistent configuration is desired. Use different `config/platforms.yaml` files only when environments intentionally have different version policies.

**Dev** `config/platforms.yaml`:
```yaml
openshift_versions:
  - version: 4.20.32
    default: true
  - version: 4.20.35  # Latest available
```

**Production** `config/platforms.yaml` (weeks later):
```yaml
openshift_versions:
  - version: 4.20.32
    default: true
  # Version 4.20.35 added only after successful dev/staging validation
```

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

During initial deployment:
- **Fresh install** (`fresh: true`): Only the **default** version's release images are mirrored
- **Default version** is used for the management cluster
- Non-default versions are **not mirrored** during fresh install (to reduce mirror time and storage)

### Managed Cluster Deployments (ACM)

The ACM `ClusterImageSet` resources and mirrored release images are based on the effective `openshift_versions` list. On a fresh disconnected install, Enclave narrows that list to the default version, so only that version is mirrored and configured for ACM.

To mirror the full configured list after installation, set `fresh: false` in `config/global.yaml` and run Phase 2 (`playbooks/02-mirror.yaml`). Then run Phase 6 (`playbooks/06-day2.yaml`) to reconcile ACM `ClusterImageSet` resources. Removing or changing versions in the config takes effect in those resources only when the relevant playbooks run again; it does not change versions already running on managed clusters. A version in a `ClusterImageSet` is available as an install choice; it does not guarantee an upgrade path from an existing cluster version.

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
4. **Validate** schema: `make -f Makefile.ci validate-json-schema`
5. **Deploy** as usual—override is automatically detected

### Reverting the Override

Remove `openshift_versions` from `config/platforms.yaml` or remove the file. Later Enclave runs then use `defaults/platforms.yaml`. This does not change a management or managed cluster that is already running; rerun the relevant deployment or ACM configuration playbooks to update available choices.

## Technical Implementation

### File Locations

- `defaults/platforms.yaml` — Enclave's tested versions (field: `openshift_versions`)
- `config/platforms.yaml` — Consumer override (field: `openshift_versions`)
- `config/platforms.example.yaml` — Example template for consumer override
- `schemas/platforms.yaml` — JSON schema validator for both files

### Loading Order

**Ansible** (`playbooks/common/load-vars.yaml`):
1. Load `defaults/platforms.yaml` → sets `openshift_versions`
2. Load `config/platforms.yaml` if it exists
3. If it defines `openshift_versions`, that list replaces the defaults

**Python CLI** (`src/enclave/reconcile/cli.py`):
1. Load `defaults/platforms.yaml` → extract `openshift_versions`
2. Check for `config/platforms.yaml`
3. If it defines `openshift_versions`, use that list instead

### Validation

- **Schema validation**: `make -f Makefile.ci validate-json-schema`
- **Defaults validation**: `playbooks/validation/tasks/defaults_schema_validation.yaml` enforces exactly one default
- **Config validation**: `playbooks/validation/tasks/config_schema_validation.yaml` validates the optional config and its default selection

## Best Practices

### For Consumers

1. **Start conservatively**: Begin with versions from `defaults/platforms.yaml`
2. **Test thoroughly**: Validate new versions in dev/staging before production
3. **Document changes**: Track why each version was added or removed
4. **Monitor Red Hat advisories**: Subscribe to OpenShift security and EOL notifications
5. **Coordinate with enclave releases**: Review `defaults/platforms.yaml` changes with each enclave update

### For Enclave Team

1. **Communicate version changes**: Announce changes to `defaults/platforms.yaml` in release notes
2. **Test across y-streams**: Validate the full range of listed versions
3. **Document testing scope**: Clearly state which versions were validated
4. **Provide migration guidance**: Help consumers understand version compatibility

## Support and Troubleshooting

### Consumer Override Issues

If a version in `config/platforms.yaml` fails:
1. **Check version availability**: Verify the release is available from the configured source with `oc adm release info`
2. **Review enclave compatibility**: Compare against `defaults/platforms.yaml` for the enclave version
3. **Check upgrade paths**: Ensure valid upgrade edges exist (`oc adm upgrade`)
4. **Test in isolation**: Deploy a test cluster with the problematic version

### Schema Validation Failures

```
TASK [Validate OpenShift version override default (if provided)] ***
fatal: [localhost]: FAILED! => {"msg": "config/platforms.yaml must have exactly one version with 'default: true'"}
```

**Solution**: Ensure exactly one version has `default: true`.

### Version Not Available

```
ClickException: Version '4.20.99' is not in defaults/platforms.yaml's effective version list. Allowed: 4.20.21, 4.20.32
```

**Solution**: Add the version to `config/platforms.yaml` or use a version from the configured list.

## Summary

The OpenShift version management override mechanism shifts version lifecycle control to consumers while preserving enclave's tested baseline. Enclave defines a supported y-stream range; consumers manage version additions, removals, and fleet policies independently of enclave releases.

**Key takeaways**:
- `defaults/platforms.yaml` = enclave's tested range
- `config/platforms.yaml` = optional consumer override for the `openshift_versions` list
- Override enables: CVE response, version deprecation, fleet management
- Consumer accepts: validation responsibility for non-default versions
- Enclave provides: tested baseline, no guarantees beyond it

This contract balances stability (enclave's tested versions) with agility (consumer-controlled updates).

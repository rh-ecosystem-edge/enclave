# OpenShift Version Management Contract

This document defines the contract between Red Hat Sovereign Enclave (RHSE) and enclave consumers for managing OpenShift versions.

## Executive Summary

**Enclave provides**: A tested y-stream release range via `defaults/platforms.yaml`  
**Consumers manage**: Version lifecycle via optional `config/platforms.yaml` override  
**Why**: Reduce dependency on enclave releases for urgent version updates, CVE patches, and fleet management

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

**Maintains**: `defaults/platforms.yaml`  
**Defines**: Tested y-stream release range  
**Updates**: With each enclave release based on validation and testing

The enclave team validates OpenShift versions against the full enclave stack (operators, plugins, experiences) and publishes tested versions in `defaults/platforms.yaml`. This file represents the **supported baseline**—versions confirmed to work with the current enclave release.

**Example** `defaults/platforms.yaml`:
```yaml
---
openshift_versions:
  - version: 4.20.21
  - version: 4.20.32
    default: true
```

This signals: "Enclave v2.5.0 has been tested and validated with OpenShift 4.20.x, with 4.20.32 as the recommended default."

### Consumer Responsibility

**Manages**: `config/platforms.yaml` (recommended override for fleet management)  
**Controls**: Version lifecycle for their deployment  
**Decides**: When to add z-streams, remove versions, or adjust fleet policy

Consumers who need version control beyond enclave's tested range can create `config/platforms.yaml`. When present, this file **completely replaces** `defaults/platforms.yaml`.

**Example** `config/platforms.yaml`:
```yaml
---
overrideOpenshiftVersions:
  - version: 4.20.21
  - version: 4.20.32
    default: true
  - version: 4.20.35  # CVE z-stream added urgently
```

This allows the consumer to add version 4.20.35 for a critical CVE fix without waiting for an enclave release.

## Override Mechanism

### How It Works

1. **Enclave loads defaults**: `defaults/platforms.yaml` defines `openshift_versions`
2. **Config loads override** (if present): `config/platforms.yaml` defines `overrideOpenshiftVersions`
3. **Override takes precedence**: If `overrideOpenshiftVersions` exists, it replaces `openshift_versions`
4. **Both systems use result**: Ansible playbooks and Python CLI use the final merged value

The field name difference (`openshift_versions` vs `overrideOpenshiftVersions`) allows both files to be loaded without collision. The Ansible variable loading in `playbooks/common/load-vars.yaml` and the Python CLI in `src/enclave/reconcile/cli.py` apply the override if present.

### Schema Validation

Both files are validated against `schemas/platforms.yaml`, which enforces:
- At least one version must be defined
- Exactly one version must be marked `default: true`
- Version objects must include a `version` field

The schema uses a shared `overrideOpenshiftVersionsList` definition (in `schemas/definitions.yaml`) to ensure consistency.

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
overrideOpenshiftVersions:
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
overrideOpenshiftVersions:
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
overrideOpenshiftVersions:
  - version: 4.20.32  # Validated, approved for production
    default: true
  # Version 4.20.35 published but not yet validated—intentionally omitted
```

**Result**: New versions enter the fleet on the organization's schedule, not Red Hat's or enclave's.

### 4. Multi-Cluster Version Control

**Scenario**: A consumer manages 50 clusters via ACM. They want to stage version rollouts: dev clusters first, then staging, then production.

**Action**: Use different `config/platforms.yaml` per environment. Dev gets new versions immediately; staging and production lag by defined intervals.

**Dev** `config/platforms.yaml`:
```yaml
overrideOpenshiftVersions:
  - version: 4.20.32
    default: true
  - version: 4.20.35  # Latest available
```

**Production** `config/platforms.yaml` (weeks later):
```yaml
overrideOpenshiftVersions:
  - version: 4.20.32
    default: true
  # Version 4.20.35 added only after successful dev/staging validation
```

## Constraints and Responsibilities

### Consumer Constraints

When using `config/platforms.yaml`, consumers accept these responsibilities:

1. **Version validity**: Ensure versions exist in the OpenShift release graph
2. **Compatibility**: Verify versions work with the current enclave release (enclave only tests what's in `defaults/platforms.yaml`)
3. **Schema compliance**: Maintain exactly one `default: true` version
4. **Upgrade paths**: Ensure valid upgrade paths exist between listed versions
5. **Testing**: Validate new versions in non-production environments before wider rollout

### Enclave Constraints

Enclave makes no guarantees about versions not listed in `defaults/platforms.yaml`:

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

After the management cluster is deployed:
- All versions from `openshift_versions` (or `overrideOpenshiftVersions` override) are available for managed clusters
- Consumers can select any listed version when provisioning clusters via ACM
- Additional versions can be mirrored on-demand using phase playbooks

### Cluster Upgrades

The `enclave reconcile mgmt-cluster-version` command:
- Validates requested versions against the `openshift_versions` list
- Rejects versions not in the list
- Supports `--use-defaults` (use the default version), `--latest` (highest semver), or `--version X.Y.Z`

## Migration Path

### Existing Deployments

No action required. Existing deployments continue using `defaults/platforms.yaml` as before.

### Adopting the Override

1. **Review** current `defaults/platforms.yaml`
2. **Copy** `config/platforms.example.yaml` to `config/platforms.yaml`
3. **Edit** `overrideOpenshiftVersions` with your desired versions
4. **Validate** schema: `make -f Makefile.ci validate-json-schema`
5. **Deploy** as usual—override is automatically detected

### Reverting the Override

Remove `config/platforms.yaml`. Enclave reverts to `defaults/platforms.yaml`.

## Technical Implementation

### File Locations

- `defaults/platforms.yaml` — Enclave's tested versions (field: `openshift_versions`)
- `config/platforms.yaml` — Consumer override (field: `overrideOpenshiftVersions`)
- `config/platforms.example.yaml` — Example template for consumer override
- `schemas/platforms.yaml` — JSON schema validator for both files
- `schemas/definitions.yaml` — Shared `overrideOpenshiftVersionsList` definition

### Loading Order

**Ansible** (`playbooks/common/load-vars.yaml`):
1. Load `defaults/platforms.yaml` → sets `openshift_versions`
2. Load `config/platforms.yaml` (if exists) → sets `overrideOpenshiftVersions`
3. If `overrideOpenshiftVersions` is defined, override: `openshift_versions = overrideOpenshiftVersions`

**Python CLI** (`src/enclave/reconcile/cli.py`):
1. Load `defaults/platforms.yaml` → extract `openshift_versions`
2. Check for `config/platforms.yaml`
3. If exists, extract `overrideOpenshiftVersions` and use it instead

### Validation

- **Schema validation**: `make -f Makefile.ci validate-json-schema`
- **Playbook validation**: `playbooks/validation/tasks/defaults_schema_validation.yaml` enforces exactly one default
- **Config validation**: `playbooks/validation/tasks/config_schema_validation.yaml` validates `config/platforms.yaml` if present

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
1. **Check version validity**: Verify the version exists in `oc adm release info`
2. **Review enclave compatibility**: Compare against `defaults/platforms.yaml` for the enclave version
3. **Check upgrade paths**: Ensure valid upgrade edges exist (`oc adm upgrade`)
4. **Test in isolation**: Deploy a test cluster with the problematic version

### Schema Validation Failures

```
TASK [Validate platforms.yaml against platforms schema (if present)] ***
fatal: [localhost]: FAILED! => {"msg": "a single default version is required in the openshift_versions list!"}
```

**Solution**: Ensure exactly one version has `default: true`.

### Version Not Available

```
ClickException: Version '4.20.99' is not in defaults/platforms.yaml. Allowed: 4.20.21, 4.20.32
```

**Solution**: Add the version to `config/platforms.yaml` or use a version from the allowed list.

## Summary

The OpenShift version management override mechanism shifts version lifecycle control to consumers while preserving enclave's tested baseline. Enclave defines a supported y-stream range; consumers manage version additions, removals, and fleet policies independently of enclave releases.

**Key takeaways**:
- `defaults/platforms.yaml` = enclave's tested range
- `config/platforms.yaml` = consumer's override (optional, complete replacement)
- Override enables: CVE response, version deprecation, fleet management
- Consumer accepts: validation responsibility for non-default versions
- Enclave provides: tested baseline, no guarantees beyond it

This contract balances stability (enclave's tested versions) with agility (consumer-controlled updates).

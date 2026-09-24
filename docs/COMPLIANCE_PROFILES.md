# Compliance Profiles Architecture

## Overview

The Compliance Profiles system allows regulatory frameworks (HIPAA, NIST 800-53, EU Digital Sovereignty) to be packaged as self-contained profiles under `compliance-profiles/`. Each profile declares security controls, plugin dependencies, and framework metadata. The Enclave pipeline discovers and activates profiles through a standard interface without framework-specific branching.

This architecture is analogous to the [Plugin Architecture](PLUGIN_ARCHITECTURE.md) and [Experiences](../experiences/README.md), providing declarative compliance control activation.

## Motivation

**Problem**: Not every OSAC deployment needs compliance controls. Enclave currently provides infrastructure hardening (ACM, software supply chain, vulnerability scanning) but does not have a mechanism to activate compliance-specific controls based on regulatory framework requirements.

**Solution**: Cloud Provider Admins select which compliance frameworks apply at deployment time, and Enclave activates the appropriate controls automatically—MFA enforcement, per-project encryption, session management, network isolation enforcement, hardening profiles, and compliance scanning/reporting.

**Benefits**:
- **Declarative**: Compliance requirements as code
- **Composable**: Multiple profiles can be layered (e.g., HIPAA + NIST 800-53)
- **Optional**: No overhead for non-regulated deployments
- **Post-deployment**: Add profiles without rebuilding infrastructure
- **Auditable**: Clear mapping from framework requirements to controls to plugins

## Directory Structure

A compliance profile is a directory under `compliance-profiles/` with a single descriptor:

```
compliance-profiles/
├── README.md
├── hipaa/
│   └── profile.yaml              <- the descriptor (required)
├── nist-800-53/
│   └── profile.yaml
└── eu-sovereignty/
    └── profile.yaml
```

### The Descriptor: `profile.yaml`

This is the required file. It contains framework metadata, control definitions, and plugin requirements.

```yaml
---
name: hipaa
description: >-
  HIPAA compliance profile for healthcare workloads requiring Protected Health
  Information (PHI) safeguards, access controls, audit logging, and encryption.

framework:
  fullName: Health Insurance Portability and Accountability Act (HIPAA)
  version: "2013 Omnibus Rule"
  authority: U.S. Department of Health and Human Services (HHS)
  applicability: Healthcare organizations handling PHI

controls:
  - name: mfa-enforcement
    required: true
    config:
      methods: [totp, webauthn]
      exemptions: []

  - name: session-timeout
    required: true
    config:
      timeout_minutes: 15
      idle_timeout_minutes: 5

  - name: encryption-at-rest
    required: true
    config:
      per_project_keys: true
      algorithm: AES-256

plugins:
  - name: acs
    required: true
  - name: compliance-operator
    required: true
  - name: rhbk
    required: true

compatibleWith:
  - nist-800-53
  - eu-sovereignty

conflicts: []
```

| Field | Description |
|-------|-------------|
| `name` | Profile identifier. Must match the directory name. |
| `description` | Human-readable description of the compliance framework. |
| `framework` | Metadata about the regulatory framework (full name, version, authority, applicability). |
| `framework.fullName` | Official name of the compliance framework. |
| `framework.version` | Framework version or publication date. |
| `framework.authority` | Governing body or regulatory authority. |
| `framework.applicability` | When this framework applies (e.g., "Healthcare with PHI"). |
| `controls` | List of security controls activated by this profile. Each control has a `name`, `required` flag, and optional `config` object. |
| `plugins` | Plugins required to implement the controls (e.g., `acs`, `compliance-operator`, `rhbk`). Each plugin can be marked as `required`. |
| `compatibleWith` | Other profiles that can be layered with this one without conflicts. |
| `conflicts` | Profiles that cannot be used simultaneously with this one. |

The profile descriptor is validated by JSON Schema (`schemas/compliance-profile.yaml`) during `make validate`.

## Security Controls

Controls are named, configured security mechanisms enforced by Enclave. Common controls include:

| Control Name | Description | Example Config |
|--------------|-------------|----------------|
| `mfa-enforcement` | Multi-factor authentication requirement | `methods: [totp, webauthn]` |
| `session-timeout` | Session and idle timeout policies | `timeout_minutes: 15` |
| `encryption-at-rest` | Data encryption requirements | `algorithm: AES-256, per_project_keys: true` |
| `encryption-in-transit` | TLS/mTLS enforcement | `min_tls_version: "1.2", enforce_mtls: true` |
| `network-isolation` | Network segmentation level | `level: strict, default_deny_ingress: true` |
| `audit-logging` | Logging and retention policies | `retention_days: 2555, log_data_access: true` |
| `hardening-profile` | OS/cluster hardening standard | `profile: STIG, apply_to: [control-plane, worker-nodes]` |
| `compliance-scanning` | Automated compliance checks | `frameworks: [hipaa], scan_interval: daily` |
| `access-control-rbac` | RBAC policies | `principle: least-privilege, role_review_interval_days: 90` |
| `vulnerability-scanning` | CVE scanning policies | `severity_threshold: medium, block_critical_vulnerabilities: true` |
| `data-residency` | Geographic data constraints | `allowed_regions: [eu-west], block_data_egress: true` |
| `gdpr-controls` | GDPR-specific requirements | `right_to_erasure: true, breach_notification_hours: 72` |

Controls are implemented through a combination of:
- **ACM Policies** applied to managed clusters
- **Operator configurations** (e.g., Compliance Operator scan settings)
- **Platform configurations** (e.g., OpenShift OAuth, session timeouts)
- **Network policies** and security contexts
- **Plugin-specific settings** (e.g., ACS policies, RHBK realm configuration)

## Control Precedence and Layering

When multiple profiles are active, the **strictest control configuration wins**:

- **Shortest timeout**: If HIPAA requires 15 minutes and NIST requires 30 minutes, 15 minutes is enforced
- **Strongest encryption**: If one profile requires AES-256 and another requires AES-128, AES-256 is used
- **Most restrictive access**: If one profile blocks egress and another allows it, egress is blocked
- **Longest retention**: If one profile requires 6 years and another requires 7 years, 7 years is used

This precedence rule ensures that adding a profile never weakens security posture.

## Lifecycle and Integration

### 1. Initial Deployment

Cloud Provider Admin configures compliance profiles before deployment:

```yaml
# config/compliance.yaml
compliance:
  profiles:
    - hipaa
    - nist-800-53
```

During deployment:
1. Enclave loads all selected profiles
2. Validates profile compatibility (checks `conflicts` fields)
3. Merges control configurations using precedence rules
4. Collects required plugins from all profiles
5. Deploys plugins via the standard plugin pipeline
6. Applies control configurations via ACM policies and operator settings
7. Initializes compliance scanning and reporting

### 2. Post-Deployment Addition

To add a compliance profile after initial deployment:

```bash
# 1. Edit config/compliance.yaml to add new profile
vim config/compliance.yaml

# 2. Run compliance reconfiguration playbook
ansible-playbook playbooks/configure-compliance.yaml

# 3. Verify control activation
enclave tools compliance status
enclave tools compliance audit --profile eu-sovereignty
```

Enclave will:
- Deploy any missing required plugins
- Activate new controls on existing clusters
- Update ACM policies
- Generate compliance reports showing the new posture

### 3. Non-Regulated Deployments

If `config/compliance.yaml` is absent or empty, Enclave operates without compliance-specific controls:

```yaml
# config/compliance.yaml (or omit the file entirely)
compliance:
  profiles: []
```

This allows development, testing, and non-regulated production environments to avoid compliance overhead.

## Relationship to Experiences

**Experiences** and **Compliance Profiles** are orthogonal and composable:

| Dimension | Experiences | Compliance Profiles |
|-----------|-------------|---------------------|
| **Purpose** | Define **what services** the platform provides | Define **how services** must be secured |
| **Examples** | VMaaS, CaaS, BMaaS | HIPAA, NIST 800-53, EU Sovereignty |
| **Config** | `config/experience.yaml` | `config/compliance.yaml` |
| **Directory** | `experiences/` | `compliance-profiles/` |
| **Schema** | `schemas/expeience.yaml` | `schemas/compliance-profile.yaml` |
| **Plugins** | Defines service capabilities | Defines security requirements |

Example combinations:

- **VMaaS + HIPAA**: VM provisioning with healthcare compliance
- **CaaS + NIST 800-53**: Container clusters with federal security controls
- **BMaaS + EU Sovereignty**: Bare metal with EU data residency requirements
- **VMaaS + HIPAA + NIST 800-53**: VM provisioning meeting both healthcare and federal requirements

## Compliance Administration

### Verifying Profile Status

```bash
# View active profiles
enclave tools compliance status

# Output:
# Active Profiles:
#   - hipaa (HIPAA 2013 Omnibus Rule)
#   - nist-800-53 (NIST SP 800-53 Rev. 5)
#
# Controls Enforced: 15 total, 15 passing, 0 failing
# Required Plugins: acs, compliance-operator, rhbk, file-integrity-operator
```

### Generating Compliance Reports

```bash
# Generate report for a specific profile
enclave tools compliance report --profile hipaa --format pdf --output hipaa-report.pdf

# Generate cross-profile report
enclave tools compliance report --all-profiles --format html --output compliance-dashboard.html
```

Reports include:
- Control implementation status
- Policy enforcement verification
- Scan results from Compliance Operator
- Audit log retention verification
- Plugin deployment status
- Non-compliance findings and remediation steps

### Auditing Control Implementation

```bash
# Audit a specific profile
enclave tools compliance audit --profile nist-800-53

# Output:
# Control: mfa-enforcement
#   Status: ENFORCED
#   RHBK Realm: enclave
#   Required Methods: totp, webauthn, piv-card
#   Exemptions: none
#
# Control: encryption-at-rest
#   Status: ENFORCED
#   Algorithm: AES-256
#   FIPS Mode: enabled
#   Per-Project Keys: enabled
#   Key Rotation: 365 days (last: 2026-07-01)
```

## Implementation Checklist

To implement compliance profile support in Enclave:

### Phase 1: Core Infrastructure
- [ ] Create `compliance-profiles/` directory structure
- [ ] Define `schemas/compliance-profile.yaml` schema
- [ ] Implement profile loader in `src/compliance/loader.py`
- [ ] Implement control precedence logic in `src/compliance/merger.py`
- [ ] Add validation to `make validate-compliance-profiles`

### Phase 2: Profile Definitions
- [ ] Define HIPAA profile with all required controls
- [ ] Define NIST 800-53 profile with all required controls
- [ ] Define EU Sovereignty profile with all required controls
- [ ] Document control-to-plugin mappings

### Phase 3: Control Implementation
- [ ] Implement MFA enforcement via RHBK configuration
- [ ] Implement session timeout via OpenShift OAuth configuration
- [ ] Implement encryption controls via cluster policies
- [ ] Implement network isolation via NetworkPolicy and ACM
- [ ] Implement audit logging configuration
- [ ] Implement hardening profile selection
- [ ] Implement compliance scanning via Compliance Operator
- [ ] Implement vulnerability scanning policies via ACS

### Phase 4: Integration
- [ ] Create `playbooks/configure-compliance.yaml` playbook
- [ ] Integrate with existing plugin deployment pipeline
- [ ] Create ACM policy templates for each control
- [ ] Add compliance status to ACM dashboard

### Phase 5: Tooling
- [ ] Implement `enclave tools compliance status` command
- [ ] Implement `enclave tools compliance report` command
- [ ] Implement `enclave tools compliance audit` command
- [ ] Create compliance dashboard UI component

### Phase 6: Testing and Documentation
- [ ] Add unit tests for profile loading and merging
- [ ] Add integration tests for control activation
- [ ] Add E2E tests for each profile
- [ ] Update deployment guide with compliance sections
- [ ] Create compliance configuration examples
- [ ] Document control remediation procedures

## References

- [HIPAA Security Rule](https://www.hhs.gov/hipaa/for-professionals/security/index.html)
- [NIST SP 800-53 Rev. 5](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)
- [EU Digital Sovereignty](https://digital-strategy.ec.europa.eu/en/policies/sovereignty)
- [OpenShift Compliance Operator](https://docs.openshift.com/container-platform/latest/security/compliance_operator/compliance-operator-understanding.html)
- [Red Hat Advanced Cluster Security](https://www.redhat.com/en/technologies/cloud-computing/openshift/advanced-cluster-security-kubernetes)
- [Red Hat build of Keycloak](https://access.redhat.com/products/red-hat-build-of-keycloak)

## Related Documentation

- [Plugin Architecture](PLUGIN_ARCHITECTURE.md) — similar declarative pattern for components
- [Experiences README](../experiences/README.md) — service capability bundles
- [Deployment Guide](DEPLOYMENT_GUIDE.md) — overall deployment workflow
- [Configuration Reference](CONFIGURATION_REFERENCE.md) — all configuration options

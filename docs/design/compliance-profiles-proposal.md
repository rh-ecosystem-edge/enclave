# Compliance Profiles: Design Proposal

**JIRA**: OSAC-3024 — Red Hat Sovereign Enclave (RHSE) Integration  
**Date**: 2026-08-13  
**Status**: Proposal

## Executive Summary

This proposal introduces **Compliance Profiles** to Enclave — a declarative system for activating compliance controls based on regulatory framework requirements. Compliance Profiles follow the same architectural pattern as Experiences and Plugins, enabling Cloud Provider Admins to select frameworks at deployment time (HIPAA, NIST 800-53, EU Sovereignty) and have appropriate controls activated automatically.

## Problem Statement

From OSAC-3024:

> Not every OSAC deployment needs compliance controls. Enclave currently provides infrastructure hardening (ACM, software supply chain, vulnerability scanning) but does not have a mechanism to activate compliance-specific controls based on regulatory framework requirements. Cloud Provider Admins need a single configuration point to say "this deployment must be HIPAA-compliant" and have the appropriate controls activated automatically, rather than manually configuring each control individually.

## Proposed Solution

### Concept: Compliance Profiles

Similar to how **Experiences** bundle plugins for customer capabilities (VMaaS, CaaS, BMaaS), **Compliance Profiles** bundle security controls for regulatory frameworks.

```
compliance-profiles/
├── README.md
├── hipaa/
│   └── profile.yaml
├── nist-800-53/
│   └── profile.yaml
└── eu-sovereignty/
    └── profile.yaml
```

Each profile declares:
- **Framework metadata** (name, version, authority, applicability)
- **Security controls** (MFA, encryption, session management, audit logging, etc.)
- **Required plugins** (ACS, Compliance Operator, RHBK, etc.)
- **Compatibility** with other profiles (can be layered)

### Example Profile Structure

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
      key_rotation_days: 90

  - name: audit-logging
    required: true
    config:
      retention_days: 2555  # 7 years per HIPAA
      log_data_access: true
      tamper_protection: true

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
```

### Cloud Provider Admin Experience

**At deployment time**:

```yaml
# config/compliance.yaml
compliance:
  profiles:
    - hipaa
    - nist-800-53  # Multiple profiles can be layered
```

**Post-deployment addition**:

```bash
# Add new profile to config
vim config/compliance.yaml

# Reconfigure compliance
ansible-playbook playbooks/configure-compliance.yaml

# Verify
enclave tools compliance status
```

**No compliance overhead for non-regulated deployments**:

```yaml
# config/compliance.yaml (or omit the file)
compliance:
  profiles: []  # No profiles = no compliance overhead
```

## Key Benefits

✅ **Declarative** — Compliance requirements as code, versioned and auditable  
✅ **Composable** — Multiple profiles can be layered (e.g., HIPAA + NIST 800-53)  
✅ **Optional** — No overhead for non-regulated deployments  
✅ **Post-deployment** — Add profiles without rebuilding infrastructure  
✅ **Consistent with existing architecture** — Follows the same pattern as Experiences and Plugins  
✅ **Auditable** — Clear mapping from framework requirements → controls → plugins  
✅ **Extensible** — New frameworks can be added without changing core code

## Mapping to OSAC-3024 User Stories

| User Story | How Compliance Profiles Address It |
|------------|-------------------------------------|
| "As a Cloud Provider Admin, I need to select which compliance frameworks apply to my Enclave deployment" | Select profiles in `config/compliance.yaml` |
| "As a Cloud Provider Admin, I need to run Enclave without any compliance framework for non-regulated deployments" | Omit `config/compliance.yaml` or set `profiles: []` |
| "As a Cloud Provider Admin, I need to add compliance frameworks post-deployment without disrupting existing tenants" | Add profile to config, run `playbooks/configure-compliance.yaml`, controls activate via ACM policies |
| "As a Compliance Admin, I need to verify which frameworks are active and that all required controls are enforced" | `enclave tools compliance status`, `enclave tools compliance audit`, `enclave tools compliance report` |

## Mapping to OSAC-3024 Definition of Done

| DoD Criterion | Implementation |
|---------------|----------------|
| Compliance framework profiles (HIPAA, NIST 800-53, EU sovereignty) configurable in Enclave | ✅ `compliance-profiles/hipaa/`, `nist-800-53/`, `eu-sovereignty/` with `profile.yaml` descriptors |
| Framework selection activates appropriate controls automatically | ✅ Profile loading → control merging → ACM policy application → plugin deployment |
| Framework addition post-deployment supported | ✅ `playbooks/configure-compliance.yaml` for reconfiguration |
| Non-regulated deployments operate without compliance overhead | ✅ `profiles: []` or omit `config/compliance.yaml` |
| Compliance profile status visible to Compliance Admin persona | ✅ `enclave tools compliance status/audit/report` commands |
| E2E test coverage | To be implemented in Phase 6 |

## Control Precedence and Layering

When multiple profiles are active, the **strictest control configuration wins**:

- **Shortest timeout**: HIPAA 15 min + NIST 30 min → 15 min enforced
- **Strongest encryption**: AES-128 + AES-256 → AES-256 used
- **Most restrictive access**: Allow egress + Block egress → Egress blocked
- **Longest retention**: 6 years + 7 years → 7 years enforced

This ensures that adding a profile never weakens security posture.

## Relationship to Experiences

**Experiences** and **Compliance Profiles** are orthogonal and composable:

| Dimension | Experiences | Compliance Profiles |
|-----------|-------------|---------------------|
| **Purpose** | Define **what services** the platform provides | Define **how services** must be secured |
| **Examples** | VMaaS, CaaS, BMaaS | HIPAA, NIST 800-53, EU Sovereignty |
| **Config** | `config/experience.yaml` | `config/compliance.yaml` |
| **Directory** | `experiences/` | `compliance-profiles/` |
| **Schema** | `schemas/expeience.yaml` | `schemas/compliance-profile.yaml` |

Example combinations:
- **VMaaS + HIPAA**: VM provisioning with healthcare compliance
- **CaaS + NIST 800-53**: Container clusters with federal security controls
- **BMaaS + EU Sovereignty**: Bare metal with EU data residency requirements

## Implementation Phases

### Phase 1: Core Infrastructure
- Create `compliance-profiles/` directory structure
- Define `schemas/compliance-profile.yaml` schema
- Implement profile loader and control merger
- Add validation to `make validate-compliance-profiles`

### Phase 2: Profile Definitions
- Define HIPAA profile with all required controls
- Define NIST 800-53 profile with all required controls
- Define EU Sovereignty profile with all required controls

### Phase 3: Control Implementation
- Implement each control type (MFA, session timeout, encryption, etc.)
- Map controls to plugin configurations and ACM policies
- Implement hardening profile selection
- Configure Compliance Operator scanning

### Phase 4: Integration
- Create `playbooks/configure-compliance.yaml` playbook
- Integrate with existing plugin deployment pipeline
- Create ACM policy templates for each control
- Add compliance status to ACM dashboard

### Phase 5: Tooling
- Implement `enclave tools compliance status` command
- Implement `enclave tools compliance report` command
- Implement `enclave tools compliance audit` command
- Create compliance dashboard UI component

### Phase 6: Testing and Documentation
- Add unit, integration, and E2E tests
- Update deployment guide with compliance sections
- Create compliance configuration examples
- Document control remediation procedures

## Initial Profiles Defined

Three profiles are proposed initially, mapping to OSAC-3024 requirements:

### 1. HIPAA (Health Insurance Portability and Accountability Act)
- **Authority**: U.S. Department of Health and Human Services (HHS)
- **Use Case**: Healthcare organizations handling PHI
- **Key Controls**: MFA (required), 15-min session timeout, AES-256 encryption with per-project keys, 7-year audit retention, STIG hardening, daily compliance scanning
- **Required Plugins**: ACS, Compliance Operator, RHBK, File Integrity Operator

### 2. NIST 800-53 (NIST Special Publication 800-53 Rev. 5)
- **Authority**: National Institute of Standards and Technology (NIST)
- **Use Case**: Federal information systems
- **Key Controls**: MFA with PIV card support, 30-min session timeout, FIPS-mode encryption, comprehensive audit logging with SIEM integration, STIG hardening, supply chain security, incident response automation
- **Required Plugins**: ACS, Compliance Operator, RHBK, File Integrity Operator

### 3. EU Sovereignty (EU Digital Sovereignty Framework)
- **Authority**: European Commission / Member State Regulations
- **Use Case**: EU public sector and critical infrastructure
- **Key Controls**: Data residency enforcement (EU-only), encryption key sovereignty, GDPR controls (right to erasure, breach notification), supply chain transparency, operational autonomy (no vendor backdoors), foreign access prohibition
- **Required Plugins**: ACS, Compliance Operator, RHBK

## Files Included in This Proposal

The following files have been created as part of this design proposal:

### Schemas
- `schemas/compliance-profile.yaml` — JSON Schema validating profile descriptors

### Compliance Profiles
- `compliance-profiles/README.md` — Overview and usage guide
- `compliance-profiles/hipaa/profile.yaml` — HIPAA compliance profile
- `compliance-profiles/nist-800-53/profile.yaml` — NIST 800-53 compliance profile
- `compliance-profiles/eu-sovereignty/profile.yaml` — EU Sovereignty compliance profile

### Documentation
- `docs/COMPLIANCE_PROFILES.md` — Comprehensive architecture documentation
- `docs/design/compliance-profiles-proposal.md` — This proposal document

## Next Steps

1. **Review and approval**: Get stakeholder feedback on this proposal
2. **JIRA ticket breakdown**: Create sub-tasks for each implementation phase (mapped to existing linked issues in OSAC-3024)
3. **Prototype**: Implement Phase 1 (core infrastructure) and Phase 2 (profile definitions)
4. **Pilot**: Implement one control end-to-end (e.g., MFA enforcement) to validate the approach
5. **Iterate**: Complete remaining phases based on pilot learnings

## Questions for Stakeholders

1. **Control Scope**: Are the proposed controls comprehensive enough for each framework, or are there additional controls needed?
2. **Plugin Dependencies**: Are there additional plugins beyond ACS, Compliance Operator, and RHBK that should be included?
3. **Compliance Admin Tooling**: What specific reports and dashboards are most valuable for Compliance Admin persona?
4. **Post-Deployment Migration**: What is the expected workflow when adding a compliance profile to an existing deployment with active tenant workloads?
5. **Multi-Cluster Scenarios**: How should compliance profiles apply across hub and spoke clusters in ACM topology?

## References

- **JIRA**: [OSAC-3024](https://redhat.atlassian.net/browse/OSAC-3024)
- **Dependencies**: OSAC-3027 (MFA), OSAC-3028 (Network Isolation), OSAC-3029 (ACM Policies), OSAC-3030 (ACS), OSAC-3031 (OpenSCAP), OSAC-3032 (Dashboard)
- **Framework Documentation**:
  - [HIPAA Security Rule](https://www.hhs.gov/hipaa/for-professionals/security/index.html)
  - [NIST SP 800-53 Rev. 5](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)
  - [EU Digital Sovereignty](https://digital-strategy.ec.europa.eu/en/policies/sovereignty)
- **Related Enclave Documentation**:
  - [Plugin Architecture](../PLUGIN_ARCHITECTURE.md)
  - [Experiences README](../../experiences/README.md)
  - [Compliance Profiles Architecture](../COMPLIANCE_PROFILES.md)

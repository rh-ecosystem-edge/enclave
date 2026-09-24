# Enclave Compliance Profiles

A Compliance Profile is a collection of security controls and plugin requirements that together enforce regulatory compliance requirements for specific frameworks.

Similar to [Experiences](../experiences/README.md) (which bundle plugins for customer capabilities), Compliance Profiles bundle controls for regulatory frameworks.

## Available Profiles

| Profile | Framework | Authority | Use Case |
|---------|-----------|-----------|----------|
| **HIPAA** | Health Insurance Portability and Accountability Act | U.S. Department of Health and Human Services (HHS) | Healthcare organizations handling Protected Health Information (PHI) |
| **NIST 800-53** | NIST Special Publication 800-53 Rev. 5 | National Institute of Standards and Technology (NIST) | Federal information systems and organizations |
| **EU Sovereignty** | EU Digital Sovereignty Framework | European Commission / Member State Regulations | EU public sector and critical infrastructure requiring data residency and operational autonomy |

## How It Works

1. **Cloud Provider Admin** selects which compliance profiles apply at deployment time in the Enclave configuration
2. **Enclave** activates the appropriate security controls automatically based on the selected profile(s)
3. **Controls** enforce framework-specific requirements:
   - MFA enforcement policies
   - Session timeout and management
   - Encryption requirements (at-rest and in-transit)
   - Network isolation levels
   - Hardening profiles (STIG, CIS, NIST)
   - Audit logging and retention
   - Compliance scanning and reporting
4. **Plugins** are automatically deployed to implement the controls (e.g., ACS, Compliance Operator, RHBK)

## Profile Structure

Each profile is defined in a `profile.yaml` file validated against `schemas/compliance-profile.yaml`:

```yaml
---
name: hipaa
description: HIPAA compliance profile for healthcare workloads...
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

plugins:
  - name: acs
    required: true
  - name: compliance-operator
    required: true
```

## Configuration

Profiles are referenced in the main Enclave configuration:

```yaml
# config/compliance.yaml
compliance:
  profiles:
    - hipaa
    - nist-800-53  # Multiple profiles can be layered
  
  # Profile-specific overrides (optional)
  overrides:
    hipaa:
      controls:
        session-timeout:
          config:
            timeout_minutes: 10  # Stricter than profile default
```

## Profile Compatibility

Profiles declare compatibility and conflicts:

- **Compatible**: HIPAA, NIST 800-53, and EU Sovereignty are mutually compatible and can be layered
- **Conflicts**: Defined in each profile's `conflicts` field (currently none)

When multiple profiles are active, the strictest control configuration wins (shortest timeout, strongest encryption, etc.).

## No Compliance Overhead for Non-Regulated Deployments

If no compliance profiles are selected, Enclave operates without compliance-specific controls. This allows:
- Development and testing environments to run without compliance overhead
- Non-regulated deployments to avoid unnecessary restrictions
- Gradual adoption of compliance controls as regulatory requirements evolve

## Post-Deployment Profile Activation

Compliance profiles can be added after initial deployment:

1. Update `config/compliance.yaml` to include the new profile
2. Re-run the compliance configuration playbook: `ansible-playbook playbooks/configure-compliance.yaml`
3. Enclave will:
   - Deploy any missing required plugins
   - Activate the new controls
   - Apply configuration changes to existing clusters via ACM policies
   - Generate compliance reports showing the new posture

This allows organizations to respond to changing regulatory requirements without rebuilding infrastructure.

## Relationship to Experiences

**Experiences** (VMaaS, CaaS, BMaaS) define **what services** the cloud platform provides.  
**Compliance Profiles** define **how those services** must be secured and governed.

They are orthogonal and composable:
- A **VMaaS + HIPAA** deployment provides VM provisioning with healthcare compliance
- A **CaaS + NIST 800-53** deployment provides container clusters with federal security controls
- A **BMaaS + EU Sovereignty** deployment provides bare metal with EU data residency requirements

## For Compliance Administrators

To verify compliance profile status:

```bash
# View active profiles
enclave tools compliance status

# Generate compliance report
enclave tools compliance report --profile hipaa --format pdf

# Audit control implementation
enclave tools compliance audit --profile nist-800-53
```

## Adding New Profiles

To add a new compliance profile:

1. Create a new directory under `compliance-profiles/<profile-name>/`
2. Define `profile.yaml` following the schema in `schemas/compliance-profile.yaml`
3. List required controls with their configurations
4. Specify required plugins
5. Document framework metadata (authority, version, applicability)
6. Add validation fixtures under `test-fixtures/` if needed
7. Update this README with the new profile in the table above

## Validation

Compliance profiles are validated during CI:

```bash
make -f Makefile.ci validate-compliance-profiles
```

This checks:
- Profile YAML structure matches the schema
- Required plugins exist in the `plugins/` directory
- Control names follow naming conventions
- Framework metadata is complete

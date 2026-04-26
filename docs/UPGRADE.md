# Enclave Upgrade Guide

## Overview

An Enclave upgrade is the process of updating the deployed infrastructure to a new Enclave release. Each Enclave tarball is distributed with a tested combination of:

1. **Enclave automation** (playbooks, scripts, configuration)
2. **Management Cluster OpenShift version** (e.g., 4.20.8)
3. **Operator versions** (ACM, Quay, etc.)

The tarball contains all version definitions in `defaults/*.yaml` files and is the artifact as-is—a complete, tested release.

### The Upgrade Process

Upgrading to a new Enclave release follows this sequence:

1. **Deploy new Enclave tarball** - Extract new release to Landing Zone, preserving your `config/*.yaml` customizations
2. **Sync/Mirror new content** - Run sync process to download and mirror new OpenShift and operator images to local registry (disconnected mode)
3. **Upgrade the management cluster** - Update OpenShift to the version specified in the new tarball
4. **Upgrade operators** - Update operators to the versions specified in the new tarball
5. **Validation** - Verify all components are healthy and at expected versions

**Key Principle**: Each Enclave tarball is a versioned release with a pre-defined set of component versions. Upgrades move from one tarball release to another.

**Important**: Components must be upgraded in order—tarball first, then sync content, then cluster, then operators.

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
7. **Upgrade management cluster** - Update OpenShift to version in tarball
8. **Upgrade operators** - Update operators to versions in tarball

---

## Resources

- [Enclave Deployment Guide](DEPLOYMENT_GUIDE.md)
- [Configuration Reference](CONFIGURATION_REFERENCE.md)
- [OpenShift Upgrade Documentation](https://docs.openshift.com/container-platform/latest/updating/index.html)
- [Red Hat Life Cycle Policy](https://access.redhat.com/support/policy/updates/openshift)

---

**Note**: This is a high-level guide. Consult OpenShift and operator-specific documentation for detailed upgrade procedures and troubleshooting.

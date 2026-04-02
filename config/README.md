# Configuration Variables

This directory contains the configuration files needed for your enclave deployment.

## Quick Start

1. **Copy the example files to create your configuration:**
   ```bash
   cd config/
   cp global.example.yaml global.yaml
   cp certificates.example.yaml certificates.yaml
   cp cloud_infra.example.yaml cloud_infra.yaml
   ```

2. **Fill in `global.yaml` with your environment details**
3. **Fill in `certificates.yaml` with your SSL certificates**
4. **Fill in `cloud_infra.yaml` with your discovery hosts (or leave `discovery_hosts: []` if not needed)**

## Configuration Files

### `global.yaml` (required)
Contains all cluster and infrastructure configuration.

### `certificates.yaml` (required)
Contains SSL certificates for the cluster.

### `cloud_infra.yaml` (required)
Contains cloud infrastructure configuration, including the list of worker nodes to be discovered and added to the cluster. Set `discovery_hosts: []` if no discovery hosts are needed.

## Security
- **Never commit `global.yaml`, `certificates.yaml` or `cloud_infra.yaml` to version control**
- These files contain sensitive credentials and private keys
- The `.gitignore` is configured to exclude them

## Storage Operators

Storage operators (LVMS, ODF) are configured via the plugin system. See `docs/PLUGIN_ARCHITECTURE.md`.

## Getting Help
- See `docs/DEPLOYMENT_GUIDE.md` for step-by-step deployment instructions
- See `docs/CONFIGURATION_REFERENCE.md` for detailed parameter documentation

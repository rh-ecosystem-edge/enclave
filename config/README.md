# Configuration Variables

This directory contains the configuration files needed for your enclave deployment.

## Quick Start

1. **Copy the example files to create your configuration:**
   ```bash
   cd config/
   cp global.example.yaml global.yaml
   cp certificates.example.yaml certificates.yaml
   cp plugins/node-management.example.yaml plugins/node-management.yaml
   ```

2. **Fill in `global.yaml` with your environment details**
3. **Fill in `certificates.yaml` with your SSL certificates**
4. **Optionally fill in `plugins/node-management.yaml` with discovery hosts**
5. **Optionally configure other plugin-specific settings** (e.g. restrict which disks LVMS manages):
   ```bash
   cp config/plugins/lvms.example.yaml config/plugins/lvms.yaml
   # Edit config/plugins/lvms.yaml as needed
   ```

## Configuration Files

### `global.yaml` (required)
Contains all cluster and infrastructure configuration.

### `certificates.yaml` (required)
Contains SSL certificates for the cluster.

### `plugins/node-management.yaml` (optional)
Contains node discovery settings for the `node-management` addon plugin. Deploy it after cluster installation with `make deploy-plugin PLUGIN=node-management`. Do not list it in `enabled_plugins`; that runs addon plugins during Phase 5.

Existing `config/cloud_infra.yaml` files remain supported as a fallback when `nodeManagementConfig.discovery_hosts` is not set in the plugin config. New configurations should use the plugin file.

### `cloud_infra.yaml` (legacy)
Legacy cloud infrastructure configuration. The node-management plugin reads its `discovery_hosts` as a backward-compatible fallback.

### `plugins/` subdirectory (optional)
Contains per-plugin configuration files, named after the plugin (e.g. `config/plugins/lvms.yaml`).
Copy from `config/plugins/<name>.example.yaml` and fill in values.
Plugin configuration is auto-loaded at deployment time — only create the file if you need to provide plugin-specific settings.
See each plugin's `plugins/<name>/schemas/config.yaml` for the available settings.

## Security
- **Never commit `global.yaml`, `certificates.yaml`, or plugin config files to version control**
- These files contain sensitive credentials and private keys
- The `.gitignore` is configured to exclude them

## Storage Operators

Storage operators (LVMS, ODF) are configured via the plugin system. See `docs/PLUGIN_ARCHITECTURE.md`.

## Getting Help
- See `docs/DEPLOYMENT_GUIDE.md` for step-by-step deployment instructions
- See `docs/CONFIGURATION_REFERENCE.md` for detailed parameter documentation

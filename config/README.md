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
5. **Optionally configure plugin-specific settings** (e.g. restrict which disks LVMS manages):
   ```bash
   cp config/plugins/lvms.example.yaml config/plugins/lvms.yaml
   # Edit config/plugins/lvms.yaml as needed
   ```

## Configuration Files

### `global.yaml` (required)
Contains all cluster and infrastructure configuration.

### `certificates.yaml` (required)
Contains SSL certificates for the cluster.

### `cloud_infra.yaml` (required)
Contains cloud infrastructure configuration, including the list of worker nodes to be discovered and added to the cluster. Set `discovery_hosts: []` if no discovery hosts are needed.

### `plugins/` subdirectory (optional)
Contains per-plugin configuration files, named after the plugin (e.g. `config/plugins/lvms.yaml`).
Copy from `config/plugins/<name>.example.yaml` and fill in values.
Plugin configuration is auto-loaded at deployment time — only create the file if you need to provide plugin-specific settings.
See each plugin's `plugins/<name>/schemas/config.yaml` for the available settings.

### `operators.yaml` (optional)

Copy `config/operators.example.yaml` to `config/operators.yaml` to override core
operators from `defaults/operators.yaml`. Entries merge **by exact operator name**;
only supplied fields replace defaults. A version-only or channel-only override is
valid. Unspecified fields (including `init_version`, namespace, CSV names and seed
flags), other operators, and the default operator ordering are retained. List-valued
fields replace that field's list; the operator list is never replaced or removed.

The effective configuration is shared by installation, image mirroring and
`enclave reconcile operator-versions --use-defaults` (including `playbooks/upgrade.yaml`).
`--use-defaults` therefore includes this optional config, not just bundled values.
When using an installed CLI, run it from the active Enclave checkout so it finds
the same config as the playbooks, or select the override explicitly from any directory:

```sh
enclave reconcile operator-versions --use-defaults --operators-vars-file /path/to/enclave/config/operators.yaml
```

`--operators-vars-file` requires `--use-defaults` and cannot be combined with the
explicit operator options (`--name`, `--version`, `--namespace`, `--channel`,
`--csv-name`). The selected file replaces automatic override discovery, not the
defaults; a missing or invalid explicit file fails rather than falling back.
Without this option, checkout discovery is unchanged; outside a checkout an
installed CLI uses bundled defaults when no config is discovered. Defaults and
the schema are bundled with the installed CLI. `playbooks/upgrade.yaml` passes
its checkout's override explicitly when present and runs from that checkout,
keeping an absent optional file a no-op.
Plugins continue to read their operators from `plugins/<name>/plugin.yaml`, through
their separate installation, mirroring and `plugin-operator-versions` paths.

Validation fails before consuming any operator if an override has unknown operator
names (including plugin-only names), duplicate names or YAML keys, unknown fields,
wrong types, nulls, or blank string values. A missing file or `operators: []` means
no overrides. An unreadable file or dangling symlink is an error, not a no-op.
An empty/null document, `{}`, missing `operators`, or a non-list
`operators` value is an error. An entry containing only `name` is a valid no-op.
`csvNames` must be a non-empty list of non-blank strings; `extraMirrorPackages: []`
is allowed to clear that field. Null never means delete or inherit. YAML aliases
are accepted, but duplicate keys introduced by YAML merges are rejected too.

Raw partial entries are validated against `definitions.overrides` in
`schemas/operators.yaml`, then the merged result is validated against the full
schema. Dependencies such as `csvMirror` requiring `csvNames` are checked on the
merged entry, so they may be inherited from defaults. Versions and channels remain
non-blank strings; availability in a catalog is not checked by this validation.
Changing `version` does not reset `init_version`: override both when you need to
change the mirror range's lower bound. Choose compatible version/channel/range
values for your catalog. No defaults or plugin descriptors need editing.

## Security
- **Never commit `global.yaml`, `certificates.yaml` or `cloud_infra.yaml` to version control**
- These files contain sensitive credentials and private keys
- The `.gitignore` is configured to exclude them

## Storage Operators

Storage operators (LVMS, ODF) are configured via the plugin system. See `docs/PLUGIN_ARCHITECTURE.md`.

## Getting Help
- See `docs/DEPLOYMENT_GUIDE.md` for step-by-step deployment instructions
- See `docs/CONFIGURATION_REFERENCE.md` for detailed parameter documentation

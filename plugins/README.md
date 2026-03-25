# Enclave Plugin System

Plugins extend the Enclave deployment with additional operators, storage backends, and custom logic. Each plugin is a self-contained directory under `plugins/` that the framework auto-discovers and deploys.

## Directory Structure

```
plugins/{plugin-name}/
├── plugin.yaml                    # Required - plugin descriptor (data + config)
└── tasks/                         # Optional - Ansible task files
    ├── pre-install-validate.yaml  # Optional - runs before cluster install
    ├── pre-validate.yaml          # Optional - runs before plugin deploy
    ├── deploy.yaml                # Optional - main deployment tasks
    ├── quay.yaml                  # Optional - Quay storage integration tasks
    └── post-validate.yaml         # Optional - runs after plugin deploy
```

## plugin.yaml

The plugin descriptor. Contains all plugin data: metadata, operator definitions, defaults, and registry entries.

### Required fields

| Field | Values | Description |
|-------|--------|-------------|
| `name` | string | Unique identifier, must match the directory name |
| `type` | `foundation`, `addon` | `foundation` plugins deploy in Phase 5 before core operators. `addon` plugins are deployed separately |

### Optional fields

| Field | Values | Description |
|-------|--------|-------------|
| `order` | integer | Controls deployment order among plugins of the same type. Lower values deploy first |
| `mirror` | `core`, `none` | `core` = operators included in the main Phase 2 oc-mirror run. `none` = no mirroring |
| `operators` | list | OLM operators to install (see Operator fields below) |
| `defaults` | object | Default variables loaded into Ansible scope before plugin tasks run |
| `registries` | list | Registry mirror entries for MCE custom-registries patching |

### Example: foundation plugin with operators

```yaml
---
name: lvms
type: foundation
order: 10
mirror: core

operators:
  - name: lvms-operator
    version: 4.20.0
    channel: stable-4.20
    init_version: 4.20.0
    namespace: openshift-storage
    source: cs-redhat-operator-index-v4-20

defaults:
  lvmsDefaults:
    deviceClassName: vg1
    defaultStorageClass: true
    thinPoolConfig:
      name: vg1-pool-1
      sizePercent: 90
      overprovisionRatio: 10

registries:
  - location: "registry.redhat.io/lvms4"
    mirror: "lvms4"
```

### Example: minimal addon plugin

```yaml
---
name: example
type: addon
order: 999

defaults:
  example_message: "Hello from example plugin"
```

### Operator fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Operator package name |
| `version` | Yes | Target version |
| `channel` | Yes | OLM subscription channel |
| `init_version` | Yes | Minimum version (for oc-mirror range) |
| `namespace` | No | Namespace for Subscription and OperatorGroup |
| `source` | No | CatalogSource name |
| `csvNames` | No | List of CSV names to approve and wait for (for operators with sub-operators) |
| `csvMirror` | No | When `true`, csvNames entries are mirrored as separate packages in the imageset |
| `global` | No | When `true`, creates a cluster-wide OperatorGroup (no target namespace) |

### Defaults

Variables defined under `defaults` are loaded into the Ansible scope before any plugin tasks run. Use a `{pluginName}Defaults` naming convention for plugin-specific defaults that can be overridden by users.

### Registry entries

Each entry maps a source registry path to a path in the internal Quay mirror (`<quayHostname>:8443/<mirror>`). Used for:
1. `registries.conf` so oc-mirror redirects image pulls to the internal Quay
2. MCE `custom-registries` ConfigMap so spoke clusters know where to pull images from

## Lifecycle Tasks

All lifecycle files live under `tasks/` and are Ansible task lists (not playbooks). They run with `KUBECONFIG` set to the cluster kubeconfig, except `pre-install-validate.yaml` which runs before the cluster exists.

### tasks/pre-install-validate.yaml

Runs during Phase 3 (cluster preparation), before installation begins. Use this to validate hardware requirements or host configuration. The `discovered_hosts` variable contains the list of hosts found by the Assisted Service.

### tasks/pre-validate.yaml

Runs after the cluster is deployed but before operators are installed. Use this to verify cluster prerequisites.

### tasks/deploy.yaml

Main deployment logic. Runs after operators are installed and ready. Use this to create Custom Resources, configure the operator, or run any post-install setup.

### tasks/post-validate.yaml

Runs after deploy.yaml completes. Use this to verify the plugin deployed correctly.

### tasks/quay.yaml

Provides Quay storage integration tasks for this plugin. When the plugin is selected as `storage_plugin`, the Quay operator dynamically includes `plugins/{name}/tasks/quay.yaml` to create the QuayRegistry CR with the appropriate storage configuration.

## Configuration

### Global settings (config/global.yaml)

```yaml
storage_plugin: lvms           # Which storage plugin to deploy (lvms or odf)
enabled_plugins:               # Plugins to deploy (defaults to just storage_plugin)
  - lvms
  - example
```

## Deployment Flow

### Phase 2 - Mirror (disconnected only)

1. `collect_core_plugin_operators` reads `operators` from `plugin.yaml` of all enabled plugins
2. Plugin operators are merged into the main imageset for a single oc-mirror invocation
3. Operators with `csvMirror: true` have their `csvNames` entries added as separate packages for OLM dependency resolution
4. `collect_plugin_registries` reads `registries` from `plugin.yaml` and adds entries to `registries.conf`

### Phase 3 - Cluster Deploy

1. `pre_install_validate_plugins` runs `tasks/pre-install-validate.yaml` from each enabled plugin

### Phase 5 - Operators

1. Default CatalogSources are disabled (disconnected mode)
2. Foundation plugins are deployed in `order` sequence:
   - Load defaults from `plugin.yaml`
   - Run tasks/pre-validate
   - Install operators (create Namespace, CatalogSource, OperatorGroup, Subscription)
   - Approve InstallPlans, wait for CSVs
   - Patch MCE registries (disconnected)
   - Run tasks/deploy
   - Run tasks/post-validate
3. Core operators are installed after all foundation plugins
4. Quay operator includes `plugins/{storage_plugin}/tasks/quay.yaml` for storage-specific QuayRegistry setup

## Schema Validation

Plugin descriptors are validated by JSON Schema (`schemas/plugin.yaml`) during `make validate` using `ansible.utils.validate`. This validates field types, required fields, enum values, operator structure, and registry entries.

Additionally, `scripts/verification/validate_plugins.sh` checks:
- Each plugin directory has a `plugin.yaml`
- Task files (if present) are valid YAML task lists
- No unexpected files outside `plugin.yaml` and `tasks/`

## Creating a New Plugin

### Minimal plugin (no operators, connected only)

```
plugins/my-plugin/
├── plugin.yaml
└── tasks/
    └── deploy.yaml
```

```yaml
# plugin.yaml
name: my-plugin
type: addon
order: 100
```

### Operator plugin (disconnected)

```
plugins/my-operator/
├── plugin.yaml
└── tasks/
    └── deploy.yaml
```

```yaml
# plugin.yaml
name: my-operator
type: foundation
order: 20
mirror: core

operators:
  - name: my-operator
    version: 1.0.0
    channel: stable
    init_version: 1.0.0
    namespace: my-namespace
    source: cs-redhat-operator-index-v4-20

registries:
  - location: "registry.redhat.io/my-operator"
    mirror: "my-operator"
```

### Enabling your plugin

Add it to `enabled_plugins` in `config/global.yaml`:

```yaml
enabled_plugins:
  - lvms
  - my-plugin
```

## Existing Plugins

| Plugin | Type | Description |
|--------|------|-------------|
| `lvms` | foundation | LVM Storage - lightweight local storage using LVM |
| `odf` | foundation | OpenShift Data Foundation - distributed storage (Ceph) |
| `example` | addon | Reference plugin demonstrating the lifecycle hooks |

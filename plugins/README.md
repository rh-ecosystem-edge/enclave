# Enclave Plugin System

Plugins extend the Enclave deployment with additional operators, storage backends, and custom logic. Each plugin is a self-contained directory under `plugins/` that the framework auto-discovers and deploys.

## Directory Structure

```
plugins/{plugin-name}/
├── plugin.yaml                    # Required - plugin descriptor
├── config/
│   └── defaults.yaml              # Optional - default variable values
├── operators/
│   └── operators.yaml             # Required if operators: true
├── mirror/
│   ├── imageset.yaml.j2           # Optional - oc-mirror imageset template
│   └── registries.yaml            # Optional - registry mirror entries
├── pre-install-validate.yaml      # Optional - runs before cluster install
├── pre-validate.yaml              # Optional - runs before plugin deploy
├── deploy.yaml                    # Optional - main deployment tasks
├── quay.yaml                      # Optional - Quay storage integration tasks
└── post-validate.yaml             # Optional - runs after plugin deploy
```

## plugin.yaml

The plugin descriptor. All fields are required.

```yaml
name: my-plugin          # Must match directory name
type: foundation         # foundation | addon
order: 10                # Deploy order (lower = first)
mirror: core             # core | plugin | none
operators: true          # Whether plugin installs operators
```

### Fields

| Field | Values | Description |
|-------|--------|-------------|
| `name` | string | Unique identifier, must match the directory name |
| `type` | `foundation`, `addon` | `foundation` plugins deploy in Phase 5 before core operators. `addon` plugins are deployed separately |
| `order` | integer | Controls deployment order among plugins of the same type. Lower values deploy first |
| `mirror` | `core`, `plugin`, `none` | `core` = operators included in the main Phase 2 oc-mirror run. `plugin` = plugin mirrors images during its own deploy. `none` = no mirroring (connected-mode only) |
| `operators` | boolean | When `true`, the framework installs operators from `operators/operators.yaml` |

## Configuration

### Global settings (config/global.yaml)

```yaml
storage_plugin: lvms           # Which storage plugin to deploy (lvms or odf)
enabled_plugins:               # Plugins to deploy (defaults to just storage_plugin)
  - lvms
  - example
```

### Plugin defaults (config/defaults.yaml)

Optional. Variables defined here are loaded into the Ansible scope before any plugin tasks run. Use a `{pluginName}Defaults` naming convention for plugin-specific defaults that can be overridden by users.

```yaml
lvmsConfigDefaults:
  deviceSelector:
    forceWipeDevicesAndDestroyAllData: true

lvmsDefaults:
  deviceClassName: vg1
  defaultStorageClass: true
  thinPoolConfig:
    name: vg1-pool-1
    sizePercent: 90
    overprovisionRatio: 10
```

## Operator Definitions (operators/operators.yaml)

Required when `operators: true` in plugin.yaml.

```yaml
plugin_operators:
  - name: lvms-operator
    version: 4.20.0
    channel: stable-4.20
    init_version: 4.20.0
    namespace: openshift-storage
    source: cs-redhat-operator-index-v4-20
```

### Operator fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Operator package name |
| `version` | Yes | Target version |
| `channel` | Yes | OLM subscription channel |
| `init_version` | Yes | Minimum version (for oc-mirror range) |
| `namespace` | Yes | Namespace for Subscription and OperatorGroup |
| `source` | Yes | CatalogSource name |
| `csvNames` | No | List of CSV names to approve and wait for (for operators with sub-operators) |
| `global` | No | When `true`, creates a cluster-wide OperatorGroup (no target namespace) |

### Extra packages (plugin_extra_packages)

Optional. For operators that depend on additional OLM packages (like ODF), list them here. These are added to the main imageset as bare entries so OLM can resolve all dependencies.

```yaml
plugin_extra_packages:
  - mcg-operator
  - rook-ceph-operator
  - ocs-operator
```

## Mirror Configuration

### mirror/imageset.yaml.j2

Jinja2 template for `ImageSetConfiguration`. Used by oc-mirror to determine which operator images to mirror. The variable `plugin_operators_file` contains the parsed content of `operators/operators.yaml`.

```yaml
---
kind: ImageSetConfiguration
apiVersion: mirror.openshift.io/v2alpha1
mirror:
  operators:
    - catalog: registry.redhat.io/redhat/redhat-operator-index:v4.20
      packages:
{% for operator in plugin_operators_file.plugin_operators %}
        - name: {{ operator.name }}
          defaultChannel: {{ operator.channel }}
          channels:
            - name: {{ operator.channel }}
              minVersion: {{ operator.init_version }}
              maxVersion: {{ operator.version }}
{% endfor %}
```

### mirror/registries.yaml

Defines registry mirror mappings. These are used in two ways:
1. Added to `registries.conf` so oc-mirror redirects image pulls to the internal Quay
2. Patched into the MCE `custom-registries` ConfigMap for spoke cluster awareness

```yaml
plugin_registries:
  - location: "registry.redhat.io/odf4"
    mirror: "odf4"
  - location: "registry.redhat.io/rhceph"
    mirror: "rhceph"
```

Each entry maps a source registry path to a path in the internal Quay mirror (`<quayHostname>:8443/<mirror>`).

## Lifecycle Tasks

All lifecycle files are Ansible task lists (not playbooks). They run with `KUBECONFIG` set to the cluster kubeconfig, except `pre-install-validate.yaml` which runs before the cluster exists.

### pre-install-validate.yaml

Runs during Phase 3 (cluster preparation), before installation begins. Use this to validate hardware requirements or host configuration. The `discovered_hosts` variable contains the list of hosts found by the Assisted Service.

```yaml
---
- name: Validate disk count
  ansible.builtin.assert:
    that: discovered_hosts | length >= 3
    fail_msg: "Need at least 3 hosts for storage"
```

### pre-validate.yaml

Runs after the cluster is deployed but before operators are installed. Use this to verify cluster prerequisites.

### deploy.yaml

Main deployment logic. Runs after operators are installed and ready. Use this to create Custom Resources, configure the operator, or run any post-install setup.

```yaml
---
- name: Create LVMCluster
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: lvm.topolvm.io/v1alpha1
      kind: LVMCluster
      metadata:
        name: lvm-storage
        namespace: openshift-storage
      spec:
        storage:
          deviceClasses:
            - name: vg1
              default: true
```

### post-validate.yaml

Runs after deploy.yaml completes. Use this to verify the plugin deployed correctly.

### quay.yaml

Optional. Provides Quay storage integration tasks for this plugin. When the plugin is selected as `storage_plugin`, the Quay operator dynamically includes `plugins/{name}/quay.yaml` to create the QuayRegistry CR with the appropriate storage configuration.

```yaml
---
- name: Ensure QuayRegistry is present
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: quay.redhat.com/v1
      kind: QuayRegistry
      metadata:
        name: registry
        namespace: quay-enterprise
      spec:
        configBundleSecret: quay-config
        components:
        - kind: objectstorage
          managed: false
        # ... storage-specific components
```

## Deployment Flow

### Phase 2 - Mirror (disconnected only)

1. `collect_core_plugin_operators` reads `operators/operators.yaml` from all enabled plugins with `mirror: core`
2. Plugin operators are merged into the main imageset for a single oc-mirror invocation
3. `plugin_extra_packages` are added as bare entries for OLM dependency resolution
4. `collect_plugin_registries` reads `mirror/registries.yaml` from all enabled plugins and adds entries to `registries.conf`

### Phase 3 - Cluster Deploy

1. `pre_install_validate_plugins` runs `pre-install-validate.yaml` from each enabled plugin

### Phase 5 - Operators

1. Default CatalogSources are disabled (disconnected mode)
2. Foundation plugins are deployed in `order` sequence:
   - Load defaults
   - Run pre-validate
   - Install operators (create Namespace, CatalogSource, OperatorGroup, Subscription)
   - Approve InstallPlans, wait for CSVs
   - Patch MCE registries (disconnected)
   - Run deploy
   - Run post-validate
3. Core operators are installed after all foundation plugins
4. Quay operator includes `plugins/{storage_plugin}/quay.yaml` for storage-specific QuayRegistry setup

## Creating a New Plugin

### Minimal plugin (no operators, connected only)

```
plugins/my-plugin/
├── plugin.yaml
└── deploy.yaml
```

```yaml
# plugin.yaml
name: my-plugin
type: addon
order: 100
mirror: none
operators: false
```

### Operator plugin (disconnected)

```
plugins/my-operator/
├── plugin.yaml
├── operators/
│   └── operators.yaml
└── mirror/
    ├── imageset.yaml.j2
    └── registries.yaml
```

```yaml
# plugin.yaml
name: my-operator
type: foundation
order: 20
mirror: core
operators: true
```

### Enabling your plugin

Add it to `enabled_plugins` in `config/global.yaml`:

```yaml
enabled_plugins:
  - lvms
  - my-plugin
```

## Validation

Plugins are validated by CI via `scripts/verification/validate_plugins.sh`. This checks:

- `plugin.yaml` has all required fields and no unknown fields
- `operators/operators.yaml` exists when `operators: true` and has valid schema
- `mirror/` directory exists when `mirror` is `core` or `plugin`
- All YAML files parse correctly
- Lifecycle task files are valid Ansible task lists

Run locally with `make validate-plugins`.

## Existing Plugins

| Plugin | Type | Description |
|--------|------|-------------|
| `lvms` | foundation | LVM Storage - lightweight local storage using LVM |
| `odf` | foundation | OpenShift Data Foundation - distributed storage (Ceph) |
| `example` | addon | Reference plugin demonstrating the lifecycle hooks |

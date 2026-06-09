# Enclave Plugin Architecture

## Overview

The plugin system allows components (storage backends, AI/ML stacks, GPU operators) to be packaged as self-contained units under `plugins/`. Each plugin carries its own operator definitions, mirroring configuration, deployment logic, and defaults. The core pipeline discovers and runs plugins through a standard interface without component-specific branching.

## Directory Structure

A plugin is a directory under `plugins/` with a single descriptor and optional task files:

```
plugins/lvms/
  plugin.yaml              <- the descriptor (required)
  defaults.yaml            <- plugin defaults (optional)
  schemas/
    defaults.yaml          <- JSON Schema validating defaults.yaml (optional)
    config.yaml            <- JSON Schema validating config/plugins/lvms.yaml (optional)
  test-fixtures/
    schemas/
      defaults/
        valid/             <- fixtures that must pass defaults schema validation
        invalid/           <- fixtures that must fail defaults schema validation
      config/
        valid/             <- fixtures that must pass config schema validation
        invalid/           <- fixtures that must fail config schema validation
  tasks/
    early-validate.yaml    <- custom validation before downloads, no cluster access (optional)
    deploy.yaml            <- post-operator deployment logic
    quay.yaml              <- Quay storage integration
    pre-validate.yaml      <- pre-deployment checks (optional)
    post-operators.yaml    <- runs after operators, before deploy (optional)
    post-validate.yaml     <- post-deployment checks (optional)
    pre-install-validate.yaml <- hardware checks before cluster install (optional)
  charts/                    <- Helm chart directories (optional)
  templates/                 <- Jinja2 templates for Helm values or other rendering (optional)
  files/                     <- Jinja2 templates for K8s manifests (optional)
```

### The Descriptor: `plugin.yaml`

This is the only required file. It contains plugin metadata, operator definitions, and registry entries.

```yaml
name: lvms
type: foundation
order: 10

operators:
  - name: lvms-operator
    version: 4.20.0
    channel: stable-4.20
    init_version: 4.20.0
    namespace: openshift-storage

requires:
  files:
    - path: "tasks/deploy.yaml"
      description: "LVMS deployment tasks"
    - path: "tasks/quay.yaml"
      description: "LVMS Quay storage configuration"

registries:
  - location: "registry.redhat.io/lvms4"
    mirror: "lvms4"
```

| Field | What it does |
|-------|-------------|
| `name` | Plugin identifier. Must match the directory name. |
| `type` | `foundation` -- deploys before core operators (storage, networking). `addon` -- deployed separately. |
| `order` | Deploy order among same-type plugins. Lower = first. LVMS is 10, ODF is 10. |
| `catalog` | Operator catalog name (`redhat` or `certified`). Defaults to `redhat`. |
| `operators` | List of OLM operators to install. Each entry is passed to `configure_operator.yaml`. |
| `installOperators` | Set to `false` to skip operator installation (mirror-only plugins). Defaults to `true`. |
| `clusterSelector` | Label-matching expressions to identify and select specific managed clusters for deploying the plugin (using ACM policies). |
| `defaults` | Variables loaded into Ansible scope before tasks run. Alternative to `defaults.yaml` — cannot use both. All top-level property names must be prefixed with the plugin name (e.g. `lvmsDefaults`, not just `Defaults`). DO NOT USE, this property has been deprecated.|
| `registries` | Registry mirror entries for MCE patching and `registries.conf`. |
| `additionalImages` | Extra images to include in the plugin's oc-mirror image set. |
| `blockedImages` | Images to exclude from mirroring (by tag, digest, or pattern). |
| `requires` | Declarative requirements validated at load time, before any deployment work begins. See [Load-Time Validation](#load-time-validation). |
| `helm` | List of Helm charts to install after operators, before `tasks/deploy.yaml`. Supports local charts and remote repos. Each entry specifies `release`, `namespace`, and optional `repo`, `version`, values template, and `extractImages`. When `extractImages: true` is set on a local chart, `helm template` is run before mirroring to discover container image references and merge them into `additionalImages` automatically. |

The plugin descriptor is validated by JSON Schema (`schemas/plugin.yaml`) during `make validate`. The validator (`make validate-plugins`) also checks directory structure.

### Lifecycle Files

Each file is optional and lives under `tasks/`. The plugin runner (`deploy_plugin.yaml`) checks if it exists before including it. If it's missing, that step is skipped.

Here's what runs and in what order:

```
 1. Load plugin.yaml              <- always
 2. Load defaults                  <- from `defaults.yaml` (if present) or `plugin.yaml` defaults field (cannot have both)
 3. Validate requirements          <- assert requires.vars and requires.files
 4. tasks/early-validate.yaml     <- custom validation (no cluster access)
 5. tasks/pre-validate.yaml       <- "is the cluster ready for me?"
 6. Extract Helm images            <- helm template on charts with extractImages: true, merge into additionalImages
 7. Mirror (plugin-mode only)      <- template imageset, run oc-mirror, apply manifests
 8. Install operators              <- from plugin.yaml operators list (if installOperators != false)
 9. tasks/post-operators.yaml     <- "set up infrastructure that Helm or deploy needs"
10. Deploy Helm charts             <- from plugin.yaml helm list (values templates rendered here)
11. tasks/deploy.yaml             <- "create my CRs"
12. tasks/post-validate.yaml      <- "did it work?"
```

Steps 3-4 are the load-time validation gate. If any declared requirement is missing or the early-validate script fails, the plugin fails immediately -- before mirroring, operator installation, or deployment. Note that `early-validate.yaml` runs before the cluster exists, so it cannot use KUBECONFIG. Use it for config format checks, external connectivity validation, or other pre-flight logic.

Step 6 runs `helm template` (with chart defaults, no custom values) on local charts that have `extractImages: true`. The discovered image references are merged into `additionalImages` before mirroring. This is opt-in because `helm template` can fail if sub-chart dependencies aren't populated. Remote charts (with `repo` set) are skipped.

Step 9 (`post-operators.yaml`) is useful for plugins that need to set up infrastructure after operators are installed but before Helm charts or deploy tasks run. For example, a plugin might use this hook to create a CR instance and extract credentials that the Helm values template depends on. Facts set in `post-operators.yaml` are available to later steps because all hooks run in the same Ansible play via `include_tasks`.

Separately, during Quay operator setup:

```
tasks/quay.yaml                  <- "here's how Quay should use my storage"
```

## Example: LVMS Plugin

LVMS is a foundation plugin that provides LVM-based block storage on local disks.

### `plugin.yaml`

```yaml
name: lvms
type: foundation
order: 10

operators:
  - name: lvms-operator
    version: 4.20.0
    channel: stable-4.20
    init_version: 4.20.0
    namespace: openshift-storage

requires:
  files:
    - path: "tasks/deploy.yaml"
      description: "LVMS deployment tasks"
    - path: "tasks/quay.yaml"
      description: "LVMS Quay storage configuration"

registries:
  - location: "registry.redhat.io/lvms4"
    mirror: "lvms4"
```

### `defaults.yaml`

Plugin defaults live in `defaults.yaml`:

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

These variables get loaded into Ansible scope before tasks run. The naming convention `lvmsDefaults.*` keeps things namespaced so plugins don't step on each other's variables.

### `tasks/deploy.yaml`

```yaml
- name: "Ensure LVMCluster exists"
  vars:
    _spec: |
      storage:
        deviceClasses:
          - name: {{ lvmsDefaults.deviceClassName }}
            default: {{ lvmsDefaults.defaultStorageClass }}
            thinPoolConfig:
              name: {{ lvmsDefaults.thinPoolConfig.name }}
              sizePercent: {{ lvmsDefaults.thinPoolConfig.sizePercent }}
              overprovisionRatio: {{ lvmsDefaults.thinPoolConfig.overprovisionRatio }}
            ...
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: lvm.topolvm.io/v1alpha1
      kind: LVMCluster
      metadata:
        name: lvm-storage
        namespace: openshift-storage
      spec: "{{ _spec | from_yaml }}"
```

After the operator is installed, this creates the LVMCluster CR using variables from `defaults.yaml`.

### `tasks/quay.yaml`

When `storage_plugin: lvms` is set, the Quay operator includes `plugins/lvms/tasks/quay.yaml` to configure Quay's storage backend. The Quay operator uses dynamic inclusion:

```yaml
- name: Run storage plugin Quay tasks
  ansible.builtin.include_tasks:
    file: "{{ playbook_dir }}/../plugins/{{ storage_plugin }}/tasks/quay.yaml"
```

Adding a new storage option requires creating `plugins/<name>/tasks/quay.yaml` with no changes to the Quay operator code.

## Pipeline Integration

### Early Validation

Before downloads begin, `validate_enabled_plugins.yaml` runs. It discovers all enabled plugins that declare a `requires` block, loads their defaults, and asserts that required variables and files are present. This happens at the start of Phase 1 (Prepare), so a missing requirement fails the run immediately -- before any binary downloads or mirroring.

The per-plugin validation in `deploy_plugin.yaml` remains as defense-in-depth during individual plugin deployment.

### Mirroring (disconnected)

Foundation Plugins have their operators collected by `collect_core_plugin_operators.yaml` and included in the core oc-mirror run. Addon plugins run their own oc-mirror invocation during step 6 of their lifecycle.

Operators with `csvMirror: true` have their `csvNames` entries added as separate packages in the image set.

Each Plugin is mirrored to a custom catalog to support operations on day 2, and to not overwrite the core operators catalog.

### Pre-install Validation

Before cluster installation, plugins with `tasks/pre-install-validate.yaml` run hardware checks against discovered hosts.

### Operator Installation

Foundation plugins deploy before core operators (Quay, GitOps). This ordering ensures storage is available when Quay creates its PVCs.

```
1. Disable default CatalogSources (disconnected)
2. Deploy foundation plugins (sorted by order):
   -> load defaults -> validate requirements -> pre-validate -> mirror -> operators
   -> post-operators -> helm -> deploy -> post-validate
3. Install core operators
   -> Quay includes plugins/{storage_plugin}/tasks/quay.yaml
4. Deploy addon plugins (sorted by order)
```

### Standalone Deploy

Plugins can be deployed independently:

```bash
make deploy-plugin PLUGIN=openshift-ai
# or directly:
./scripts/deployment/deploy_plugin.sh openshift-ai
# or with Ansible:
ansible-playbook playbooks/deploy-plugin.yaml -e plugin_name=openshift-ai -e workingDir=/home/cloud-user
```

The `deploy_plugin.sh` script accepts the same environment variables as `deploy_phase.sh`:
- `STORAGE_PLUGIN` -- overrides the storage plugin (e.g., `odf`)
- `ENABLED_PLUGINS` -- comma-separated list of enabled plugins (e.g., `lvms,openshift-ai`)

### Automatic `storage_plugin` Inclusion

The `storage_plugin` value (set in `config/global.yaml`) is automatically unioned into `enabled_plugins` during variable loading. This ensures that even if a user overrides `enabled_plugins` without listing the storage plugin, it will still be mirrored and deployed. If the storage plugin is already in the list, no duplication occurs.

## Validation

Every plugin is validated at two levels:

1. **JSON Schema** (`schemas/plugin.yaml`) -- validates field types, required fields, enum values, operator structure, and registry entries. Runs via `ansible.utils.validate` during `make validate`. If a plugin provides `schemas/defaults.yaml` or `schemas/config.yaml`, those are validated against defaults and user config files as well. Test fixtures under `test-fixtures/schemas/` are also run automatically: valid fixtures must pass, invalid fixtures must fail.

2. **Shell script** (`scripts/verification/validate_plugins.sh`) -- validates directory structure:
   - `plugin.yaml` exists with required fields
   - `name` field matches the directory name
   - Plugin cannot have both `defaults.yaml` and a `defaults:` field in `plugin.yaml`
   - Top-level property names in `schemas/defaults.yaml` and `schemas/config.yaml` must start with the plugin name prefix (e.g. `lvms` → `lvmsConfig`, `lvmsDefaults`)
   - Task files are valid Ansible task lists
   - No unexpected files outside `plugin.yaml`, `defaults.yaml`, `schemas/`, `test-fixtures/`, `tasks/`, `files/`, `charts/`, and `templates/`

## Plugin Configuration

Plugins may require user-provided deployment configuration (e.g., which disks LVMS should manage, external cluster credentials). This config goes in `config/plugins/<name>.yaml`, separate from `config/global.yaml`.

- The file is auto-discovered at load time by matching `plugins/<name>/` directories — no explicit include is needed
- If no `config/plugins/<name>.yaml` exists for a plugin, nothing is loaded (no error)
- Files in `config/plugins/` that don't match any plugin directory are ignored
- Plugin authors provide `plugins/<name>/schemas/config.yaml` to validate the config file; validation runs automatically during `make validate` when the schema file exists

Example: the LVMS plugin's defaults cover most setups, but you can restrict which disks it manages:

```bash
# Create from template
cp config/plugins/lvms.example.yaml config/plugins/lvms.yaml
```

```yaml
# config/plugins/lvms.yaml
lvmsConfig:
  deviceSelector:
    optionalPaths:
      - /dev/disk/by-path/pci-0000:00:1f.2-ata-1
```

If `config/plugins/lvms.yaml` is absent, LVMS auto-detects all available disks (its default behaviour).

## Load-Time Validation

Plugins can declare requirements in the `requires` block. These are validated at two points:

1. **Early validation** (`validate_enabled_plugins.yaml`) -- runs at the start of Phase 1 (Prepare), before any downloads or mirroring begins. This is the primary gate that prevents wasting time on long-running operations when a requirement is missing.
2. **Per-plugin validation** (`deploy_plugin.yaml`) -- runs when each plugin is individually deployed. Acts as defense-in-depth.

If a requirement isn't met, the run fails with a clear error message.

Two requirement types are supported:

| Type | Purpose | Example |
|------|---------|---------|
| `vars` | Assert an Ansible variable is defined | rarely used, see note below |
| `files` | Assert a file exists in the plugin directory | `tasks/deploy.yaml` |

Each entry supports an optional `when` condition (Jinja2 expression). If the condition evaluates to false, the check is skipped.

Example from the ODF plugin:

```yaml
requires:
  files:
    - path: "tasks/deploy.yaml"
      description: "ODF deployment tasks"
    - path: "tasks/quay.yaml"
      description: "ODF Quay storage configuration"
```

**Prefer `schemas/config.yaml` over `requires.vars`** for plugin configuration variables.
If a plugin needs a user-supplied value (e.g. an endpoint URL or a license file path),
declare it as a required property in `plugins/<name>/schemas/config.yaml` and have users
supply it via `config/plugins/<name>.yaml`. Schema validation at CI time is the primary
enforcement mechanism; the playbooks trust that CI has already validated the config.

Reserve `requires.vars` for variables that genuinely cannot come from a plugin config file
(e.g. vars injected by external orchestration). Don't use it for variables that come from
`defaults.yaml` or `config/plugins/<name>.yaml`. Don't validate cluster state here
(KUBECONFIG, CRDs) -- that's what `pre-validate` is for.

## Validation-Only Plugins

A plugin doesn't have to deploy anything. If a plugin has no `operators`, and no `tasks/deploy.yaml`, all deployment steps are skipped -- only validation runs. This is useful for pre-flight checks, config verification, or environment validation that should gate the pipeline.

A validation-only plugin can hook into any of these checkpoints:

| File | Pipeline phase | Cluster access | Auto-discovered |
|------|---------------|----------------|-----------------|
| `early-validate.yaml` | Phase 1 (Prepare) start | No | Yes -- any enabled plugin with `requires` |
| `pre-install-validate.yaml` | Phase 3 (Deploy), before cluster install | No | Yes |
| `pre-validate.yaml` | Phase 5 (Operators), inside `deploy_plugin.yaml` | Yes | Only `type: foundation` plugins |
| `post-validate.yaml` | Phase 5 (Operators), inside `deploy_plugin.yaml` | Yes | Only `type: foundation` plugins |

`pre-validate.yaml` and `post-validate.yaml` run inside `deploy_plugin.yaml`, which is only triggered automatically for `type: foundation` plugins (via `deploy_plugins.yaml`). Use `type: foundation` with an `order` field for validation-only plugins that need cluster access.

Example -- a plugin that validates network config before mirroring:

```yaml
# plugins/check-network/plugin.yaml
name: check-network
type: foundation
order: 1

requires:
  vars:
    - name: externalGateway
      description: "Gateway IP from outside plugin config"
```

```yaml
# plugins/check-network/tasks/early-validate.yaml
- name: Verify gateway is reachable
  ansible.builtin.command: ping -c 1 -W 3 {{ externalGateway }}
  changed_when: false
```

## Adding a New Plugin

1. Create `plugins/your-plugin/plugin.yaml` with `name` (must match the directory name) and `type`
1. Add `operators` list if you need OLM operators
1. Add `defaults.yaml` with your configurable parameters. Prefix all top-level variable names with the plugin name (e.g. `yourPluginDefaults`, not just `defaults`).
1. If your plugin needs user-provided deployment configuration, document the expected properties and provide `plugins/your-plugin/schemas/config.yaml`. Users put their values in `config/plugins/your-plugin.yaml`.
1. Optionally add `plugins/your-plugin/schemas/defaults.yaml` to validate your defaults, with test fixtures under `plugins/your-plugin/test-fixtures/schemas/`.
1. Add `registries` if disconnected mirroring is needed
1. Add `requires` to declare variables or files that must exist at load time
1. Add `tasks/early-validate.yaml` for custom pre-flight checks that don't need cluster access (optional)
1. Add `tasks/post-operators.yaml` for setup needed after operators but before Helm or deploy (optional)
1. Add `helm` list if you need Helm chart deployments, with chart sources under `charts/` or from a remote `repo`. Set `extractImages: true` on local charts to auto-discover container images for mirroring
1. Add `tasks/deploy.yaml` with your post-operator (or post-Helm) setup logic
1. Add `tasks/quay.yaml` if your plugin provides storage for Quay
1. Run `make validate-plugins` to verify
1. Add your plugin name to `enabled_plugins` in `config/global.yaml`

No core files need to be modified.

## Current Plugins

| Plugin | Type | Order | What it does |
|--------|------|-------|--------------|
| `lvms` | foundation | 10 | LVM-based block storage for edge/single-node |
| `odf` | foundation | 10 | OpenShift Data Foundation (external Ceph) |
| `openshift-ai` | addon | 100 | OpenShift AI (RHOAI) with service mesh and dependencies |
| `nvidia-gpu` | addon | 110 | NVIDIA GPU operator for GPU-accelerated workloads |
| `example` | addon | 999 | Reference implementation (does nothing useful) |

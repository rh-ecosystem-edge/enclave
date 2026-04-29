# Enclave Plugin Architecture

## Overview

The plugin system allows components (storage backends, AI/ML stacks, GPU operators) to be packaged as self-contained units under `plugins/`. Each plugin carries its own operator definitions, mirroring configuration, deployment logic, and defaults. The core pipeline discovers and runs plugins through a standard interface without component-specific branching.

## Directory Structure

A plugin is a directory under `plugins/` with a single descriptor and optional task files:

```
plugins/lvms/
  plugin.yaml              <- the descriptor (required) -- all data in one file
  tasks/
    early-validate.yaml    <- custom validation before downloads, no cluster access (optional)
    deploy.yaml            <- post-operator deployment logic
    quay.yaml              <- Quay storage integration
    pre-validate.yaml      <- pre-deployment checks (optional)
    post-operators.yaml    <- runs after operators, before deploy (optional)
    post-validate.yaml     <- post-deployment checks (optional)
    pre-install-validate.yaml <- hardware checks before cluster install (optional)
```

### The Descriptor: `plugin.yaml`

This is the only required file. It contains all plugin data: metadata, operator definitions, defaults, and registry entries.

```yaml
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

requires:
  files:
    - path: "tasks/deploy.yaml"
      description: "LVMS deployment tasks"
    - path: "tasks/quay.yaml"
      description: "LVMS Quay storage configuration"

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

| Field | What it does |
|-------|-------------|
| `name` | Plugin identifier. Must match the directory name. |
| `type` | `foundation` -- deploys before core operators (storage, networking). `addon` -- deployed separately. |
| `order` | Deploy order among same-type plugins. Lower = first. LVMS is 10, ODF is 10. |
| `mirror` | How images are mirrored. `core` = mirrored in the core run. `plugin` = plugin runs its own oc-mirror. `none` = skip. |
| `catalog` | Operator catalog name (`redhat` or `certified`). Defaults to `redhat`. |
| `operators` | List of OLM operators to install. Each entry is passed to `configure_operator.yaml`. |
| `installOperators` | Set to `false` to skip operator installation (mirror-only plugins). Defaults to `true`. |
| `defaults` | Variables loaded into Ansible scope before tasks run. Keeps config namespaced per plugin. |
| `registries` | Registry mirror entries for MCE patching and `registries.conf`. |
| `additionalImages` | Extra images to include in the plugin's oc-mirror image set. |
| `blockedImages` | Images to exclude from mirroring (by tag, digest, or pattern). |
| `requires` | Declarative requirements validated at load time, before any deployment work begins. See [Load-Time Validation](#load-time-validation). |

The plugin descriptor is validated by JSON Schema (`schemas/plugin.yaml`) during `make validate`. The validator (`make validate-plugins`) also checks directory structure.

### Lifecycle Files

Each file is optional and lives under `tasks/`. The plugin runner (`deploy_plugin.yaml`) checks if it exists before including it. If it's missing, that step is skipped.

Here's what runs and in what order:

```
 1. Load plugin.yaml              <- always
 2. Load defaults                  <- from plugin.yaml defaults section
 3. Validate requirements          <- assert requires.vars and requires.files
 4. tasks/early-validate.yaml     <- custom validation (no cluster access)
 5. tasks/pre-validate.yaml       <- "is the cluster ready for me?"
 6. Mirror (plugin-mode only)      <- template imageset, run oc-mirror, apply manifests
 7. Install operators              <- from plugin.yaml operators list (if installOperators != false)
 8. tasks/post-operators.yaml     <- "set up infrastructure that deploy needs"
 9. tasks/deploy.yaml             <- "create my CRs"
10. tasks/post-validate.yaml      <- "did it work?"
```

Steps 3-4 are the load-time validation gate. If any declared requirement is missing or the early-validate script fails, the plugin fails immediately -- before mirroring, operator installation, or deployment. Note that `early-validate.yaml` runs before the cluster exists, so it cannot use KUBECONFIG. Use it for config format checks, external connectivity validation, or other pre-flight logic.

Step 8 (`post-operators.yaml`) is useful for plugins that need to set up infrastructure after operators are installed but before deploy tasks run. For example, a plugin might use this hook to create a CR instance and extract credentials that `tasks/deploy.yaml` depends on. Facts set in `post-operators.yaml` are available to later steps because all hooks run in the same Ansible play via `include_tasks`.

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
mirror: core

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

defaults:
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

registries:
  - location: "registry.redhat.io/lvms4"
    mirror: "lvms4"
```

The `defaults` section gets loaded into Ansible scope. The naming convention `lvmsDefaults.*` keeps things namespaced so plugins don't step on each other's variables. Users can override them in `config/global.yaml`.

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

After the operator is installed, this creates the LVMCluster CR using variables from `defaults`.

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

Plugins with `mirror: core` have their operators collected by `collect_core_plugin_operators.yaml` and included in the core oc-mirror run. Plugins with `mirror: plugin` run their own oc-mirror invocation during step 5 of their lifecycle.

Operators with `csvMirror: true` have their `csvNames` entries added as separate packages in the image set.

### Pre-install Validation

Before cluster installation, plugins with `tasks/pre-install-validate.yaml` run hardware checks against discovered hosts.

### Operator Installation

Foundation plugins deploy before core operators (Quay, GitOps). This ordering ensures storage is available when Quay creates its PVCs.

```
1. Disable default CatalogSources (disconnected)
2. Deploy foundation plugins (sorted by order):
   -> load defaults -> validate requirements -> pre-validate -> mirror -> operators
   -> post-operators -> deploy -> post-validate
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

1. **JSON Schema** (`schemas/plugin.yaml`) -- validates field types, required fields, enum values, operator structure, and registry entries. Runs via `ansible.utils.validate` during `make validate`.

2. **Shell script** (`scripts/verification/validate_plugins.sh`) -- validates directory structure:
   - `plugin.yaml` exists with required fields
   - Task files are valid Ansible task lists
   - No unexpected files outside `plugin.yaml`, `tasks/`, and `files/`

## Load-Time Validation

Plugins can declare requirements in the `requires` block. These are validated at two points:

1. **Early validation** (`validate_enabled_plugins.yaml`) -- runs at the start of Phase 1 (Prepare), before any downloads or mirroring begins. This is the primary gate that prevents wasting time on long-running operations when a requirement is missing.
2. **Per-plugin validation** (`deploy_plugin.yaml`) -- runs when each plugin is individually deployed. Acts as defense-in-depth.

If a requirement isn't met, the run fails with a clear error message.

Two requirement types are supported:

| Type | Purpose | Example |
|------|---------|---------|
| `vars` | Assert an Ansible variable is defined | `odfExternalConfig` |
| `files` | Assert a file exists in the plugin directory | `tasks/deploy.yaml` |

Each entry supports an optional `when` condition (Jinja2 expression). If the condition evaluates to false, the check is skipped.

Example from the ODF plugin:

```yaml
requires:
  vars:
    - name: odfExternalConfig
      when: "{{ storage_plugin == 'odf' }}"
      description: "External Ceph cluster connection details from ceph-external-cluster-details-exporter.py"
  files:
    - path: "tasks/deploy.yaml"
      description: "ODF deployment tasks"
    - path: "tasks/quay.yaml"
      description: "ODF Quay storage configuration"
```

Don't add `requires.vars` entries for variables that come from `plugin.defaults` -- those are always defined after defaults loading. Don't validate cluster state here (KUBECONFIG, CRDs) -- that's what `pre-validate` is for.

## Validation-Only Plugins

A plugin doesn't have to deploy anything. If a plugin has no `operators`, no `mirror` config, and no `tasks/deploy.yaml`, all deployment steps are skipped -- only validation runs. This is useful for pre-flight checks, config verification, or environment validation that should gate the pipeline.

A validation-only plugin can hook into any of these checkpoints:

| File | Pipeline phase | Cluster access | Auto-discovered |
|------|---------------|----------------|-----------------|
| `early-validate.yaml` | Phase 1 (Prepare) start | No | Yes -- any enabled plugin with `requires` |
| `pre-install-validate.yaml` | Phase 3 (Deploy), before cluster install | No | Yes |
| `pre-validate.yaml` | Phase 5 (Operators), inside `deploy_plugin.yaml` | Yes | Only `type: foundation` plugins |
| `post-validate.yaml` | Phase 5 (Operators), inside `deploy_plugin.yaml` | Yes | Only `type: foundation` plugins |

`pre-validate.yaml` and `post-validate.yaml` run inside `deploy_plugin.yaml`, which is only triggered automatically for `type: foundation` plugins (via `deploy_foundation_plugins.yaml`). Use `type: foundation` with an `order` field for validation-only plugins that need cluster access.

Example -- a plugin that validates network config before mirroring:

```yaml
# plugins/check-network/plugin.yaml
name: check-network
type: foundation
order: 1

requires:
  vars:
    - name: externalGateway
      description: "Gateway IP for external network"
```

```yaml
# plugins/check-network/tasks/early-validate.yaml
- name: Verify gateway is reachable
  ansible.builtin.command: ping -c 1 -W 3 {{ externalGateway }}
  changed_when: false
```

## Adding a New Plugin

1. Create `plugins/your-plugin/plugin.yaml` with `name` and `type`
2. Add `operators` list if you need OLM operators
3. Add `defaults` with your configurable parameters
4. Add `registries` if disconnected mirroring is needed
5. Add `requires` to declare variables or files that must exist at load time
6. Add `tasks/early-validate.yaml` for custom pre-flight checks that don't need cluster access (optional)
7. Add `tasks/post-operators.yaml` for setup needed after operators but before deploy (optional)
8. Add `tasks/deploy.yaml` with your post-operator setup logic
9. Add `tasks/quay.yaml` if your plugin provides storage for Quay
10. Run `make validate-plugins` to verify
11. Add your plugin name to `enabled_plugins` in `config/global.yaml`

No core files need to be modified.

## Current Plugins

| Plugin | Type | Order | Mirror | What it does |
|--------|------|-------|--------|-------------|
| `lvms` | foundation | 10 | core | LVM-based block storage for edge/single-node |
| `odf` | foundation | 10 | core | OpenShift Data Foundation (external Ceph) |
| `openshift-ai` | addon | 100 | plugin | OpenShift AI (RHOAI) with service mesh and dependencies |
| `nvidia-gpu` | addon | 110 | plugin | NVIDIA GPU operator for GPU-accelerated workloads |
| `example` | addon | 999 | none | Reference implementation (does nothing useful) |

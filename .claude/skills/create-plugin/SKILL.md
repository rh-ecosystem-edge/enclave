---
name: create-plugin
description: Creates and manages Enclave plugins. Handles creating new plugins from scratch, adding operators or Helm charts, adding lifecycle tasks (deploy, pre-validate, post-operators, post-validate), creating schemas and test fixtures, and fixing plugin validation errors.
---

# Enclave Plugin Management

Create, modify, and validate Enclave plugins. Plugins are self-contained components under `plugins/` that carry their own operator definitions, Helm charts, deployment logic, defaults, and schemas.

## Authoritative References

- Architecture: `docs/PLUGIN_ARCHITECTURE.md`
- Config/schema design doc: `docs/design/plugin-config-and-schema-split.md`
- Plugin descriptor schema: `schemas/plugin.yaml`
- Shared type definitions: `schemas/definitions.yaml`
- Validation script: `scripts/verification/validate_plugins.sh`
- Schema validation playbook: `playbooks/validation/validate-schema.yaml`

## Before Starting

Gather the following from the user:

1. **Plugin name** -- lowercase, alphanumeric with hyphens/dots/underscores (pattern: `^[A-Za-z0-9][A-Za-z0-9._-]*$`). This becomes both the directory name and the `name` field in `plugin.yaml`.
2. **Plugin type** -- `foundation` (deploys before core operators, used for storage/networking) or `addon` (deploys after core operators).
3. **Order** -- integer controlling deploy sequence among same-type plugins. Foundation plugins typically use lower numbers (e.g., 10). Check existing plugins in `plugins/*/plugin.yaml` for current order values to avoid collisions.
4. **What the plugin deploys** -- OLM operators, Helm charts, custom resources, or a combination.

## Workflow 1: Create a New Plugin

### Step 1: Create the plugin directory and descriptor

Create `plugins/<name>/plugin.yaml` with required fields.

**Minimal plugin (operator only):**

```yaml
---
name: <name>
type: addon
order: <number>

operators:
  - name: <operator-package-name>
    version: <version>
    channel: <channel>
    init_version: <version>
    namespace: <target-namespace>
```

**Minimal plugin (Helm only):**

```yaml
---
name: <name>
type: addon
order: <number>

helm:
  - release: <release-name>
    namespace: <target-namespace>
    chart: charts/<chart-name>
    valuesTemplate: templates/values.yaml.j2
    createNamespace: true
    timeout: "15m"
    wait: true
```

**Minimal plugin (no deployment, validation only):**

```yaml
---
name: <name>
type: foundation
order: 1
```

See `docs/PLUGIN_ARCHITECTURE.md` for the full plugin.yaml field reference.

### Step 2: Create defaults

**Preferred approach: `plugins/<name>/defaults.yaml` file.** Use inline `defaults:` in plugin.yaml only for very simple cases. Never use both.

Create `plugins/<name>/defaults.yaml` with ALL variables (both internal and user-facing), each with a sensible default value:

```yaml
---
# Internal variables (not exposed to users)
<name>_ns: <namespace>
<name>_resource_name: <name>

# User-facing variables (also exposed in config/plugins/<name>.example.yaml)
<name>_instances: 1
<name>_deploy_feature: true
<name>_storage_size: 5Gi
```

**CRITICAL RULE:** All top-level variable names MUST be prefixed with the plugin name. Accepted prefix styles:
- snake_case: `my_plugin_setting`
- camelCase: `myPluginSetting` or `myPluginDefaults`
- For single-word names: `lvmsDefaults`, `lvmsConfig`

Check existing plugins in `plugins/*/defaults.yaml` for real naming examples.


### Step 3: Create defaults schema

Create `plugins/<name>/schemas/defaults.yaml` -- validates ALL variables in defaults.yaml:

```yaml
---
"$schema": "http://json-schema.org/draft-07/schema"
type: object
additionalProperties: false
required:
  - <name>_ns
  - <name>_resource_name
  - <name>_instances
  - <name>_deploy_feature
  - <name>_storage_size
properties:
  <name>_ns:
    "$ref": "#/definitions/nonEmptyString"
    description: Namespace for the deployment.
  <name>_resource_name:
    "$ref": "#/definitions/nonEmptyString"
    description: Name of the primary resource.
  <name>_instances:
    type: integer
    minimum: 1
    description: Number of replicas.
  <name>_deploy_feature:
    type: boolean
    description: Enable or disable the feature.
  <name>_storage_size:
    "$ref": "#/definitions/k8sQuantity"
    description: Storage volume size.
```

**Schema rules:**
- MUST use `"$schema": "http://json-schema.org/draft-07/schema"` (draft-07)
- MUST set `additionalProperties: false` at the top level
- All property names MUST start with the plugin name prefix
- Use `"$ref": "#/definitions/nonEmptyString"` for required string fields -- definitions are in `schemas/definitions.yaml` and merged automatically at validation time. Do NOT define them locally in the schema.
- Every property in defaults.yaml SHOULD be listed in `required`

Read `schemas/definitions.yaml` for available shared type definitions (e.g., `nonEmptyString`, `k8sQuantity`, `httpUrl`).

### Step 4: Create defaults test fixtures

**Required: valid fixture**

Create `plugins/<name>/test-fixtures/schemas/defaults/valid/base.yaml` -- must be a copy of defaults.yaml values that passes schema validation:

```yaml
---
<name>_ns: <namespace>
<name>_resource_name: <name>
<name>_instances: 1
<name>_deploy_feature: true
<name>_storage_size: 5Gi
```

**Required: invalid fixture (unknown property)**

Create `plugins/<name>/test-fixtures/schemas/defaults/invalid/unknown-property.yaml` -- include at least one valid key plus an unknown key:

```yaml
---
<name>_ns: <namespace>
unknownField: this-should-fail
```

**Optional: invalid fixture (missing required)**

Create `plugins/<name>/test-fixtures/schemas/defaults/invalid/missing-required.yaml` -- omit a required field:

```yaml
---
<name>_instances: 1
```

### Step 5: Create user-facing configuration

User-facing variables are a subset of defaults that users can override via `config/plugins/<name>.yaml`. Every user-facing variable MUST have a default value in `defaults.yaml` (unless it's a mandatory user input like a license file path).

**Create `config/plugins/<name>.example.yaml`** -- a template users copy to `config/plugins/<name>.yaml`:

```yaml
---
##############################################################################
# <NAME> PLUGIN CONFIGURATION TEMPLATE
# Instructions: Copy this file to 'config/plugins/<name>.yaml' and set values.
# All fields are optional; defaults are shown below.
##############################################################################

# Number of replicas.
# <name>_instances: 1

# Enable or disable the feature.
# <name>_deploy_feature: true

# Storage volume size.
# <name>_storage_size: 5Gi
```

Comment out all optional fields (showing defaults). Leave mandatory fields uncommented with a placeholder.

**Create `plugins/<name>/schemas/config.yaml`** -- validates user config:

```yaml
---
"$schema": "http://json-schema.org/draft-07/schema"
type: object
additionalProperties: false
properties:
  <name>_instances:
    type: integer
    minimum: 1
    description: Number of replicas.
  <name>_deploy_feature:
    type: boolean
    description: Enable or disable the feature.
  <name>_storage_size:
    "$ref": "#/definitions/k8sQuantity"
    description: Storage volume size.
```

Config schema rules:
- Same structure as defaults schema (`additionalProperties: false`, draft-07)
- Only include user-facing variables, NOT internal ones (e.g., `<name>_ns` and `<name>_resource_name` are NOT in config schema)
- No `required` section unless the variable is a mandatory user input with no default (e.g., license file path)
- Properties here are a subset of what's in `schemas/defaults.yaml`

**Create config test fixtures:**

`test-fixtures/schemas/config/valid/base.yaml`:
```yaml
---
<name>_instances: 2
```

`test-fixtures/schemas/config/invalid/unknown-property.yaml`:
```yaml
---
<name>_instances: 2
unknownField: this-should-fail
```

**Variable layering (in order of precedence):**
1. `config/plugins/<name>.yaml` -- user overrides (highest)
2. `plugins/<name>/defaults.yaml` -- developer defaults (lowest)

### Step 6: Validate

```bash
make -f Makefile.ci validate-plugins
make -f Makefile.ci validate-yaml
make -f Makefile.ci validate-ansible
```

## Workflow 2: Add Lifecycle Tasks

Lifecycle tasks are Ansible task lists under `plugins/<name>/tasks/`. See [LIFECYCLE-TASKS.md](LIFECYCLE-TASKS.md) for execution order, task file conventions, retry strategy, and templates for each task type (pre-validate, deploy, post-operators, post-validate, templates).

## Workflow 3: Add Operators to a Plugin

### OLM operator entry

Add to the `operators` list in `plugin.yaml`:

```yaml
operators:
  - name: <operator-package-name>      # Required: package name from catalog
    version: <version>                  # Required: operator version
    channel: <channel>                  # Required: update channel
    init_version: <version>             # Optional: initial install version
    namespace: <namespace>              # Optional: for namespace-scoped operators
    source: <catalog-source>            # Optional: catalog source name
    csvNames:                           # Optional: for mirroring CSV images
      - <csv-name>
    csvMirror: true                     # Optional: mirror CSV images (requires csvNames)
    extraMirrorPackages:                # Optional: additional packages to mirror
      - <package-name>
    global: true                        # Optional: watch all namespaces
```

### When to use each optional field

- `init_version`: Set to the same as `version` for new plugins. Tracks initial install version separately.
- `namespace`: Required for namespace-scoped operators. Omit for cluster-scoped.
- `csvNames`: Needed when the CSV name differs from the package name, or for mirroring specific CSVs.
- `csvMirror: true`: Mirror images from CSVs in `csvNames`. Requires `csvNames`.
- `global: true`: Configures the operator subscription to watch all namespaces.

### Disconnected/air-gapped support

If the operator pulls images from non-default registries, add mirror entries:

```yaml
registries:
  - location: "registry.redhat.io/<path>"
    mirror: "<path>"

additionalImages:
  - <full-image-reference>:<tag>
```

### Mirror-only plugin

Set `installOperators: false` to mirror images without installing the operator on the hub cluster:

```yaml
installOperators: false
```

## Workflow 4: Add Helm Charts to a Plugin

### Local chart (bundled in repo)

Place the chart under `plugins/<name>/charts/<chart-name>/`:

```yaml
helm:
  - release: <release-name>
    namespace: <target-namespace>
    chart: charts/<chart-name>
    valuesTemplate: templates/values.yaml.j2
    createNamespace: true
    extractImages: true
    timeout: "15m"
    wait: true
```

Set `extractImages: false` if the chart has required values not available at image-extraction time.

### Remote chart (from Helm repo)

```yaml
helm:
  - release: <release-name>
    repo: https://<helm-repo-url>
    chart: <chart-name>
    version: <chart-version>
    namespace: <target-namespace>
    createNamespace: false
```

### Values template vs values file

Use ONE (never both):
- `valuesTemplate`: Jinja2 template rendered with Ansible variables. Use when values depend on runtime state.
- `valuesFile`: Static YAML file. Use when values are fixed.

## Workflow 5: Validate and Troubleshoot

### Validation commands

```bash
make -f Makefile.ci validate-plugins    # Structure, naming, schema validation
make -f Makefile.ci validate-yaml       # yamllint
make -f Makefile.ci validate-ansible    # ansible-lint on task files
make -f Makefile.ci validate            # All checks at once
```

### Common errors and fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `plugin.yaml name does not match directory name` | `name` field differs from directory | Set `name` to exactly match directory name |
| `Has both defaults.yaml and a defaults: field` | Both present | Remove one. Prefer `defaults.yaml` for complex defaults |
| `properties [X] must start with one of [prefixes]` | Schema property not prefixed | Prefix all top-level schema properties with the plugin name |
| `additionalProperties not set to false` | Missing in schema | Add `additionalProperties: false` at top level |
| `Unexpected file or directory: X` | File not in allowed set | Only allowed: plugin.yaml, defaults.yaml, tasks/, files/, charts/, templates/, schemas/, test-fixtures/ |
| Valid fixture fails validation | Fixture missing required fields | Ensure valid fixture includes all `required` fields with correct types |
| Invalid fixture passes validation | Fixture does not violate schema | Add `unknownField: this-should-fail` (caught by `additionalProperties: false`) |
| `Expected YAML list of tasks, got dict` | Task file has playbook header | Task files must be plain lists of tasks, not playbooks |

## Plugin Independence Rules

See `docs/PLUGIN_ARCHITECTURE.md` for full plugin independence rules. Key points: no cross-plugin variable references, shared values must be defined independently in each plugin with the same default, and `requires.vars` is for external variables only (not plugin defaults).

## Reference Implementations

Browse existing plugins in `plugins/` for real examples. To find plugins with specific features:

```bash
# List all plugins with their type and order
grep -r 'type:\|order:' plugins/*/plugin.yaml

# Find plugins with defaults files
ls plugins/*/defaults.yaml

# Find plugins with schemas
ls plugins/*/schemas/

# Find plugins with lifecycle tasks
ls plugins/*/tasks/
```

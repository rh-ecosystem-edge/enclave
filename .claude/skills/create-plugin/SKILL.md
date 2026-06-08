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

1. **Plugin name** -- lowercase alphanumeric with hyphens, dots, or underscores. Must match the directory name exactly and the `name` field in `plugin.yaml`.
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

Always use `plugins/<name>/defaults.yaml`. Never use inline `defaults:` in plugin.yaml — it is deprecated.

Create `plugins/<name>/defaults.yaml` with all variables that have default values (both internal and user-facing). User-facing configuration variables that are optional or do not have a default value (e.g., external cluster connection details, license file paths) need to be validated in `schemas/config.yaml` (as `required` properties) and documented in `config/plugins/<name>.example.yaml`, but not added to `defaults.yaml`.

```yaml
---
# Internal variables (not exposed to users)
myPluginNamespace: my-plugin
myPluginResourceName: my-plugin

# User-facing variables (also exposed in config/plugins/<name>.example.yaml)
myPluginInstances: 1
myPluginDeployFeature: true
myPluginStorageSize: 5Gi
```

**CRITICAL RULE:** All top-level variable names MUST be prefixed with the plugin name using camelCase (e.g., `myPluginNamespace`, `myPluginStorageSize`, `lvmsDefaults`).

Check existing plugins in `plugins/*/defaults.yaml` for real naming examples.

### Step 3: Create defaults schema

Create `plugins/<name>/schemas/defaults.yaml` -- validates ALL variables in defaults.yaml:

```yaml
---
"$schema": "http://json-schema.org/draft-07/schema"
type: object
additionalProperties: false
required:
  - myPluginNamespace
  - myPluginResourceName
  - myPluginInstances
  - myPluginDeployFeature
  - myPluginStorageSize
properties:
  myPluginNamespace:
    "$ref": "#/definitions/nonEmptyString"
    description: Namespace for the deployment.
  myPluginResourceName:
    "$ref": "#/definitions/nonEmptyString"
    description: Name of the primary resource.
  myPluginInstances:
    type: integer
    minimum: 1
    description: Number of replicas.
  myPluginDeployFeature:
    type: boolean
    description: Enable or disable the feature.
  myPluginStorageSize:
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
myPluginNamespace: my-plugin
myPluginResourceName: my-plugin
myPluginInstances: 1
myPluginDeployFeature: true
myPluginStorageSize: 5Gi
```

**Required: invalid fixture (unknown property)**

Create `plugins/<name>/test-fixtures/schemas/defaults/invalid/unknown-property.yaml` -- include at least one valid key plus an unknown key:

```yaml
---
myPluginNamespace: my-plugin
unknownField: this-should-fail
```

**Optional: invalid fixture (missing required)**

Create `plugins/<name>/test-fixtures/schemas/defaults/invalid/missing-required.yaml` -- omit a required field:

```yaml
---
myPluginInstances: 1
```

### Step 5: Create user-facing configuration

User-facing configuration variables must be defined at `config/plugins/<name>.yaml`. Variables with default values must also appear in `defaults.yaml`. Mandatory user inputs with no default (e.g., license file paths, external cluster connection details) only appear in the config schema.

**Create `config/plugins/<name>.example.yaml`** -- a template users copy to `config/plugins/<name>.yaml`:

```yaml
---
##############################################################################
# <NAME> PLUGIN CONFIGURATION TEMPLATE
# Instructions: Copy this file to 'config/plugins/<name>.yaml' and set values.
# All fields are optional; defaults are shown below.
##############################################################################

# Number of replicas.
# myPluginInstances: 1

# Enable or disable the feature.
# myPluginDeployFeature: true

# Storage volume size.
# myPluginStorageSize: 5Gi
```

Comment out all optional fields (showing defaults). Leave mandatory fields uncommented with a placeholder.

**Create `plugins/<name>/schemas/config.yaml`** -- validates user config:

```yaml
---
"$schema": "http://json-schema.org/draft-07/schema"
type: object
additionalProperties: false
properties:
  myPluginInstances:
    type: integer
    minimum: 1
    description: Number of replicas.
  myPluginDeployFeature:
    type: boolean
    description: Enable or disable the feature.
  myPluginStorageSize:
    "$ref": "#/definitions/k8sQuantity"
    description: Storage volume size.
```

Config schema rules:
- Same structure as defaults schema (`additionalProperties: false`, draft-07)
- Include only variables the plugin author wants to expose to users
- No `required` section unless the variable is a mandatory user input with no default (e.g., a license file path or external cluster connection details)
- Properties may overlap with `schemas/defaults.yaml` (user overrides) or be unique to config (mandatory user inputs with no default)

**Required config file pattern:** when the config file is mandatory (has `required` properties with no default), add a `requires.files` entry in `plugin.yaml` so deployment fails early with a clear message instead of a cryptic undefined variable error:

```yaml
requires:
  files:
    - path: "../../config/plugins/<name>.yaml"
      description: "<Name> user configuration"
```

The schema validates the file's content at CI time; the `requires.files` entry validates the file's existence at deploy time.

**Create config test fixtures:**

`test-fixtures/schemas/config/valid/base.yaml`:

```yaml
---
myPluginInstances: 2
```

`test-fixtures/schemas/config/invalid/unknown-property.yaml`:

```yaml
---
myPluginInstances: 2
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

Lifecycle tasks are Ansible task lists under `plugins/<name>/tasks/`. See `docs/PLUGIN_ARCHITECTURE.md` (section "Lifecycle Files") for the full execution order and detailed behavior of each step.

### Task file conventions

Every task file MUST:
- Be a valid YAML list of task mappings (no playbook header, no `hosts:` key)
- Start with `---`
- Have descriptive `name:` on every task

**Retry strategy -- two patterns:**
- `retries: "{{ k8s_retries }}"` / `delay: "{{ k8s_delay }}"` -- for checks where the resource should already exist (CRD registered, deployment available). Guards against transient API failures, not long waits.
- Hardcoded higher values (e.g., `retries: 60` / `delay: 15`) -- for waiting on state changes that take real time (CR becoming Ready, operator starting pods). Values depend on the specific resource and expected cluster pressure.

### Real plugin examples

Browse `plugins/*/tasks/` for real examples. Key references:

- **pre-validate**: `plugins/trust-manager/tasks/pre-validate.yaml` -- checks that cert-manager CRD and deployment exist before trust-manager deploys
- **deploy**: `plugins/trust-manager/tasks/deploy.yaml` -- waits for operator CRD/deployment, applies manifests, waits for CR readiness
- **post-operators**: `plugins/vast-csi/tasks/post-operators.yaml` -- creates CRs after OLM operator install, before deploy.yaml runs (not limited to Helm-deployed plugins)
- **post-validate**: `plugins/vast-csi/tasks/post-validate.yaml` -- verifies CSI drivers and StorageClasses exist after deployment

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
| `Has both defaults.yaml and a defaults: field` | Both present | Remove the `defaults:` field from plugin.yaml. Use `defaults.yaml`. |
| `properties [X] must start with one of [prefixes]` | Schema property not prefixed | Prefix all top-level schema properties with the plugin name |
| `additionalProperties not set to false` | Missing in schema | Add `additionalProperties: false` at top level |
| `Unexpected file or directory: X` | File not in allowed set | Only allowed: plugin.yaml, defaults.yaml, tasks/, files/, charts/, templates/, schemas/, test-fixtures/ |
| Valid fixture fails validation | Fixture missing required fields | Ensure valid fixture includes all `required` fields with correct types |
| Invalid fixture passes validation | Fixture does not violate schema | Add `unknownField: this-should-fail` (caught by `additionalProperties: false`) |
| `Expected YAML list of tasks, got dict` | Task file has playbook header | Task files must be plain lists of tasks, not playbooks |

## Plugin Independence Rules

See `docs/PLUGIN_ARCHITECTURE.md` for full plugin independence rules. Key points: no cross-plugin variable references, shared values must be defined independently in each plugin with the same default.

**Prefer `schemas/config.yaml` over `requires.vars`** for plugin configuration variables. If a plugin needs user-supplied values, declare them in `schemas/config.yaml` and have users supply them via `config/plugins/<name>.yaml`. Reserve `requires.vars` for variables that genuinely cannot come from a plugin config file (e.g., vars injected by external orchestration).

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

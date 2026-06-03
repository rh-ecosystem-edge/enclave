# Design doc: Declarative Kubernetes resource dependencies for plugins

## Author/date

Maor Friedman / 2026-06-03

## Tracking JIRA

OSAC-1240

## Problem Statement

Plugins often depend on Kubernetes resources being present before they can deploy. For example:
- `trust-manager` requires the `certificates.cert-manager.io` CRD from cert-manager operator
- `trust-manager` also requires the `cert-manager` Deployment to be running
- Future plugins may require storage CRDs from LVMS, ODF, or VAST-CSI plugins
- Plugins may require specific Namespaces or other core resources to exist

Currently, each plugin implements this validation manually in `tasks/pre-validate.yaml` with repetitive boilerplate:
1. Call `kubernetes.core.k8s_info` to check if resource exists
2. Fail with custom error message if not found

This approach has several problems:
- **Code duplication**: Same pattern repeated across multiple plugins
- **Inconsistent error messages**: Each plugin writes their own error text
- **Hidden dependencies**: Requirements not visible in plugin.yaml descriptor
- **Maintenance burden**: Boilerplate to update if validation logic changes

## Goals

- Provide a declarative way to specify Kubernetes resource dependencies in `plugin.yaml`
- Automatically validate required resources exist before plugin deployment
- Work for any Kubernetes resource kind (CRDs, Deployments, Namespaces, ConfigMaps, etc.)
- Generate consistent, clear error messages for missing dependencies
- Eliminate manual validation boilerplate from plugin task files
- Make plugin dependencies self-documenting and visible in the descriptor

## Non-objectives

- **Automatic plugin ordering**: Plugin deployment order still uses the numeric `order` field. This design does NOT introduce topological sorting or automatic dependency resolution between plugins.
- **Availability checks**: Only checks resource existence, not health/readiness (e.g., doesn't check Deployment replicas are ready)
- **Resource creation**: Does not create missing resources, only validates they exist
- **Load-time validation**: Validation runs at deployment time with cluster access, not at early validate time

## Proposal

### 1. Extend plugin schema

Add a `resources` field under the existing `requires` block in `schemas/plugin.yaml`:

```yaml
requires:
  type: object
  description: Declarative requirements validated at plugin load time.
  additionalProperties: false
  properties:
    vars:
      type: array
      description: Ansible variables that must be defined.
      items:
        $ref: "#/definitions/requirement_var"
    files:
      type: array
      description: Files that must exist within the plugin directory.
      items:
        $ref: "#/definitions/requirement_file"
    resources:  # NEW
      type: array
      description: Kubernetes resources that must exist before this plugin deploys.
      items:
        $ref: "#/definitions/requirement_resource"
```

Define a new `requirement_resource` type:

```yaml
definitions:
  requirement_resource:
    type: object
    additionalProperties: false
    properties:
      apiVersion:
        type: string
        description: |
          API version (e.g., apiextensions.k8s.io/v1, apps/v1, v1).
      kind:
        type: string
        description: |
          Resource kind (e.g., CustomResourceDefinition, Deployment, Namespace).
      name:
        type: string
        description: Resource name.
      namespace:
        type: string
        description: |
          Namespace (optional, for namespaced resources). Omit for cluster-scoped resources.
      when:
        type: string
        description: |
          Jinja2 condition — skip check if false (same semantics as requirement_var and requirement_file).
    required:
      - apiVersion
      - kind
      - name
```

### 2. Add automated validation to deploy_plugin.yaml

Insert validation tasks in `playbooks/tasks/deploy_plugin.yaml` after line 123 (after file requirements validation, before the `pre-validate` hook):

```yaml
- name: Check required Kubernetes resources exist
  kubernetes.core.k8s_info:
    api_version: "{{ item.apiVersion }}"
    kind: "{{ item.kind }}"
    name: "{{ item.name }}"
    namespace: "{{ item.namespace | default(omit) }}"
  loop: "{{ plugin.requires.resources | default([]) }}"
  loop_control:
    label: "{{ item.kind }}/{{ item.name }}"
  register: r_required_resources
  environment:
    KUBECONFIG: "{{ workingDir }}/ocp-cluster/auth/kubeconfig"
  when:
    - plugin.requires is defined
    - plugin.requires.resources is defined
    - item.when | default(true) | bool
  tags: pre-validate

- name: Fail if required resources are missing
  ansible.builtin.fail:
    msg: >-
      Plugin '{{ plugin.name }}' requires {{ item.item.kind }} '{{ item.item.name }}'
      {% if item.item.namespace is defined %}in namespace '{{ item.item.namespace }}'{% endif %}
      but it was not found.
      Ensure the operator or plugin that provides this resource is installed and deployed first.
  loop: "{{ r_required_resources.results }}"
  loop_control:
    label: "{{ item.item.kind }}/{{ item.item.name }}"
  when:
    - plugin.requires is defined
    - plugin.requires.resources is defined
    - item.item.when | default(true) | bool
    - item.resources | length == 0
  tags: pre-validate
```

**Design notes:**
- Validation runs in the `pre-validate` tag phase, before operators are installed
- Uses same `when` conditional pattern as `requirement_var` and `requirement_file`
- Requires `KUBECONFIG` access, so this is a runtime check, not early validation
- Uses `omit` filter for namespace to support cluster-scoped resources
- Consistent error message format across all plugins

### 3. Migrate trust-manager plugin

Update `plugins/trust-manager/plugin.yaml`:

```yaml
name: trust-manager
type: addon
order: 100

operators:
  - name: trust-manager
    version: 0.14.0
    channel: stable
    init_version: 0.14.0
    namespace: cert-manager

requires:
  resources:
    - apiVersion: apiextensions.k8s.io/v1
      kind: CustomResourceDefinition
      name: certificates.cert-manager.io
    - apiVersion: apps/v1
      kind: Deployment
      name: cert-manager
      namespace: cert-manager
```

Delete `plugins/trust-manager/tasks/pre-validate.yaml` entirely. The manual validation logic (lines 2-27) is replaced by the declarative `requires.resources` field.

### 4. Update plugin validation

Resource requirement validation happens at two levels:
- **Schema validation** (CI-time): JSON Schema validates field types and required fields (apiVersion, kind, name)
- **Runtime validation** (deployment-time): Ansible evaluates Jinja2 `when` conditions and validates resource existence

No additional validation code needed in `scripts/verification/validate_plugins.sh` - JSON Schema handles structure, and Ansible handles Jinja2 syntax at runtime.

## Examples

### CRD dependency

```yaml
requires:
  resources:
    - apiVersion: apiextensions.k8s.io/v1
      kind: CustomResourceDefinition
      name: lvmclusters.lvm.topolvm.io
```

### Storage class dependency

```yaml
requires:
  resources:
    - apiVersion: storage.k8s.io/v1
      kind: StorageClass
      name: lvms-vg1
```

### Namespace prerequisite

```yaml
requires:
  resources:
    - apiVersion: v1
      kind: Namespace
      name: my-required-namespace
```

### Conditional requirement

```yaml
requires:
  resources:
    - apiVersion: v1
      kind: Secret
      name: gpu-operator-credentials
      namespace: openshift-operators
      when: "{{ enableGPU | default(false) }}"
```

### Multiple dependencies

```yaml
requires:
  resources:
    - apiVersion: apiextensions.k8s.io/v1
      kind: CustomResourceDefinition
      name: certificates.cert-manager.io
    - apiVersion: apps/v1
      kind: Deployment
      name: cert-manager
      namespace: cert-manager
    - apiVersion: v1
      kind: Namespace
      name: cert-manager
```

## Alternatives considered

### Option A: Plugin-to-plugin dependencies with topological sorting

Instead of `requires.resources`, add `requires.plugins: [lvms, odf]` and implement topological sorting of plugin deployment order.

**Rejected because:**
- Much more complex: requires graph algorithms, cycle detection, and maintaining a CRD→plugin mapping
- The `order` field already provides explicit ordering control
- Users may want to order plugins differently than dependency relationships dictate
- CRD validation is useful even without automatic ordering (validates core operators too)
- This can be added later if needed, building on top of resource validation

### Option B: Only support CRDs

Instead of generic resources, only add `requires.crds: [certificates.cert-manager.io]`.

**Rejected because:**
- Less flexible: doesn't cover Deployments, Namespaces, Secrets, etc.
- trust-manager already checks both CRD and Deployment
- The schema complexity is nearly identical (just add `kind` and `apiVersion` fields)
- No significant simplification benefit

### Option C: Keep validation in task files, add helper

Create a reusable task file `playbooks/tasks/validate_resource_exists.yaml` that plugins can include.

**Rejected because:**
- Still requires boilerplate in every plugin's pre-validate
- Dependencies remain hidden (not in plugin.yaml)
- Doesn't solve the self-documentation problem
- Only slightly reduces code duplication

## Migration path

1. Implement schema changes and validation logic in `deploy_plugin.yaml`
2. Migrate `trust-manager` plugin as reference example
3. Document in plugin architecture docs
4. Gradually migrate other plugins as needed (non-blocking, opt-in)
5. Eventually deprecate manual resource checks in pre-validate tasks

Backward compatible: existing plugins continue to work unchanged.

## Testing

1. **Schema validation**: Run `make -f Makefile.ci validate-plugins` after schema changes
2. **Unit tests**: Not applicable (no Python code)
3. **Integration test - positive path**:
   - Deploy trust-manager with cert-manager already installed
   - Verify deployment succeeds with no errors
4. **Integration test - missing CRD**:
   - Deploy trust-manager without cert-manager
   - Verify clear error: "Plugin 'trust-manager' requires CustomResourceDefinition 'certificates.cert-manager.io' but it was not found"
5. **Integration test - missing Deployment**:
   - Install cert-manager CRDs but not operator
   - Deploy trust-manager
   - Verify clear error: "Plugin 'trust-manager' requires Deployment 'cert-manager' in namespace 'cert-manager' but it was not found"
6. **Integration test - conditional skip**:
   - Add a resource with `when: false`
   - Verify it's not checked

## Benefits

- **DRY**: Eliminate repetitive validation boilerplate from plugin tasks
- **Flexible**: Works for any Kubernetes resource kind
- **Self-documenting**: Dependencies explicit and visible in plugin.yaml
- **Consistent errors**: All validation failures use same clear message format
- **Maintainable**: Centralized validation logic, easier to enhance
- **Opt-in**: Existing plugins work unchanged, new plugins adopt as needed

## Impact

- **Plugin authors**: Can declare dependencies in plugin.yaml instead of writing validation tasks
- **Deployment**: Fails earlier with clearer errors if prerequisites are missing
- **Documentation**: Plugin dependencies self-evident from descriptor
- **Maintenance**: Less code to maintain across plugin ecosystem

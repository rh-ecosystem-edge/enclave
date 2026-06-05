# Lifecycle Tasks

Hooks are Ansible task lists under `plugins/<name>/tasks/`. Execution order:

```
1. tasks/early-validate.yaml      NO cluster access, local validation only
2. tasks/pre-validate.yaml        Cluster access, check prerequisites
3. tasks/post-operators.yaml      After operators installed, before Helm
4. [Helm chart deployment]        Automatic from plugin.yaml helm list
5. tasks/deploy.yaml              Create CRs, apply manifests
6. tasks/post-validate.yaml       Verify deployment success
```

Special hooks (called outside the normal lifecycle):
- `tasks/quay.yaml` -- Quay storage backend integration
- `tasks/pre-install-validate.yaml` -- Before cluster install, no cluster access

## Task file conventions

Every task file MUST:
- Be a valid YAML list of task mappings (no playbook header, no `hosts:` key)
- Start with `---`
- Have descriptive `name:` on every task
- Use `__r_` prefix for registered variables (double underscore = private)

**Retry strategy -- two patterns:**
- `retries: "{{ k8s_retries }}"` / `delay: "{{ k8s_delay }}"` -- for checks where the resource should already exist (CRD registered, deployment available). Guards against transient API failures, not long waits.
- Hardcoded higher values (e.g., `retries: 60` / `delay: 15`) -- for waiting on state changes that take real time (CR becoming Ready, operator starting pods). Values depend on the specific resource and expected cluster pressure.

## pre-validate.yaml template

Check cluster prerequisites:

```yaml
---
- name: Check <prerequisite> CRD exists
  kubernetes.core.k8s_info:
    api_version: apiextensions.k8s.io/v1
    kind: CustomResourceDefinition
    name: <crd-name>
  register: __r_prereq_crd
  retries: "{{ k8s_retries }}"
  delay: "{{ k8s_delay }}"
  until: __r_prereq_crd is success

- name: Fail if <prerequisite> is not installed
  ansible.builtin.fail:
    msg: "<prerequisite> CRD not found. Install before <plugin>."
  when: __r_prereq_crd.resources | length == 0
```

## deploy.yaml template

Wait for operator, create CRs:

```yaml
---
- name: Wait for <resource> CRD to be established
  kubernetes.core.k8s_info:
    api_version: apiextensions.k8s.io/v1
    kind: CustomResourceDefinition
    name: <crd-full-name>
  register: __r_crd
  retries: "{{ k8s_retries }}"
  delay: "{{ k8s_delay }}"
  until:
    - __r_crd.resources | length > 0
    - __r_crd.resources[0].status.conditions
      | default([])
      | selectattr('type', 'equalto', 'Established')
      | selectattr('status', 'equalto', 'True')
      | list
      | length > 0

- name: Wait for <operator> deployment to be available
  kubernetes.core.k8s_info:
    api_version: apps/v1
    kind: Deployment
    name: <deployment-name>
    namespace: <namespace>
  register: __r_deploy
  retries: "{{ k8s_retries }}"
  delay: "{{ k8s_delay }}"
  until:
    - __r_deploy.resources | length > 0
    - __r_deploy.resources[0].status.availableReplicas | default(0) | int > 0

- name: Create <custom-resource>
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: <api-version>
      kind: <Kind>
      metadata:
        name: "{{ <name>_variable }}"
        namespace: "{{ <name>_namespace }}"
      spec: <spec>
  register: __r_cr
  retries: "{{ k8s_retries }}"
  delay: "{{ k8s_delay }}"
  until: __r_cr is success

- name: Wait for <custom-resource> to be ready
  kubernetes.core.k8s_info:
    api_version: <api-version>
    kind: <Kind>
    name: "{{ <name>_variable }}"
    namespace: "{{ <name>_namespace }}"
  register: __r_cr_status
  retries: 60    # hardcoded higher values for state changes
  delay: 15      # adjust based on expected resource startup time
  until:
    - __r_cr_status.resources | length > 0
    - __r_cr_status.resources[0].status.conditions
      | default([])
      | selectattr('type', 'equalto', 'Ready')
      | selectattr('status', 'equalto', 'True')
      | list
      | length > 0

- name: Debug <resource> status
  ansible.builtin.debug:
    msg: "<Resource> {{ <name>_variable }} is ready"
```

## post-operators.yaml template

Set facts for Helm values:

```yaml
---
- name: Detect cluster ingress domain
  kubernetes.core.k8s_info:
    api_version: config.openshift.io/v1
    kind: Ingress
    name: cluster
  register: __r_ingress_config
  retries: "{{ k8s_retries }}"
  delay: "{{ k8s_delay }}"
  until: __r_ingress_config.resources | length > 0

- name: Set <name> hostname fact
  ansible.builtin.set_fact:
    <name>_hostname: "<name>.{{ __r_ingress_config.resources[0].spec.domain }}"
```

## post-validate.yaml template

Verify deployment:

```yaml
---
- name: Check <component> deployment
  kubernetes.core.k8s_info:
    api_version: apps/v1
    kind: Deployment
    namespace: "{{ <name>_namespace }}"
    label_selectors:
      - "app.kubernetes.io/name=<component>"
  register: __r_component
  retries: "{{ k8s_retries }}"
  delay: "{{ k8s_delay }}"
  until: __r_component is success

- name: Assert <component> is running
  ansible.builtin.assert:
    that:
      - __r_component.resources | length > 0
      - __r_component.resources | map(attribute='status.readyReplicas')
        | select('defined') | list | length > 0
    fail_msg: "<component> not found or not ready in {{ <name>_namespace }}"

- name: Log validation success
  ansible.builtin.debug:
    msg: "<Plugin> post-validate passed: all components running"
```

## Template conventions

**Helm values templates** go in `templates/values.yaml.j2`:

```yaml
setting:
  value: "{{ <name>Defaults.someValue }}"
  url: "{{ <name>_url }}"
```

**K8s manifest templates** go in `files/*.yaml.j2`. Apply from tasks:

```yaml
- name: Apply <resource> manifest
  kubernetes.core.k8s:
    state: present
    definition: "{{ lookup('ansible.builtin.template', plugin_dir ~ '/files/<resource>.yaml.j2') | from_yaml }}"
```

For multi-document templates (multiple YAML documents separated by `---`):

```yaml
- name: Apply <multi-resource> manifests
  kubernetes.core.k8s:
    state: present
    definition: "{{ item }}"
  loop: "{{ lookup('template', plugin_dir ~ '/templates/<resource>.yaml.j2') | from_yaml_all | list }}"
  register: __r_manifests
  retries: "{{ k8s_retries }}"
  delay: "{{ k8s_delay }}"
  until: __r_manifests is success
```

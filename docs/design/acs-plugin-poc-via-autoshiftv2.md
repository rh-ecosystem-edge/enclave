# Design doc: ACS plugin PoC via autoshiftv2

## Author/date

mafriedm / 2026-07-15

## Tracking JIRA

TBD

## Problem Statement

We want to evaluate [autoshiftv2](https://github.com/auto-shift/autoshiftv2) as a tool for
fleet-wide day-2 configuration of clusters managed by enclave. autoshiftv2 is a Helm+GitOps+ACM
policy framework: it ships ~50 self-contained Helm charts under
`policies/{stable,certified,community}/<feature>`, each rendering ACM
`Policy`/`ConfigurationPolicy`/`OperatorPolicy`/`Placement`/`PlacementBinding` objects that ACM's
governance-policy framework enforces on whichever clusters match, selected via labels
(`autoshift.io/<feature>: 'true'`) on ACM `ManagedCluster`/`ManagedClusterSet` objects.

Before committing to a broader integration, we want a **small, focused PoC**: reuse one
autoshiftv2 policy chart as the implementation behind a normal enclave plugin, proving out the
pattern end to end. **autoshiftv2 is the tool, not the plugin** — the plugin is `acs`
(Advanced Cluster Security), which happens to use autoshiftv2's vendored `advanced-cluster-security`
chart the same way another plugin might use any other upstream Helm chart.

The explicit goal (per direction from the requester) is to use autoshiftv2 primarily for
**fleet management**: install and configure things on spoke/managed clusters via ACM policy,
not via enclave's own Ansible-driven local install path. ACS is a good first component for this
because it has a natural hub/spoke split: `Central` (the singleton management/data-plane
backend) runs once, and `SecuredCluster` (the lightweight per-cluster agent: sensor, admission
controller, collector) runs on every cluster that should be secured — including the hub itself
(self-monitoring) and any number of spokes.

## Background: why autoshiftv2 fits enclave's plugin model well

- Enclave's core already installs both of autoshiftv2's hard prerequisites: ACM 2.15.3
  (`defaults/operators.yaml:18`) and OpenShift GitOps 1.19.2 (`defaults/operators.yaml:33`).
  No need to install autoshiftv2's own bootstrap charts
  (`openshift-gitops/`, `advanced-cluster-management/` from the autoshiftv2 repo).
- For one fixed component, autoshiftv2's ArgoCD `ApplicationSet` git-discovery layer (the
  `autoshift/` chart, which auto-discovers all ~50 policy folders from a git repo and
  continuously syncs them) is unneeded complexity. Deploying the vendored ACS chart directly
  via enclave's existing `plugin.helm` mechanism keeps the same ACM Policy self-healing
  behavior — that property comes from ACM's policy controller, not from ArgoCD — with far
  fewer moving parts. Revisit GitOps/ApplicationSet mode if/when we manage many autoshiftv2
  components at once.
- ACS's operator install is expressed **inside the vendored chart** as an ACM `OperatorPolicy`
  (`policy-acs-operator-install.yaml`), not a plain OLM `Subscription` created by Ansible.
  Enclave already has a purpose-built mechanism for exactly this split between "make the
  operator catalog available" and "actually install the operator", described below.

## Goals

- Prove that an enclave plugin can reuse an autoshiftv2 policy chart as its implementation,
  referencing it remotely (no vendoring into the enclave repo).
- Install ACS `Central` on the enclave-managed hub cluster and `SecuredCluster` on any cluster
  (hub included) opted in via a single ACM label — all installed and enforced via ACM policy.
- Keep enclave's own responsibility limited to what nothing else already does at the right
  scope: mirroring the operator's images/catalog for disconnected environments, and making
  that catalog available on opted-in clusters.

## Non-goals (explicitly out of scope for this PoC)

- ArgoCD `ApplicationSet`/GitOps auto-discovery of policies.
- Any autoshiftv2 component beyond ACS.
- Actually provisioning or labeling real spoke clusters — the mechanism is wired up generically,
  but exercising it needs a live fleet, which isn't available while designing this.
- Full end-to-end functional test against a live disconnected cluster (ACM Policy reaching
  `Compliant`, Central/SecuredCluster actually coming up) — deferred until a test
  cluster/fleet is available.

## Proposal

New addon plugin: `plugins/acs/`

```
plugins/acs/
  plugin.yaml
  templates/values.yaml.j2
  tasks/deploy.yaml
```

### Reference the chart remotely (no vendoring)

autoshiftv2 publishes each policy chart to OCI at release time
(`quay.io/autoshift/policies/<name>`, per its `make release` flow). Rather than copying the
chart into the enclave repo, the plugin's `helm:` entry points straight at the published OCI
artifact — the same pattern the existing `osac` plugin already uses for
`oci://ghcr.io/osac-project/charts/osac` (`plugins/osac/plugin.yaml`). No local `charts/`
directory, no template edits, no vendoring/sync step: enclave pulls
`oci://quay.io/autoshift/policies/advanced-cluster-security` at deploy time, pinned to
`0.0.4` — the latest published release tag as of this writing.

### `plugin.yaml`

```yaml
name: acs
type: addon
order: 130

catalog: redhat
operators:
  - name: rhacs-operator
    version: 4.11.1
    channel: stable
    namespace: rhacs-operator
    global: true

installOperators: false        # no Ansible-driven OLM install on the management cluster
installOperatorsFleet: false   # push CatalogSource only; the vendored OperatorPolicy installs the operator via ACM policy
clusterSelector:
  matchLabels:
    autoshift.io/acs: "true"

helm:
  - release: acs
    namespace: open-cluster-policies
    chart: "oci://quay.io/autoshift/policies/advanced-cluster-security"
    version: "0.0.4"
    valuesTemplate: templates/values.yaml.j2
    createNamespace: true

registries:
  - location: "registry.redhat.io/advanced-cluster-security"
    mirror: "advanced-cluster-security"
```

`registries` covers RHACS's operand images (Central, Scanner, Sensor, Collector, Admission
Controller), which are published separately from the operator bundle/catalog — this feeds
MCE's custom-registries patch so fleet/spoke clusters can also resolve them.

#### Why `installOperators: false` + `clusterSelector` + `installOperatorsFleet: false`

Confirmed by reading `playbooks/tasks/configure_operators_fleet.yaml` and
`playbooks/tasks/deploy_plugin.yaml`:

- `installOperators: false` skips enclave's local/hub Ansible-driven `Subscription` creation
  entirely (`deploy_plugin.yaml`'s "Install plugin operators" step is gated on
  `plugin.installOperators | default(true)`).
- `clusterSelector: {...}` — independent of `installOperators` — triggers
  `configure_operators_fleet.yaml`, which always creates an ACM `Policy`
  (`{{ plugin.name }}-catalogsource`, i.e. `acs-catalogsource`) that enforces a `CatalogSource`
  named exactly `{{ catalog_mirror }}` (`redhat-operator-index-acs`) in
  `openshift-marketplace` on every `ManagedCluster` matching the selector, with `spec.image`
  pointing at the hub's internal Quay mirror route.
- `installOperatorsFleet: false` skips the *second* ACM policy that would otherwise also push
  a `Namespace`/`OperatorGroup`/`Subscription` to matched clusters — leaving the actual
  operator install to autoshiftv2's own `OperatorPolicy` in the vendored chart.
- Mirroring itself (the oc-mirror run that populates the internal Quay with `rhacs-operator`'s
  package/images) is **not** gated by `installOperators`, so this still happens normally.

Net effect: enclave's job is just "get the mirrored operator catalog onto every cluster that
opts in"; the vendored autoshiftv2 policy fully owns installing/configuring the operator and
the ACS custom resources. This is the same pattern already used by `odf`/`lvms`
(`installOperatorsFleet: false`, broadcast catalog to spokes, manual/external operator
install), taken one step further by letting an external policy (the vendored chart, instead of
a human) own the install step too.

### `templates/values.yaml.j2`

```jinja
policy_namespace: open-cluster-policies
hubClusterSets:
  hub: {}
managedClusterSets:
  managed: {}
acs:
  source: >-
    {{ 'redhat-operators' if not (disconnected | default(true) | bool) else catalog_mirror }}
```

`catalog_mirror` (`redhat-operator-index-acs`) is already a global fact set by
`deploy_plugin.yaml` before the Helm deploy step runs, and is exactly the `CatalogSource` name
the fleet policy enforces — so the vendored chart's `OperatorPolicy` and enclave's fleet
`CatalogSource` policy agree on the source name with zero extra plumbing. All other `acs.*`
values are left at upstream chart defaults.

### Hub vs. spoke resource gating in the vendored chart

- `Central`, the hub's own `SecuredCluster`, and the init-bundle job are gated on a non-empty
  `.Values.hubClusterSets` map. Their `Placement.spec.clusterSets` names an ACM
  `ManagedClusterSet`, further filtered by an `autoshift.io/acs: 'true'` `ManagedCluster`
  label predicate baked into the chart's own templates.
- The spoke-facing `SecuredCluster` (`policy-acs-secured-cluster.yaml`) is the mirror image:
  gated on `.Values.managedClusterSets`, same label predicate, and depends on a
  `policy-acs-sync-bundle` policy that relays the Central-issued init-bundle down to spokes.
- Reusing autoshiftv2's own `autoshift.io/acs: 'true'` label as the plugin's `clusterSelector`
  match key means **one label** on a `ManagedCluster` simultaneously (a) makes it a target of
  enclave's fleet `CatalogSource` push, and (b) makes it a target of the vendored chart's own
  `OperatorPolicy`/`SecuredCluster` policies. No bespoke enclave-specific label is needed.

### Do we need any tasks at all?

Only one, and only for plumbing autoshiftv2 itself doesn't provide at the right scope.

It's named `tasks/deploy.yaml`, **not** `tasks/post-operators.yaml`. `post-operators.yaml` is
enclave's lifecycle hook for "runs right after *this plugin's own* operator install step" (see
`deploy_plugin.yaml`'s "Run post-operators" step) — misleading here, since
`installOperators: false` means no local/hub Ansible-driven operator install ever runs for
this plugin. `tasks/deploy.yaml` (the generic "CR creation / supporting logic around the
Helm-deployed chart" hook, documented in `docs/PLUGIN_ARCHITECTURE.md`) is the correct fit,
and runs after the Helm chart deploy — which is fine functionally, since ACM Policy/Placement
reconciliation is eventually consistent: the vendored chart's Placements will simply show "no
matching clusters" until this task's `ManagedClusterSet`/label setup lands, then pick them up
on the next reconcile.

`tasks/deploy.yaml` does the minimum needed for the vendored chart's Placements to have
something to match:

- Ensure namespace `open-cluster-policies` exists.
- Create `ManagedClusterSet` `hub` and `ManagedClusterSet` `managed`
  (`cluster.open-cluster-management.io/v1beta2`, `selectorType: ExclusiveClusterSetLabel`) and
  a `ManagedClusterSetBinding` for each in `open-cluster-policies`.
- Patch `ManagedCluster/local-cluster` with labels `autoshift.io/acs: "true"` and
  `cluster.open-cluster-management.io/clusterset: hub`.

Actual spoke/fleet clusters are **not** created or labeled by this plugin — enabling ACS on a
spoke going forward is just: label that `ManagedCluster` with `autoshift.io/acs: "true"` and
`cluster.open-cluster-management.io/clusterset: managed`. No plugin change needed. That's the
fleet-management story this PoC demonstrates, even without live spoke clusters to test against
during design.

`tasks/pre-validate.yaml`/`tasks/post-validate.yaml` are dropped for this PoC — ACM's own
Policy compliance reporting (`oc get policy -n open-cluster-policies`, or the ACM console) is
the natural place to check post-deploy status, and duplicating that as Ansible assertions
doesn't add much value here. Cheap to add back later if we want CI-time structural checks.

## Alternatives considered

**Reusing autoshiftv2's own `advanced-cluster-management`/`cluster-labels` charts instead of a
plain `tasks/deploy.yaml` task**, for the `ManagedClusterSet`/`ManagedClusterSetBinding` and
local-cluster labeling:

- `policies/stable/advanced-cluster-management` does render `ManagedClusterSet`/
  `ManagedClusterSetBinding`, but that's a small fraction of what it does — it also installs
  and configures ACM itself (operator install, `MultiClusterHub`, observability, provisioning,
  search storage, addon tuning), which would directly conflict with enclave's own
  already-configured ACM core install.
- `policies/stable/cluster-labels` handles cluster labeling, but it's a fleet-wide
  label-reconciliation engine using `mustonlyhave` semantics — it takes over **all**
  `autoshift.io/*` labels on **every** `ManagedCluster` in the fleet, driven by a separate
  `ConfigMap`-based config system (`autoshift.io/cluster-labels` ConfigMaps, itself produced
  by the `cluster-config-maps` policy). Adopting it just to set two labels on one cluster is
  disproportionate blast radius for a PoC.
- Conclusion: a small, obviously-scoped Ansible task is the better trade-off here than pulling
  in either of those charts.

**Vendoring the ACS chart into the enclave repo** instead of referencing it via OCI: rejected
in favor of the `osac` plugin's existing remote-OCI-chart precedent — avoids a sync/drift
problem between the vendored copy and upstream, at the cost of a runtime dependency on
`quay.io/autoshift` availability (same trade-off `osac` already accepts for `ghcr.io`).

**Full ArgoCD `ApplicationSet`/GitOps mode** (autoshiftv2's native deployment path): deferred.
Adds a git-repo dependency (or OCI policy-list mode) and continuous ArgoCD sync on top of what
a single-component PoC needs; revisit once/if enclave manages several autoshiftv2 components
and the dynamic-discovery benefit outweighs the added moving parts.

## Open questions / follow-ups

- `rhacs-operator` pinned to `4.11.1` and the autoshiftv2 `advanced-cluster-security` chart
  pinned to `0.0.4` — both confirmed as latest as of this writing.
- Live-cluster verification (ACM Policy reaching `Compliant`, Central/SecuredCluster actually
  running, fleet `CatalogSource` landing on a real spoke) requires a test cluster/fleet not
  available during design — to be done as a follow-up.
- Decide whether/how to expose user-facing configuration (e.g. `config/plugins/acs.yaml`) once
  the PoC proves out — no config surface is planned for the initial version.

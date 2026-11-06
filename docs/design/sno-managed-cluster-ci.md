# Design doc: SNO managed cluster CI testing with OpenShift AI

## Author/date

eurijon / 2026-11-04

## Tracking JIRA

[OSAC-6354](https://redhat.atlassian.net/browse/OSAC-6354)

## Problem Statement

Enclave CI deploys the management (hub) cluster and stops there. Everything Enclave delivers to
*managed* clusters through ACM is never exercised, because CI never has a managed cluster:

1. **The openshift-ai plugin only acts on managed clusters.** `plugins/openshift-ai` installs ACM
   policies on the hub (`model-catalogsources`, `model-prerequisites`, `model-operators-crd-ready`,
   `model-configuration`) bound to a PlacementRule that selects clusters labeled
   `sovcloud.open-cluster-management.io/openshift-ai=true`. No CI job deploys the plugin, and
   without a labeled managed cluster it would only create the policies: the operators and the
   `default-dsc` DataScienceCluster are never installed anywhere. Issues in this plugin were only
   found outside CI.
1. **The update service policy only acts on managed clusters.** The `update-service` policy
   (`operators/cincinnati-operator/tasks.yaml`) sets `ClusterVersion.spec.upstream` on managed
   clusters to the hub OpenShift Update Service graph. Its PlacementRule excludes `local-cluster`,
   so CI never checks that a managed cluster is pointed at the hub for disconnected updates.
1. **Host discovery is not covered.** The `node-management` addon plugin
   ([#1035](https://github.com/rh-ecosystem-edge/enclave/pull/1035)) turns `discovery_hosts` into
   BareMetalHosts and Agents, but CI deploys it with an empty list.

## Goals

- A disconnected CI job that adds an **SNO managed cluster** next to the hub, on the same runner:
  an additional VM is discovered by Enclave, installed as SNO through assisted-service, and
  imported into ACM.
- Verify what Enclave delivers to managed clusters:
  - the SNO is an Available ManagedCluster and is healthy;
  - the update service policy patched the SNO's `ClusterVersion.spec.upstream` to the hub;
  - once labeled, OpenShift AI is deployed on the SNO by the openshift-ai plugin policies.
- Keep `ci/infrastructure/vm_infra.py` generic: it creates an additional SNO VM and its network
  plumbing, with no knowledge of managed clusters or ACM.
- Keep the job's wall-clock time down by running independent work in parallel.
- Actionable diagnostics on failure and no leaked resources.

## Non-objectives

- **Managed cluster lifecycle in Enclave.** Enclave provides the platform (ACM, MCE/Hive,
  assisted-service, node-management discovery) and leaves cluster lifecycle to ACM. Installing and
  importing the SNO is a test-harness action that lives in CI (`ci/`, `.github/`), never in
  `playbooks/`.
- **Connected mode.** The assisted-service `AgentServiceConfig` is only configured on disconnected
  hubs, so there is no discovery or agent install path on connected hubs. The job is
  disconnected-only.
- **Multi-node managed clusters, GPUs, several managed clusters.** One SNO is enough to exercise
  the fleet policies. The design does not prevent more VMs later.
- **Reinstalling a managed cluster on the same host.** Each CI run creates fresh VMs.

## Proposal

### Topology

Everything runs on one runner host, as libvirt VMs created by `vm_infra.py`:

```
runner host (enclave-large-odf-aws class, 256 GiB)
├── Landing Zone VM
├── master-0..2         hub cluster (ACM, MCE, assisted-service, Quay mirror, OSUS)
└── SNO VM              discovered by the hub, installed as SNO, imported into ACM
```

The SNO VM defaults to 16 vCPU / 32 GiB, a 200 GiB primary disk and a 200 GiB extra disk (storage
for the SNO, e.g. LVMS).

### Additional SNO VM in `vm_infra.py`

`vm_infra.py` remains the single source of truth for the CI environment
([ci/docs/vm-infra.md](../../ci/docs/vm-infra.md)). It creates one additional VM when asked and
emits its facts in `cluster-env.sh`; it does not know what the VM is used for.

- **Input:** `ENCLAVE_ADDITIONAL_SNO_VM` (`true`/`false`, default `false`). Sizing overrides:
  `SNO_VM_MEMORY`, `SNO_VM_VCPU`, `SNO_VM_DISK`, `SNO_VM_EXTRA_DISK`.
- **VM:** `{cluster}_sno`, defined like a master (empty CD-ROM, CD-ROM-first boot, exposed by
  sushy-tools under its libvirt UUID), with a static lease in the cluster network outside the
  master range.
- **Networking:** the same NICs as a master, in both modes: L2 only on the BMC bridge
  (`{cluster}-p`) and the cluster bridge (`{cluster}-e`). `vm_infra.py` builds a different network
  architecture per deployment mode, and the job relies on the disconnected one: the cluster bridge
  is isolated, so the SNO has no internet access and pulls everything (release, operators,
  klusterlet) from the hub Quay mirror, as a real air-gapped managed cluster would. In connected
  mode the cluster bridge is NATed; the VM is still created the same way to keep `vm_infra.py`
  generic, but the job does not run connected (see *Non-objectives*).
- **DNS:** SNO records for the cluster `<cluster>-sno`, all pointing at the VM: `api`, `api-int`
  and `*.apps` under `<cluster>-sno.<base_domain>`. These are VM-level DNS records, which
  `vm_infra.py` must own: downstream scripts never create DNS.
- **`cluster-env.sh`:** `ENCLAVE_SNO_VM_ENABLED`, `ENCLAVE_SNO_VM_NAME`,
  `ENCLAVE_SNO_VM_CLUSTER_NAME`, `ENCLAVE_SNO_VM_CLUSTER_MAC`, `ENCLAVE_SNO_VM_CLUSTER_IP`,
  `ENCLAVE_SNO_VM_UUID`.

### Discovery of the SNO VM

Discovery is done by the `node-management` addon plugin, which the e2e workflow deploys after the
cluster install (`make -f Makefile.ci deploy-plugin PLUGIN=node-management`).
`ci/infrastructure/generate_enclave_vars.sh` adds the VM to `nodeManagementConfig.discovery_hosts`
in `config/plugins/node-management.yaml` when `ENCLAVE_SNO_VM_ENABLED=true`, the same way it builds
`agent_hosts` for masters: MAC and IP of the cluster NIC, `redfish` set to the sushy endpoint,
`bmcSystemId` set to the libvirt UUID, `rootDisk: /dev/vda`. The plugin then creates the
BareMetalHost, boots the discovery ISO through sushy-tools and waits until the Agent registers,
unbound, in the `infraenv` namespace. This design depends on the plugin being merged.

### SNO install and ACM import

A CI script, `ci/deployment/deploy_sno.sh` (`make -f Makefile.ci deploy-sno`), runs on the hub
through the Landing Zone once the node-management plugin has discovered the VM:

1. Picks the mirror-backed ClusterImageSet (`openshift-v<version>-disconnected`).
1. Creates a namespace named after the SNO cluster and copies `infraenv/pullsecret` into it.
1. Applies, in that namespace, a ClusterDeployment and an AgentClusterInstall (SNO: one control
   plane agent, machine network = the cluster network), and the cluster-scoped ManagedCluster plus
   a KlusterletAddonConfig.
1. Binds the discovered Agent, which stays in `infraenv`, to the ClusterDeployment in the SNO
   namespace (`spec.clusterDeploymentName.namespace`), approves it and sets its role to `master`.
1. Waits for the AgentClusterInstall to report `Completed`, extracts the admin kubeconfig, then
   waits for the ManagedCluster to be Joined and Available.

ACM's import controller imports a Hive-provisioned cluster when the ClusterDeployment has the same
name as the ManagedCluster and lives in a namespace of that name. With that layout the SNO is
imported automatically using Hive's admin kubeconfig: no `auto-import-secret` is needed, and ACM
owns the cluster's full lifecycle. The klusterlet and add-on images come from the hub Quay mirror.

### Parallel openshift-ai deployment and SNO install

The openshift-ai plugin is deployed as a day-2 plugin (`make -f Makefile.ci deploy-plugin
PLUGIN=openshift-ai`): the deploy-plugin playbook mirrors its content into the hub Quay and creates
its policies on the hub. Nothing happens on a cluster until one is labeled, so it is independent of
the SNO install, which takes about 45 minutes. Running them one after the other wastes the shorter
of the two.

Both must run on the runner that owns the VMs and `cluster-env.sh`, so they cannot be separate
GitHub jobs. One workflow step calls a small generic helper, `ci/utils/run_parallel.sh`, with the
two make targets as named commands. The helper starts them as background processes, each logging to
its own file (lines are also streamed to the step output with a task prefix), waits for both, prints
both logs and fails if either failed. It reports which task failed in the step summary, so the
failed-step reporting stays accurate, and publishes each task's result as a step output
(`sno=success|failure`, `openshift-ai=success|failure`), so later steps can depend on the task they
need instead of on the step as a whole.

The helper waits for both tasks instead of failing fast: if one fails early, the other runs to
completion. Killing it would leave the SNO half-installed or the plugin half-deployed and make the
diagnostics incomplete, while the job fails either way. Each task keeps its own timeouts, with the
job timeout as the backstop; a cancelled job stops both.

### Verification

The checks run in this order, each as its own step. GitHub Actions skips the remaining steps after
a failure by default, so every check runs with `!cancelled()` and is gated on the result of the
task it depends on, not on whether earlier steps failed:

- SNO checks and the label: the SNO install succeeded — `steps.deploy_sno.outcome == 'success'`
  while the SNO is deployed by its own step, then `steps.<parallel-step>.outputs.sno == 'success'`
  once openshift-ai runs alongside it. A failed openshift-ai deployment does not hide the SNO
  results.
- OpenShift AI check: both tasks succeeded and the label step succeeded.

So each check reports even when an earlier one failed, and none runs against a task that failed.
Any failed step, including the parallel step when either task failed, fails the job, and the
failed-step summary names the first one:

1. **SNO health** (`ci/verification/verify_sno.sh`, `make -f Makefile.ci verify-sno`): with the SNO
   kubeconfig, ClusterVersion Available, all nodes Ready, no cluster operator unavailable or
   degraded; on the hub, the ManagedCluster is Joined and Available.
1. **Update service patch** (`ci/verification/verify_sno_update_service.sh`,
   `make -f Makefile.ci verify-sno-update-service`): on the hub, the replicated policy
   `<sno-namespace>/openshift-acm-policies.update-service` is Compliant; on the SNO,
   `clusterversion/version` has `spec.upstream` equal to
   `https://service-route-openshift-update-service.apps.<hub>.<base_domain>/api/upgrades_info/v1/graph`.
   The `RetrievedUpdates` condition is reported for information only, since fetching the graph also
   depends on the SNO trusting the hub ingress certificate.
1. **Label the SNO:** the ManagedCluster gets
   `sovcloud.open-cluster-management.io/openshift-ai=true`, so the openshift-ai PlacementRule
   selects it. Labeling after the update service check keeps the two independent.
1. **OpenShift AI** (`ci/verification/verify_sno_openshift_ai.sh`): the four `model-*` replicated
   policies are Compliant for the SNO, and on the SNO the operators are installed and
   DataScienceCluster `default-dsc` is Ready with KServe `Managed`.

### CI workflow

The steps live in the reusable `.github/workflows/e2e-deployment.yml`, gated on a new
`additional-sno-vm` input that sets `ENCLAVE_ADDITIONAL_SNO_VM=true`. The input also selects the
256 GiB `enclave-large-odf-aws` runner, as ODF does today: the hub plus the SNO do not fit the
default 128 GiB runner. openshift-ai is deployed only by the parallel step and is not added to
`ENABLED_PLUGINS`, which would deploy it again, sequentially, with the other post-install plugins.
Callers that do not set the input are unchanged. After the bootstrap phases:

| Step | What |
|---|---|
| Deploy node-management plugin | existing step; discovers the SNO VM as an unbound Agent |
| Deploy openshift-ai ∥ deploy SNO | `deploy-plugin PLUGIN=openshift-ai` and `deploy-sno` in parallel |
| Verify SNO | SNO health + ManagedCluster Joined/Available |
| Verify update service patch | policy Compliant, `ClusterVersion.spec.upstream` points to the hub |
| Label SNO for openshift-ai | ManagedCluster label |
| Verify OpenShift AI on the SNO | `model-*` policies Compliant, `default-dsc` Ready |

**Where it runs.** Three triggers enable `additional-sno-vm`:

- **PRs, by path filter.** `.github/actions/check-e2e-needed` already switches a disconnected PR
  run to `storage-plugin=odf` when `plugins/odf/**` changes. The same mechanism adds a filter on the
  files this job tests: `plugins/openshift-ai/**`, the SNO CI scripts (`deploy_sno.sh`,
  `verify_sno*.sh`), the VM and discovery generation (`vm_infra.py`, `generate_enclave_vars.sh`),
  the discovery itself (`plugins/node-management/**`, plus `playbooks/07-configure-discovery.yaml`
  while discovery still runs there) and the update service policy it checks
  (`operators/cincinnati-operator/**`). This job is the only one that discovers a real host or
  exercises the update service policy on a managed cluster, so those changes are tested here.
  When it matches, the disconnected PR run sets `additional-sno-vm=true`. It keeps the storage
  plugin the rest of the decision picked: `lvms`, or `odf` when ODF files changed too.
- **The weekly ODF run** (below), for regular coverage independent of PRs.
- **Manual dispatch**, for an on-demand run.

**The weekly ODF run.** `.github/workflows/e2e-odf-schedule.yml` already dispatches
`e2e-disconnected.yml` once a week with `storage-plugin=odf`, on the 256 GiB
`enclave-large-odf-aws` runner. The SNO needs a disconnected hub and that runner class, so the
weekly ODF run passes `additional-sno-vm=true` instead of adding a new scheduled job:

- **No new runner capacity.** A weekly run is already paid for on the runner class the SNO needs.
- **Memory fits.** ODF hub VMs take about 168 GiB (3 × 48 GiB masters, 24 GiB Landing Zone); the
  SNO adds 32 GiB, for about 200 GiB of 256 GiB.
- **Time fits.** The ODF run takes about 3 hours; the SNO adds about an hour, within the 6 hour
  timeout of a dispatched disconnected run.
- **Disk needs measuring.** The ODF Landing Zone already holds the mirror and the Ceph loopback OSD
  files on the runner's scratch volume; the SNO disks are thin-provisioned, but the first runs must
  confirm the headroom. If it is too tight, the SNO sizing or the scratch volume size is adjusted.
- **Coupling.** An ODF failure before the SNO steps hides the SNO result for that week, and the
  other way round. The failed-step summary tells them apart, and PR-triggered runs use `lvms`
  unless ODF files changed, so most SNO changes are tested without ODF.

### Diagnostics and cleanup

The SNO path fails in ways the step log does not explain (an Agent validation that never passes,
an import that never joins, a policy that stays NonCompliant on the managed cluster), so the job
collects rich debugging data. `ci/verification/collect_ci_artifacts.sh` gets an SNO section, fed
at both of its existing levels, under `managed_sno/` in the diagnostics bundle.

**Every run** (`deployment` level), so a green run can be compared with a red one:

- the per-task logs of the parallel step and its task durations (to track the time saved), and the
  `deploy_sno.sh` / `verify_sno*.sh` logs;
- hub: AgentClusterInstall, ClusterDeployment, Agent, BareMetalHost, InfraEnv and ManagedCluster as
  YAML (status included), ManagedClusterAddons, and the compliance status of every policy
  replicated to the SNO namespace;
- SNO: ClusterVersion and cluster operators as YAML, nodes, and pod status in all namespaces.

**On failure** (`full` level), additionally:

- hub: the assisted-service install events and the install logs bundle (downloaded from the
  AgentClusterInstall `debugInfo` events/logs URLs), the Agent `validationsInfo`, events in
  `infraenv` and the SNO namespace, and logs of assisted-service, the BareMetal Operator / Ironic,
  the import controller and the policy propagator;
- SNO: describe and logs (including `--previous`) of every pod that is not Running/Completed,
  events, the klusterlet and add-on pod logs, the config-policy-controller logs, and for OpenShift
  AI the DataScienceCluster, DSCInitialization, Subscriptions, InstallPlans and CSVs.

The in-flight resource sampler (`ci/verification/sample_resources.sh`) also records the SNO VM and,
once installed, the SNO API latency, so transient stalls during the install are visible
afterwards.

The SNO's credentials are not collected: its admin kubeconfig stays on the Landing Zone and is
only used there, and no Secret values are dumped. One SNO-related credential does reach
`managed_sno/` indirectly: the discovery ISO download URL, in the InfraEnv status, events and
assisted-service logs, carries a token for an ISO that embeds the local mirror pull secret. That
is accepted: those are CI-default credentials of an ephemeral registry that is not reachable
outside the runner. The hub's artifacts are collected and redacted as today; this design does not
change them.

`make -f Makefile.ci clean` destroys every VM of the cluster, the SNO VM included, so cleanup stays
unconditional and nothing new can leak.

## Alternatives considered

**Use the hub (`local-cluster`) as the managed cluster.** Cheaper, but it does not test the fleet
path: the update service PlacementRule excludes `local-cluster`, and the hub is not a realistic
target for the openshift-ai policies.

**CI creates its own InfraEnv and BareMetalHost.** Simpler to control, but it skips Enclave's
discovery path (the node-management plugin's `discovery_hosts` to BareMetalHost to Agent), which is
one of the things worth testing.

**Import with an `auto-import-secret`, ClusterDeployment kept in `infraenv`.** Also works, but ACM
treats the cluster as imported rather than created, and CI has to hand the SNO admin kubeconfig to
ACM.

**A managed-cluster-aware VM in `vm_infra.py`.** Ties the environment tool to one use case; a
generic additional SNO VM serves this and any future test that needs an SNO.

**A new dedicated scheduled job.** Clearer separation from ODF, but it pays for a second weekly run
on the large runner class for setup that the ODF run already does.

**Scrub the SNO artifacts before upload.** Would remove the discovery ISO download token, but the
SNO artifacts carry no long-lived secret (the SNO path only uses the local mirror pull secret, and
its kubeconfig is not collected), so the added step is not worth it for this job.

**Separate GitHub jobs for the parallel work.** Not possible: jobs run on different runners, and
the VMs only exist on one.

**Run openshift-ai and the SNO install sequentially.** Simpler, but adds the plugin deployment time
to an already long job.

## Milestones

1. **SNO managed cluster CI job**
   ([OSAC-7265](https://redhat.atlassian.net/browse/OSAC-7265)). Generic additional SNO VM in
   `vm_infra.py` (`ENCLAVE_ADDITIONAL_SNO_VM`), discovery through the node-management plugin's
   `discovery_hosts`, SNO install and ACM import after Enclave finishes, SNO health and update
   service checks, diagnostics. The `additional-sno-vm` input runs it on manual dispatch, in the
   weekly ODF run, and on PRs that touch the SNO CI scripts, `vm_infra.py`,
   `generate_enclave_vars.sh`, discovery (node-management plugin, phase 07) or the update service
   policy (`operators/cincinnati-operator/**`).
1. **OpenShift AI on the SNO**
   ([OSAC-7266](https://redhat.atlassian.net/browse/OSAC-7266)). openshift-ai plugin deployment in
   parallel with the SNO install, update service check, labeling, OpenShift AI verification,
   diagnostics. The PR path filter also covers `plugins/openshift-ai/**`.

# CI Troubleshooting Guide

How to debug failed GitHub Actions runs for this repository from the command line, and
how to read the diagnostics bundle that every e2e run uploads.

Prefer the [`gh`](https://cli.github.com/) CLI over the web UI — it is headless,
scriptable, and gives an agent the raw text it needs instead of screenshots.

## 1. Find and inspect the failing run

```bash
# Checks for a PR, with pass/fail state and the run URL for each
gh pr checks <pr>

# Recent runs for a workflow (find a run id)
gh run list --workflow "E2E Deployment" --limit 10

# Overview of a run: which jobs failed
gh run view <run-id>

# Jump straight to the log lines of the failed steps (usually where you start)
gh run view <run-id> --log-failed

# Full log for one job (get <job-id> from `gh run view <run-id>`)
gh run view --job <job-id> --log

# Follow a run until it settles
gh pr checks <pr> --watch --interval 60
```

The step log tells you *which task failed* (for e2e, often an Ansible task such as
`Wait for quay-app Deployment rolling update to complete`). It rarely tells you *why*.
For that, download the diagnostics bundle.

## 2. Download the diagnostics bundle

Every e2e job runs `scripts/verification/collect_ci_artifacts.sh full artifacts` and
uploads the result as an artifact (7-day retention):

- `e2e-connected-<cluster>-<run_id>`
- `e2e-disconnected-<cluster>-<run_id>`

```bash
# List artifacts attached to a run
gh api repos/{owner}/{repo}/actions/runs/<run-id>/artifacts \
  --jq '.artifacts[] | "\(.name)\t\(.size_in_bytes)"'

# Download one artifact by name into ./artifacts
gh run download <run-id> -n e2e-disconnected-<cluster>-<run_id> -D ./artifacts
```

Always pull the bundle before reasoning about a deploy failure. The single most common
mistake is reading only the step log and guessing; the bundle usually contains the exact
`Event` or pod condition that explains the failure.

## 3. Collection levels

`collect_ci_artifacts.sh <level> <output_dir>` supports increasing depth. CI uses `full`
on e2e jobs; lighter jobs use `basic`/`infra`.

| Level | Adds |
| --- | --- |
| `basic` | System info and process list of the runner. |
| `infra` | Runner performance/logs, libvirt VMs/networks/storage, host network, BMC. |
| `deployment` | Landing Zone: system info, DNS, cloud-init, deployment + pipeline logs, services, mirror registry, redacted enclave config files, redacted rendered plugin Helm values. |
| `full` | Cluster diagnostics via the kubeconfig (see the map below). Runs on failure. |

## 4. Artifact map

```
artifacts/
├── system/                     Runner host: system-info, processes, top, iostat, vmstat, journal
├── libvirt/                    VM list/details/XML, networks, storage pools
├── network/                    Host interfaces, bridges, firewall, DNS resolution
├── bmc/                        sushy-tools / Redfish BMC status
├── landing-zone/               Landing Zone (LZ) VM
│   ├── .openshift_install.log            OpenShift/agent installer log
│   ├── openshift_install_agent.log
│   ├── serial-console.log / qemu-domain.log   (SSH-less fallback only)
│   ├── config/                 Redacted enclave config: every *.yaml/*.yml under config/ (recurses into config/plugins/), examples excluded
│   ├── helm-values/            Redacted rendered plugin Helm values (helm-values-<plugin>-<release>.yaml)
│   └── pipeline-logs/          oc-mirror progress, helm logs, mirroring_errors_*
└── cluster/                    Only present when the cluster came up (level: full)
    ├── cluster-status.txt      clusterversion, nodes, ClusterOperators, MCPs, degraded COs
    ├── events.txt              All events + Warning-only events (FailedScheduling, etc.)
    ├── pods.txt                All pods + Pending/Failed/not-Running selectors
    ├── <ns>_<pod>.log          Problem-pod logs (all containers, --previous, describe)
    ├── plugin-diagnostics/     Per-enabled-plugin namespace state
    └── quay-diagnostics-*/     Quay (see below)
        ├── quayregistry.yaml   QuayRegistry CR — .status.conditions is the operator's own diagnosis
        ├── overview.txt        pods, deployments, HPA, events, PVCs, secret NAMES (never values)
        ├── <pod>_describe.txt  Per-pod describe
        ├── <pod>.log           Per-pod logs, all containers, 2000-line tail (+ _previous.log)
        └── health-instance.txt /health/instance body naming the failing subsystem
```

Security note: `vars.yaml` (pull secrets, Quay admin password) is intentionally **not**
collected. Secrets appear by name only. The `config/` and `helm-values/` files are passed
through a redactor before upload — values under secret-like keys (password, secret, key,
token, credential, auth, cert) are replaced with `REDACTED`, and credentials embedded in
URLs are stripped — but treat them as best-effort: do not add raw secret dumps to the bundle.

## 5. Reading the bundle by failure signature

| Symptom in the step log | Look first at |
| --- | --- |
| `ProgressDeadlineExceeded`, rollout never completes | `cluster/events.txt` (`FailedScheduling`, `Insufficient memory/cpu`), `cluster/pods.txt` (Pending pod), the Pending pod's `*_describe.txt` |
| Quay unhealthy / crashloop | `cluster/quay-diagnostics-*/quayregistry.yaml` (`.status.conditions`), then `overview.txt`, per-pod `*.log` and `*_previous.log`, `health-instance.txt` |
| Operator/ClusterOperator degraded | `cluster/cluster-status.txt` (degraded CO list), then the operator pod logs under `cluster/` |
| PVC unbound / storage | `cluster/quay-diagnostics-*/overview.txt` (PVCs), `cluster/events.txt`, `libvirt/` storage |
| Mirroring / disconnected image pull | `landing-zone/pipeline-logs/` (`oc-mirror*`, `mirroring_errors_*`) |
| Install never reaches a cluster (no `cluster/` dir) | `landing-zone/.openshift_install.log`, `landing-zone/pipeline-logs/`, `landing-zone/serial-console.log` |
| Plugin deploys wrong values / misconfiguration (e.g. a service enabled/disabled unexpectedly, bad hostname) | `landing-zone/config/` (input config, incl. `config/plugins/<plugin>.yaml`), `landing-zone/helm-values/` (what was actually rendered and applied) |

Worked example: an LVMS Quay rollout hang (OSAC-4957) showed only
`ProgressDeadlineExceeded` in the step log. `cluster/events.txt` had
`FailedScheduling … Insufficient memory` and `cluster/pods.txt` showed the surge pod
`Pending` — the node-local RWO PV pins Quay to one node, so a rolling-update surge pod
could not fit a second memory request there. That is invisible in the step log but obvious
in the bundle.

## 6. Reproducing validation checks locally

The non-e2e checks map to Makefile targets you can run before pushing:

```bash
make -f Makefile.ci validate             # all infra checks
make -f Makefile.ci validate-shell       # shellcheck
make -f Makefile.ci validate-yaml        # yamllint
make -f Makefile.ci validate-ansible     # ansible-lint
make -f Makefile.ci validate-plugins     # plugin descriptors
make -f Makefile.ci validate-json-schema # config/defaults vs schemas
make python-unit-test                    # pytest
```

## Related docs

- [CI_WORKFLOWS.md](CI_WORKFLOWS.md) — what each workflow runs and how to trigger it
- [CI_RUNNER_SETUP.md](CI_RUNNER_SETUP.md) / [CI_RUNNER_MAINTENANCE.md](CI_RUNNER_MAINTENANCE.md) — runner host
- [ODF_CEPH_CI.md](ODF_CEPH_CI.md) — ODF/Ceph e2e specifics

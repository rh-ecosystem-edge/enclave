---
name: install-virtualized
description: >-
  Deploy or clean up an Enclave virtualized lab environment on a bare-metal host.
  Use when the user says "install enclave", "deploy enclave", "install virtualized",
  "deploy on <host>", "clean up the deployment", "tear down the cluster",
  "install plugins", "deploy plugins", "install experience", or "deploy experience".
when_to_use: >-
  Use for deploying Enclave on bare-metal using dev-scripts VMs, cleaning up
  an existing deployment, or installing day-2 addon plugins or experiences
  on a successful deployment. This skill dynamically reads the CI e2e workflow
  each time to stay in sync with the latest deployment steps.
disable-model-invocation: true
allowed-tools: Bash(ssh *) Bash(scp *) Bash(grep *) Bash(cat *) Bash(ls *) Bash(make *) Bash(gh *) Bash(git *) Read AskUserQuestion
---

# Enclave Virtualized Installation

> **Warning:** This skill is **not** a supported installation method. It is provided
> for Enclave virtualized deployments intended for development and testing purposes only.
> Use at your own risk.

Display the warning above to the user before proceeding with any installation.

## Prerequisites

Before starting, ensure the bare-metal host meets these minimum requirements:
- 128 GB RAM (64 GB minimum, but not recommended)
- 200 GB free disk (connected) / 1,200 GB (disconnected)
- 16+ CPU cores
- RHEL/CentOS with libvirt available
- Passwordless SSH access from your machine

Requirements are validated in detail during Step 7.

This skill deploys Red Hat Sovereign Enclave on a bare-metal host using dev-scripts
to create virtualized infrastructure (Landing Zone VM + OpenShift master VMs).

**This skill follows the CI e2e-deployment workflow exactly.** It reads the workflow
files dynamically each run so it adapts automatically when CI changes.

## User Input

Whenever the skill needs the user to choose between options, use the
`AskUserQuestion` tool to present the choices. This applies to all steps that
offer a selection (installation mode, deployment mode, storage plugin, resource
allocation, pull secret method, branch/PR, etc.). Group related questions into
a single `AskUserQuestion` call when they appear in the same step. **Never mark
any option as recommended** — present all choices neutrally and let the user
decide. When a step has a default, note it with "(default)" in the label, not
"(Recommended)".

$ARGUMENTS

## Before You Start: Discover the Deployment Workflow

**Every time this skill runs**, read these files from the enclave repo and extract
the complete deployment procedure dynamically. Do NOT assume any steps, env vars,
or plugin order — derive everything from these files:

1. **`.github/workflows/e2e-deployment.yml`** — find the job for the selected mode
   (connected or disconnected). Extract:
   - The `env:` block (all environment variables and their values)
   - Every `run:` step in order (each `make -f Makefile.ci <target>` call)
   - Conditional steps (`if:` clauses — e.g., steps that only run for certain storage plugins)
   - The plugin deployment order (the sequence of `deploy-plugin PLUGIN=<name>` steps)

2. **`Makefile.ci`** — understand target dependencies and the `clean` target

3. **`scripts/setup/configure_devscripts.sh`** — extract current default VM resource
   values (grep for `MASTER_MEMORY_VAL`, `MASTER_VCPU_VAL`, `LANDINGZONE_MEMORY`,
   `LANDINGZONE_VCPU`, `LANDINGZONE_DISK_VAL`, `ENCLAVE_NUM_MASTERS`, `MASTER_DISK`,
   `VM_EXTRADISKS_SIZE_VAL`)

4. **`experiences/`** — read the experience YAML files to understand plugin sets

The steps, env vars, and plugin order you extract ARE the deployment procedure.
Execute them in that exact order.

**Build the step list**: after extracting steps, assign each a sequential number.
Count the total (filtering out steps whose conditions won't be met for this
deployment). Store `TOTAL_STEPS` — you'll use it for the progress bar.

**Every step must come from the workflow file.** Foundation plugins (like lvms
or odf) are deployed by the `deploy-cluster-operators` playbook — they do NOT
have a `deploy-plugin` step in the workflow.

## Check CI Health on Main

Before proceeding, check whether the corresponding e2e job is currently passing on `main`:

```bash
gh run list --workflow=e2e-deployment.yml --branch=main --limit=5 --json databaseId,status,conclusion,displayTitle,createdAt
```

Then inspect the specific job for the selected mode using the most recent run ID:
```bash
gh run view <run-id> --json jobs --jq '.jobs[] | select(.name | test("e2e-connected|e2e-disconnected")) | {name, status, conclusion, url}'
```

If the job matching the selected mode is **failing**:
- **Warn the user clearly**: "The CI e2e-connected job is currently failing on main.
  This deployment follows the same workflow, so it may hit the same failure."
- Show the failure details (run URL, which step failed)
- Ask if they want to proceed anyway, use a specific branch, or wait

## Step 1: Ask for Bare-Metal Host

Ask the user for the SSH target (e.g., `cloud-user@edge-23`, `root@10.0.0.1`).

## Step 2: Verify Connectivity

Test passwordless SSH:
```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <host> "hostname && uname -r"
```

If it fails, stop and tell the user to set up passwordless SSH (e.g., `ssh-copy-id <host>`).

After verifying SSH, check for existing enclave deployments on the host:

```bash
ssh <host> "test -f /tmp/working_dir && echo WORKDIR_EXISTS; test -d ~/dev-scripts && echo DEVSCRIPTS_EXISTS; sudo -n virsh list --all --name 2>/dev/null | grep -v '^$' || true"
```

If enclave artifacts are found (`/tmp/working_dir` or `~/dev-scripts` exist),
a previous enclave deployment exists on this host. This skill does not support
multiple concurrent deployments on the same host. Show the user the list of
running VMs regardless, for context.

Show the user:
- The list of running VMs
- Available vs total RAM: `ssh <host> "free -m | awk '/Mem:/{printf \"Available: %d MB / Total: %d MB\", \$7, \$2}'"`
- Warn: "An existing enclave deployment was found on this host. Multiple
  deployments are not supported. You can clean it up before proceeding or
  abort."

Ask the user (via `AskUserQuestion`):
1. **Clean up and redeploy** — run `make -f Makefile.ci clean` to remove the
   existing deployment, then proceed with a fresh install
2. **Install day-2 addons on existing deployment** — skip to day-2 plugin or
   experience installation (Step 12) on the running cluster. Collect the LZ IP
   and verify cluster access before proceeding.
3. **Abort** — stop the skill

If cleanup is selected, follow the **Cleanup** section procedure before
continuing to Step 3.

If day-2 addons is selected:
1. Capture the LZ IP: `ssh <host> "cd ~/enclave && ./scripts/utils/get_landing_zone_ip.sh"`
2. Verify cluster access via double-hop SSH:
   ```bash
   ssh <host> "ssh -o StrictHostKeyChecking=no cloud-user@<LZ_IP> 'export KUBECONFIG=/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig && /home/cloud-user/sessions/1/bin/oc get clusterversion'"
   ```
3. If cluster is accessible, skip to Step 9 (day-2 addon/experience selection
   only — skip env var collection since the deployment is already configured),
   then proceed to Step 12 for installation.

## Step 3: Choose Installation Mode

Ask the user: **attended** or **unattended**?

- **Unattended** (default): run all deployment steps (Step 10) and
  plugin/experience installation (Step 12) automatically without asking for
  confirmation. If any step fails, stop and ask the user what to do.
- **Attended**: ask for confirmation before running each deployment step
  (Step 10) and each plugin installation (Step 12). Between steps, show what's
  about to run and wait for the user to approve before proceeding.

Both modes go through the same initial configuration (Steps 4-9), collecting
required values and presenting options for confirmation.

## Step 4: Clone Enclave Repository on Host

Instead of assuming the repo exists, clone it on the bare-metal host.

**Shell quoting**: always shell-quote user-provided values (branch names, paths,
pull-secret content) when interpolating into SSH commands. Use `scp` for file
transfers instead of heredocs for secret/config content.

1. **Derive the repo URL** from the local checkout:
   ```bash
   gh repo view --json url -q .url
   ```

2. **Ask the user**:
   > "Which branch or PR should I deploy?"
   > - Branch name (e.g., `main`, `feature/my-fix`)
   > - PR number (e.g., `432`) — I'll resolve the branch name
   > - Default: `main`

3. **If PR number**, resolve the branch locally:
   ```bash
   gh pr view <PR_NUMBER> --json headRefName -q .headRefName
   ```

4. **Check if repo exists on the host**:
   ```bash
   ssh <host> "test -d ~/enclave && echo exists || echo missing"
   ```
   - If exists: ask "The enclave repo already exists at ~/enclave. Remove and
     re-clone, or reuse and checkout the requested branch?"
   - If missing: clone it

5. **Clone or update**:
   ```bash
   # Clone (if missing):
   ssh <host> "git clone <repo-url> ~/enclave"
   # Or update (if reusing):
   ssh <host> "cd ~/enclave && git fetch origin && git checkout <branch> && git pull origin <branch>"
   ```

6. **Verify**:
   ```bash
   ssh <host> "cd ~/enclave && git rev-parse --abbrev-ref HEAD && git log --oneline -1"
   ```

## Step 5: Pull Secret

Ask how the user wants to provide the pull secret:

1. **Already on the server** — provide the path (e.g., `~/pull-secret.json`)
2. **Paste it** — paste the JSON content directly
3. **Local file** — provide the local path and it will be uploaded

**Option 1** (on server):
```bash
ssh <host> "cat <path> | python3 -c \"import sys,json; d=json.load(sys.stdin); assert 'registry.redhat.io' in d.get('auths',{}), 'Missing registry.redhat.io'; print('Valid')\" "
```
Then symlink or copy to a known path:
```bash
ssh <host> "cp <path> ~/.pull-secret.json && chmod 600 ~/.pull-secret.json"
```

**Option 2** (paste):
Receive the JSON content from the user. Upload it:
```bash
ssh <host> "cat > ~/.pull-secret.json << 'EOFPS'
<pasted content>
EOFPS
chmod 600 ~/.pull-secret.json"
```

**Option 3** (local file):
```bash
scp <local-path> <host>:~/.pull-secret.json
ssh <host> "chmod 600 ~/.pull-secret.json"
```

In all cases, validate the file on the server:
```bash
ssh <host> "python3 -c \"from pathlib import Path; import json; d=json.load(Path.home().joinpath('.pull-secret.json').open()); assert 'registry.redhat.io' in d.get('auths',{}); print('Pull secret valid: ' + str(len(d['auths'])) + ' registries')\" "
```

For all subsequent make targets, reference the pull secret as:
```bash
export PULL_SECRET="\$(cat ~/.pull-secret.json)"
```

## Step 6: Ask Deployment Mode

Ask the user: **connected** or **disconnected**?

- **Connected**: standard deployment with direct registry access. Estimated
  time: 20-30 minutes. Minimum host disk: ~200 GB free.
- **Disconnected** (air-gapped): deployment includes setting up a mirror
  registry on the Landing Zone and mirroring all container images locally.
  Estimated time: 45-90 minutes (includes ~30+ minutes of image mirroring).
  Minimum host disk: ~1200 GB free.

Both modes use the same sequence of make targets — the behavior difference is
driven entirely by `ENCLAVE_DEPLOYMENT_MODE`. The Ansible playbooks on the
Landing Zone handle mirror registry setup and image mirroring automatically
when `disconnected: true` is set.

## Step 7: Check Host Resources

Gather available resources:
```bash
ssh <host> "free -m | awk '/Mem:/{print \$2}'; nproc; df -BG --output=avail / /home 2>/dev/null | tail -n+2"
```

Use the defaults extracted from `configure_devscripts.sh` **and**
`scripts/infrastructure/provision_landing_zone.sh` to calculate total requirements.

**Important**: `configure_devscripts.sh` and `provision_landing_zone.sh` both define
LZ resource defaults. Always check both files and use the values from
`provision_landing_zone.sh` (the `--memory` and `--vcpus` args to `virt-install`)
as the source of truth, since that script actually creates the LZ VM.

### Disk space requirements

Disk sizing differs between connected and disconnected modes. Extract the
actual values dynamically from these files (do NOT hardcode them):

- **`scripts/setup/validate_prerequisites.sh`** — grep for `MIN_DISK_GB` to
  get the minimum disk thresholds for each deployment mode
- **`scripts/setup/configure_devscripts.sh`** — grep for `VM_EXTRADISKS_SIZE_VAL`
  to get the per-master extra disk size (differs by deployment mode)
- **`scripts/infrastructure/provision_landing_zone.sh`** — grep for `LZ_DISK_SIZE`
  to get the Landing Zone disk size (differs by deployment mode)

Compare the host's available disk against the `MIN_DISK_GB` for the selected
mode. If below the threshold, warn the user and explain that disconnected mode
needs extra space for the mirror registry and mirrored container images.

```
RAM:  ENCLAVE_NUM_MASTERS x MASTER_MEMORY_VAL + LANDINGZONE_MEMORY + 4096 (host overhead)
vCPU: ENCLAVE_NUM_MASTERS x MASTER_VCPU_VAL + LANDINGZONE_VCPU
Disk: ENCLAVE_NUM_MASTERS x (MASTER_DISK + VM_EXTRADISKS_SIZE_VAL) + LZ_DISK_SIZE
```

Disk images use qcow2 thin provisioning — actual usage is much lower than the
formula's theoretical maximum. Compare available disk against `MIN_DISK_GB`
extracted from `validate_prerequisites.sh` (do not hardcode — grep the value).

### Resource sizing logic

Calculate `available_for_vms = total_ram_mb - 4096` (host overhead).

**Hosts with ≤64 GB RAM** — **do not recommend installing Enclave**:
- Warn: "This host has ≤64 GB RAM. Enclave requires 3 master nodes and the
  platform components (ACM, Quay, Clair) need significant memory headroom.
  A minimum of 128 GB RAM is recommended for a stable deployment."
- Ask the user if they want to proceed anyway with reduced resources or stop.
- If proceeding: use the same calculation as >64 GB hosts below, with a floor
  of `MASTER_MEMORY_VAL=16384` (16 GB) and `LANDINGZONE_MEMORY=2048`. If even
  these minimums don't fit, stop — the host cannot run Enclave.

**Hosts with >64 GB RAM** — use **3 masters** (default):
- Check if the CI defaults from `configure_devscripts.sh` fit:
  `3 x MASTER_MEMORY_VAL + LANDINGZONE_MEMORY + 4096 ≤ total_ram_mb`
- If defaults fit: use them as-is
- If defaults don't fit: calculate values that do:
  - `LANDINGZONE_MEMORY=2048` (LZ uses <2 GB in practice)
  - `MASTER_MEMORY_VAL = (available_for_vms - LANDINGZONE_MEMORY) / 3`
  - `LANDINGZONE_VCPU=2`
  - `MASTER_VCPU_VAL = min(default, (host_vcpus - LANDINGZONE_VCPU) / 3)`
  - Show the adjusted values and the calculation

**Always present the suggested resource allocation to the user for confirmation.**

**Important**: The override env vars are `MASTER_MEMORY_VAL` (not `MASTER_MEMORY`).
This is what `configure_devscripts.sh` reads when the fix from PR #562 is present.
Also pass `MASTER_VCPU_VAL`, `LANDINGZONE_MEMORY`, `LANDINGZONE_VCPU` if adjusted.

## Step 8: Storage Plugin

The virtualized deployment uses **lvms** as the storage plugin. Inform the user
that lvms will be used (it is the only supported option for virtualized installs).

## Step 9: Collect Environment Variables and Day-2 Options

Set the following environment variables for the deployment. Use CI defaults
from the workflow `env:` block for non-secret values. Only ask the user for
host-specific or secret values that cannot be derived from the workflow.

**Fixed variable allowlist** (do NOT scan or expose other CI env vars):
- `ENCLAVE_CLUSTER_NAME` (default: `enclave-test`, ask user)
- `ENCLAVE_DEPLOYMENT_MODE` (set from Step 6: `connected` or `disconnected`)
- `STORAGE_PLUGIN` (set from Step 8: `lvms`)
- `ENABLED_PLUGINS` (default: same as `STORAGE_PLUGIN`, updated in Step 9 day-2 selection)
- `MASTER_MEMORY_VAL` (override if adjusted in Step 7)
- `MASTER_VCPU_VAL` (override if adjusted in Step 7)
- `LANDINGZONE_MEMORY` (override if adjusted in Step 7)
- `LANDINGZONE_VCPU` (override if adjusted in Step 7)
- `PULL_SECRET` (from Step 5: `$(cat ~/.pull-secret.json)`)
- `OPENSHIFT_CI` (CI default: `true`)
- `CLEANUP_AFTER` (CI default: `true`)
- `ENCLAVE_ENABLE_GPU_PASSTHROUGH` (CI default: `false`)
- `AAP_LICENSE_FILE` (only when OSAC plugin selected, set in day-2 config)
- `OSAC_CHART_VERSION` (only when OSAC plugin selected, optional override)

All other CI variables (`BASE_WORKING_DIR`, `LZ_OS_VARIANT`, RHSM secrets,
cloud image URLs, Slack webhooks, etc.) are CI-runner-specific and must NOT
be collected or exposed to the user.

### Day-2 Addon Plugins or Experiences (Optional)

Ask the user if they want to install day-2 addon plugins or an experience after
the base deployment completes. Use `AskUserQuestion` with these options:

1. **Install addon plugins** — select individual plugins and their install order
2. **Install an experience** — select a predefined experience bundle
3. **Skip** — no day-2 addons

Options 1 and 2 are mutually exclusive.

**Discovery**: do NOT use Bash for-loops, shell scripts, or sub-agents for
discovery. Use the Read tool directly to read each file — this avoids
shell permission prompts.

**Addon plugins**: list directories under `plugins/` with Bash(`ls`), then
use the Read tool on each `plugins/<name>/plugin.yaml`. Check if
`type: addon`. Collect the names of all addon plugins.

**Experiences**: list directories under `experiences/` with Bash(`ls`), then
use the Read tool on each `experiences/<name>/experience.yaml`. Collect
the name, description, and plugins list.

Show the full list of available addon plugins and experiences to the user,
then present the selection via `AskUserQuestion`:
- For addon plugins: use `multiSelect: true`. After selection, ask the user
  to specify the installation order. Store the ordered list — plugins will be
  installed one by one in that order after the base deployment succeeds.
- For experiences: present each with its `name`, `description`, and `plugins`
  list. Only one experience can be selected. The plugin installation order
  follows the order defined in the experience YAML.

### Plugin Configuration

After the user selects addon plugins (or an experience), check if each
selected plugin has a config file at `config/plugins/<plugin>.example.yaml`.
If found, read it and identify required fields (uncommented or marked
"Required"). Ask the user for values of required fields. Optional fields
should be shown but can be skipped.

Create the actual config file `config/plugins/<plugin>.yaml` on the LZ
(at `~/enclave/config/plugins/<plugin>.yaml`) with the user-provided values
before running the plugin deployment:
```bash
ssh <host> "ssh cloud-user@<LZ_IP> 'cat > ~/enclave/config/plugins/<plugin>.yaml << EOF
<config content>
EOF'"
```

**Special case — OSAC plugin license file**: the OSAC plugin requires an
AAP license manifest file (`osacAapLicenseFile` in `config/plugins/osac.yaml`).
When the OSAC plugin is selected (directly or as part of an experience):
1. Ask the user for the local path to the AAP license `.zip` file
2. Upload it to the LZ:
   ```bash
   scp <local-license-path> <host>:~/aap-license.zip
   ssh <host> "scp ~/aap-license.zip cloud-user@<LZ_IP>:/home/cloud-user/aap-license.zip"
   ```
3. Create `config/plugins/osac.yaml` on the LZ with the license path:
   ```bash
   ssh <host> "ssh cloud-user@<LZ_IP> 'cat > ~/enclave/config/plugins/osac.yaml << EOF
   osacAapLicenseFile: /home/cloud-user/aap-license.zip
   EOF'"
   ```

Store the selection and configuration. Installation happens in Step 12
after the base deployment succeeds.

## Step 10: Run Installation

Execute each make target extracted from the CI workflow, sequentially via SSH.
Build an env block from all collected and CI-default variables.

Always report progress via the progress bar after each step completes.

- **Attended**: before each step, show what's about to run and ask for
  confirmation. This lets the user pause, inspect, or skip steps.
- **Unattended**: run all steps automatically. On failure, ask the user:
  1. Retry the failed step
  2. Skip and continue with the next step
  3. Stop the deployment

### Progress Bar

After each step completes, display a progress bar:

```
[████████░░░░░░░░] 8/22 — deploy-cluster-prepare ✓
```

- Use 16 characters width: `█` for completed portion, `░` for remaining
- Show step number / TOTAL_STEPS and current step name
- On completion add ✓, on failure add ✗

Before the first step, show the full step list:
```
Deployment plan (22 steps):
  1. setup-working-dir
  2. environment
  ...
  22. verify-cluster
```

### Make Target to LZ Log File Mapping

Steps that run via `deploy_bootstrap_step.sh` produce a log file on the Landing
Zone at `~/enclave/deployment_bootstrap_<STEP>.log`. The mapping from make target
to bootstrap step name (and therefore log file) is:

| Make target | Bootstrap step | LZ log file |
|---|---|---|
| `deploy-cluster-setup` | `setup` | `deployment_bootstrap_setup.log` |
| `deploy-cluster-check-leftovers` | `check-leftovers` | `deployment_bootstrap_check-leftovers.log` |
| `deploy-cluster-validate` | `validate` | `deployment_bootstrap_validate.log` |
| `deploy-cluster-prepare` | `download-content` | `deployment_bootstrap_download-content.log` |
| `deploy-cluster-mirror` | `build-cache` | `deployment_bootstrap_build-cache.log` |
| `deploy-cluster-acquire-hardware` | `acquire-hardware` | `deployment_bootstrap_acquire-hardware.log` |
| `deploy-cluster-install` | `deploy` | `deployment_bootstrap_deploy.log` |
| `deploy-cluster-post-install` | `post-install` | `deployment_bootstrap_post-install.log` |
| `deploy-cluster-operators` | `operators` | `deployment_bootstrap_operators.log` |
| `deploy-cluster-day2` | `day2` | `deployment_bootstrap_day2.log` |
| `deploy-cluster-discovery` | `discovery` | `deployment_bootstrap_discovery.log` |
| `deploy-plugin PLUGIN=<name>` | (plugin) | `deployment_plugin_<name>.log` |

Steps that run directly on the hypervisor (e.g., `setup-working-dir`, `environment`,
`provision-landing-zone`, `generate-ironic-cert`, `setup-ceph`) do not produce a
log file — their output is only in the SSH session stdout. For these steps, show
"(output in SSH session)" instead of a log path.

### Running Steps

For each step:
1. Show the progress bar with the step about to run
2. **Show the log path** for the step (see mapping table above):
   ```
   [████████░░░░░░░░] 8/22 — deploy-cluster-install
     Log: ssh <host> "ssh cloud-user@<LZ_IP> 'tail -f ~/enclave/deployment_bootstrap_deploy.log'"
   ```
   For steps without a log file:
   ```
   [██░░░░░░░░░░░░░░] 3/22 — provision-landing-zone
     Log: (output in SSH session)
   ```
3. Run it via SSH with the full env block
4. Check the exit code
5. On success: update progress bar with ✓ and move to next
6. On failure: go to **Failure Handling** below

Handle conditional steps as the CI workflow does (e.g., skip `setup-ceph` since
this skill uses lvms only).

After `setup-working-dir`, capture WORKING_DIR: `cat /tmp/working_dir` and add it
to the env block for all subsequent commands.

After `provision-landing-zone`, capture the LZ IP for log monitoring:
```bash
ssh <host> "cd ~/enclave && ./scripts/utils/get_landing_zone_ip.sh"
```
Store this as `LZ_IP` — needed for monitoring long-running steps and log paths.

Immediately verify double-hop SSH connectivity to the LZ before proceeding:
```bash
ssh <host> "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 cloud-user@<LZ_IP> 'hostname'"
```
If this fails, stop and troubleshoot — all subsequent log monitoring depends on it.

### Long-Running Step Monitoring

For steps known to take >10 minutes (especially `deploy-cluster-install` which
takes 30-60+ minutes), provide live feedback:

1. **Run the command in background**: use the Bash tool with `run_in_background: true`

2. **Poll every 5-6 minutes** while the background task runs. Use the LZ log file
   from the mapping table above. Tail via double-hop SSH:
   ```bash
   ssh <host> "ssh -o StrictHostKeyChecking=no cloud-user@<LZ_IP> 'tail -30 ~/enclave/deployment_bootstrap_deploy.log 2>/dev/null'"
   ```

3. **Check VM status**:
   ```bash
   ssh <host> "sudo -n virsh list --all"
   ```

4. **Interpret log patterns** and report human-readable status:
   | Log pattern | Report |
   |---|---|
   | `Writing image to disk: XX%` | "Masters writing image to disk: XX%" |
   | `Waiting for control plane` | "Bootstrap waiting for control plane to initialize" |
   | `Waiting for bootstrap` | "Waiting for bootstrap node to complete" |
   | `Joined` or `reached installation stage Joined` | "Node joined the cluster" |
   | `reached installation stage Done` | "Node installation complete" |
   | `Rebooting` | "Node rebooting after image write" |
   | `Bootstrap complete` | "Bootstrap complete, finalizing installation" |
   | `Install complete` | "Cluster installation complete!" |
   | `Configuring` | "Node configuring after reboot" |
   | Error / failure patterns | Flag and prepare for failure handling |

5. **Stop polling** when the background task notification arrives, then check
   the exit code and proceed.

## Step 11: Report Completion

Show the final progress bar at 100%:
```
[████████████████] 22/22 — verify-cluster ✓

Deployment complete!
```

Summarize:
- Cluster name
- Branch/PR deployed
- Plugins deployed
- Total deployment time
- **Failed steps** (if any): list each step that failed, whether it was retried
  or skipped, and the error summary
- Landing Zone IP: `<LZ_IP>`
- How to access the cluster from the LZ:
  ```bash
  export KUBECONFIG=/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig
  export PATH=$PATH:/home/cloud-user/sessions/1/bin/
  ```

For **disconnected** deployments, also verify the mirror registry is running
on the Landing Zone and include the result in the summary:
```bash
ssh <host> "ssh -o StrictHostKeyChecking=no cloud-user@<LZ_IP> 'podman ps --filter name=quay --format \"table {{.Names}}\t{{.Status}}\"'"
```

## Step 12: Install Day-2 Addon Plugins or Experience

**Skip this step if the base deployment failed** (any step in Step 10 exited
non-zero).

If the user selected addon plugins or an experience in Step 9, install them
now — in **unattended** mode proceed directly without confirmation. If the
user skipped in Step 9, ask again (addon plugins, experience, or skip).

For each plugin in the ordered list, run:
```bash
ssh <host> "cd ~/enclave && <env-block> make -f Makefile.ci deploy-plugin PLUGIN=<name>"
```

Include plugin steps in the progress bar, extending `TOTAL_STEPS` accordingly.
Show the log path for each plugin:
```
[██████████████░░] 20/22 — deploy-plugin PLUGIN=trust-manager
  Log: ssh <host> "ssh cloud-user@<LZ_IP> 'tail -f ~/enclave/deployment_plugin_trust-manager.log'"
```

If a plugin fails, report the error and ask the user whether to:
1. Retry the failed plugin
2. Skip it and continue with the next one
3. Stop plugin installation

## Standalone Plugin/Experience Installation

When the skill is invoked with keywords like "install plugins", "deploy plugins",
"install experience", or "deploy experience" on a host that already has a
successful Enclave deployment:

1. Ask for the SSH target (Step 1)
2. Verify connectivity (Step 2)
3. Check for an existing deployment — VMs must be running and the cluster
   accessible
4. Verify the enclave repo is present on the host and identify the branch
5. Ask the user to select addon plugins or an experience (same flow as Step 9)
6. Run the plugin installation (same flow as Step 12)

## Cleanup

If the user requests cleanup, or if redeployment is needed after a failure:

```bash
ssh <host> "cd ~/enclave && <env-block> make -f Makefile.ci clean"
```

Requires the same env vars (ENCLAVE_CLUSTER_NAME, WORKING_DIR, etc.).

After cleanup, verify:
```bash
ssh <host> "cd ~/enclave && <env-block> make -f Makefile.ci verify-cleanup"
```

## Failure Handling

When a step fails:

1. **Capture the error** — show the last 50 lines of output
2. **For long-running steps**: also dump the LZ deployment log tail using the
   correct log filename from the target-to-log mapping table above (the log
   name does not always match the make target name):
   ```bash
   ssh <host> "ssh -o StrictHostKeyChecking=no cloud-user@<LZ_IP> 'tail -50 /home/cloud-user/enclave/deployment_bootstrap_<LOG_NAME>.log 2>/dev/null'"
   ```
3. **Analyze** — look for known patterns:
   - `CI_TOKEN` / `No valid CI_TOKEN` → verify `OPENSHIFT_CI=true` is set
   - OOM / `Out of memory` / `Killed process` → check `free -m` and `dmesg | grep -i oom`,
     suggest lower `MASTER_MEMORY_VAL`
   - Network errors → check `sudo -n virsh net-list`
   - `bootstrap process timed out` → check VM status with `sudo -n virsh list --all`,
     check if a master VM is shut off (likely OOM killed)
   - Timeout during install → check VM status, check if nodes are booting
   - Pool/volume errors → check `sudo -n virsh pool-list --all`
   - Pod CrashLoopBackOff → get pod logs, check for root cause
   - `Insufficient memory` / pod Pending → nodes are at memory request capacity;
     check `oc describe nodes | grep -A5 "Allocated resources"` for request %,
     suggest redeploying with more RAM per master
   - `BASE_WORKING_DIR` double path → don't include `clusters` in the path
4. **Suggest a fix** — provide the specific command or config change but
   **do not apply any fix without explicit user approval**. Always ask the
   user before running any command that is not part of the CI workflow steps.

## Self-Improvement

While running, if this skill itself gave wrong guidance, missed a step, had stale
information, or could have handled a situation better — suggest an update to this
SKILL.md file.

When you identify a skill improvement:
1. Explain what was wrong or missing in the skill
2. Ask the user if they want you to file an issue or draft a separate patch
3. Generate the proposed change outside the runtime skill flow

---
name: install-virtualized
description: >-
  Deploy or clean up an RHSE virtualized lab environment on a bare-metal host.
  Use when the user says "install enclave", "deploy enclave", "install virtualized",
  "deploy on <host>", "clean up the deployment", "tear down the cluster",
  "install plugins", "deploy plugins", "install experience", or "deploy experience".
when_to_use: >-
  Use for deploying RHSE on bare-metal using dev-scripts VMs, cleaning up
  an existing deployment, or installing day-2 addon plugins or experiences
  on a successful deployment. This skill dynamically reads the CI e2e workflow
  each time to stay in sync with the latest deployment steps.
disable-model-invocation: true
allowed-tools: Bash(ssh *) Bash(scp *) Bash(grep *) Bash(cat *) Bash(make *) Bash(gh *) Bash(git *) Read AskUserQuestion
---

# RHSE Virtualized Installation

This skill deploys Red Hat Sovereign Enclave on a bare-metal host using dev-scripts
to create virtualized infrastructure (Landing Zone VM + OpenShift master VMs).

**This skill follows the CI e2e-deployment workflow exactly.** It reads the workflow
files dynamically each run so it adapts automatically when CI changes.

## User Input

Whenever the skill needs the user to choose between options, use the
`AskUserQuestion` tool to present the choices. This applies to all steps that
offer a selection (installation mode, deployment mode, storage plugin, resource
allocation, pull secret method, branch/PR, etc.). Group related questions into
a single `AskUserQuestion` call when they appear in the same step.

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

## Check CI Health on Main

Before proceeding, check whether the corresponding e2e job is currently passing on `main`:

```bash
gh run list --workflow=e2e-deployment.yml --branch=main --limit=5 --json status,conclusion,displayTitle,createdAt
```

If the most recent run for the selected mode (connected or disconnected) is **failing**:
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
ssh <host> "sudo virsh list --all --name 2>/dev/null | grep -v '^$' || true"
```

If VMs are found (any output), an enclave deployment is already running. This
skill does not support multiple concurrent deployments on the same host.

Show the user:
- The list of running VMs
- Available vs total RAM: `ssh <host> "free -m | awk '/Mem:/{printf \"Available: %d MB / Total: %d MB\", \$7, \$2}'"`
- Warn: "An existing enclave deployment was found on this host. Multiple
  deployments are not supported. You can clean it up before proceeding or
  abort."

Ask the user (via `AskUserQuestion`):
1. **Clean up and redeploy** — run `make -f Makefile.ci clean` to remove the
   existing deployment, then proceed with a fresh install
2. **Abort** — stop the skill

If cleanup is selected, follow the **Cleanup** section procedure before
continuing to Step 3.

## Step 3: Choose Installation Mode

Ask the user: **attended** or **unattended**?

- **Attended** (default): ask for confirmation before running each deployment
  step (Step 10) and each plugin installation (Step 12). Between steps, show
  what's about to run and wait for the user to approve before proceeding.
- **Unattended**: run all deployment steps (Step 10) and plugin/experience
  installation (Step 12) automatically without asking for confirmation. If any
  step fails, stop and ask the user what to do.

Both modes go through the same initial configuration (Steps 4-9), collecting
required values and presenting options for confirmation.

## Step 4: Clone Enclave Repository on Host

Instead of assuming the repo exists, clone it on the bare-metal host.

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
ssh <host> "python3 -c \"import json; d=json.load(open('/home/\$(whoami)/.pull-secret.json')); assert 'registry.redhat.io' in d.get('auths',{}); print('Pull secret valid: ' + str(len(d['auths'])) + ' registries')\" "
```

For all subsequent make targets, reference the pull secret as:
```bash
export PULL_SECRET="\$(cat ~/.pull-secret.json)"
```

## Step 6: Ask Deployment Mode

Ask the user: **connected** or **disconnected**?

- **Connected**: proceed with the workflow
- **Disconnected**: tell the user this mode is not yet supported by this skill, stop

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

```
RAM:  NUM_MASTERS x MASTER_MEMORY_VAL + LANDINGZONE_MEMORY + 4096 (host overhead)
vCPU: NUM_MASTERS x MASTER_VCPU_VAL + LANDINGZONE_VCPU
Disk: NUM_MASTERS x (MASTER_DISK + VM_EXTRADISKS_SIZE) + LANDINGZONE_DISK + overhead
```

### Resource sizing logic

Calculate `available_for_vms = total_ram_mb - 4096` (host overhead).

**Hosts with ≤64 GB RAM** — **do not recommend installing RHSE**:
- Warn: "This host has ≤64 GB RAM. RHSE requires 3 master nodes and the
  platform components (ACM, Quay, Clair) need significant memory headroom.
  A minimum of 128 GB RAM is recommended for a stable deployment."
- Ask the user if they want to proceed anyway with reduced resources or stop.
- If proceeding: calculate best-effort values (see below) and warn that the
  deployment may fail due to insufficient memory for scheduling pods.

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

## Step 8: Ask Storage Plugin

Ask the user: **lvms** or **odf**?

- **lvms**: proceed (default, lightweight)
- **odf**: tell the user this is not yet supported by this skill, stop

## Step 9: Collect Environment Variables and Day-2 Options

From the CI workflow `env:` block extracted earlier, identify which variables are
secrets or host-specific and ask the user for those values.

Also set:
- `ENCLAVE_CLUSTER_NAME` (default: `enclave-test`)
- `MASTER_MEMORY_VAL` override if adjusted in Step 7

### Day-2 Addon Plugins or Experiences (Optional)

Ask the user if they want to install day-2 addon plugins or an experience after
the base deployment completes. Use `AskUserQuestion` with these options:

1. **Install addon plugins** — select individual plugins and their install order
2. **Install an experience** — select a predefined experience bundle
3. **Skip** — no day-2 addons

Options 1 and 2 are mutually exclusive.

**Addon plugins**: discover available addon plugins dynamically:
```bash
for f in plugins/*/plugin.yaml; do
  name=$(grep '^name:' "$f" | awk '{print $2}')
  type=$(grep '^type:' "$f" | awk '{print $2}')
  if [ "$type" = "addon" ]; then echo "$name"; fi
done
```

Present the list via `AskUserQuestion` with `multiSelect: true`. After
selection, ask the user to specify the installation order. Store the ordered
list — plugins will be installed one by one in that order after the base
deployment succeeds.

**Experiences**: discover available experiences dynamically:
```bash
for f in experiences/*/experience.yaml; do cat "$f"; echo "---"; done
```

Present each experience with its `name`, `description`, and `plugins` list.
Only one experience can be selected. The plugin installation order follows
the order defined in the experience YAML.

Store the selection. Installation happens in Step 12 after the base deployment
succeeds.

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

Handle conditional steps as the CI workflow does (e.g., `setup-ceph` only for ODF,
`generate-ironic-cert` only when `ENCLAVE_IRONIC_HTTPS=true`).

After `setup-working-dir`, capture WORKING_DIR: `cat /tmp/working_dir` and add it
to the env block for all subsequent commands.

After `provision-landing-zone`, capture the LZ IP for log monitoring:
```bash
ssh <host> "cd ~/enclave && ./scripts/utils/get_landing_zone_ip.sh"
```
Store this as `LZ_IP` — needed for monitoring long-running steps and log paths.

### Long-Running Step Monitoring

For steps known to take >10 minutes (especially `deploy-cluster-install` which
takes 30-60+ minutes), provide live feedback:

1. **Run the command in background**: use the Bash tool with `run_in_background: true`

2. **Poll every 2-3 minutes** while the background task runs. Use the LZ log file
   from the mapping table above. Tail via double-hop SSH:
   ```bash
   ssh <host> "ssh -o StrictHostKeyChecking=no cloud-user@<LZ_IP> 'tail -30 ~/enclave/deployment_bootstrap_deploy.log 2>/dev/null'"
   ```

3. **Check VM status**:
   ```bash
   ssh <host> "sudo virsh list --all"
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
successful RHSE deployment:

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
2. **For long-running steps**: also dump the LZ deployment log tail:
   ```bash
   ssh <host> "ssh -o StrictHostKeyChecking=no cloud-user@<LZ_IP> 'tail -50 /home/cloud-user/enclave/deployment_bootstrap_<step>.log 2>/dev/null'"
   ```
3. **Analyze** — look for known patterns:
   - `CI_TOKEN` / `No valid CI_TOKEN` → verify `OPENSHIFT_CI=true` is set
   - OOM / `Out of memory` / `Killed process` → check `free -m` and `dmesg | grep -i oom`,
     suggest lower `MASTER_MEMORY_VAL`
   - Network errors → check `sudo virsh net-list`
   - `bootstrap process timed out` → check VM status with `sudo virsh list --all`,
     check if a master VM is shut off (likely OOM killed)
   - Timeout during install → check VM status, check if nodes are booting
   - Pool/volume errors → check `sudo virsh pool-list --all`
   - Pod CrashLoopBackOff → get pod logs, check for root cause
   - `Insufficient memory` / pod Pending → nodes are at memory request capacity;
     check `oc describe nodes | grep -A5 "Allocated resources"` for request %,
     suggest redeploying with more RAM per master
   - `BASE_WORKING_DIR` double path → don't include `clusters` in the path
4. **Suggest a fix** — provide the specific command or config change
5. **Offer options**:
   - Retry the failed step after applying the fix
   - Skip and continue (if safe to do so)
   - Clean up and redeploy from scratch

## Self-Improvement

While running, if this skill itself gave wrong guidance, missed a step, had stale
information, or could have handled a situation better — suggest an update to this
SKILL.md file.

When you identify a skill improvement:
1. Explain what was wrong or missing in the skill
2. Ask the user if they want you to fix the skill
3. If yes: edit this SKILL.md, commit on a new branch, and open a PR following the
   project git workflow (descriptive branch name, commit with
   `Assisted-by: Claude Code <noreply@anthropic.com>` trailer, PR body with
   `## Summary` and `## Test plan`)

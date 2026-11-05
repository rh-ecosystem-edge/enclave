## Project overview

Red Hat Sovereign Enclave (RHSE) is an optionally disconnected infrastructure platform that
delivers a cloud-like experience based on OpenShift. It provisions and maintains OpenShift clusters
on bare metal hardware, supports a local management plane (ACM, Quay), and controls software
ingress into air-gapped environments.

## Languages and tooling

- **Ansible** — deployment automation (playbooks/, 66+ playbooks in 7 phases)
- **Python 3.12** — reconciliation engine and tools (`src/`)
- **Bash** — CI/E2E automation (`ci/`) and host-local helpers (`scripts/`)
- **Jinja2** — cluster config templates (`templates/`)
- **YAML/JSON** — configuration and schema validation (`config/`, `schemas/`)

## Repository layout

```
playbooks/       Ansible playbooks: 01-prepare → 07-configure-discovery
plugins/         Optional components (lvms, odf, openshift-ai, nvidia-gpu, vast-csi)
experiences/     Experience bundles (collections of plugins, e.g. osac, aiaas)
src/             Python source root; a uv workspace (root package + members)
  enclave/       Shipped root package (the `enclave` CLI), installed on clients
    cli.py       Unified CLI entry point (reconcile + tools subcommands)
    utils.py     Shared utilities (configure_logging, etc.) for the enclave package
    reconcile/   Cluster/operator version reconciliation (enclave reconcile subcommand)
    tools/       Additional tools (enclave tools subcommand); new shipped tools go here
  enclave_ci/    CI-only workspace member (the `enclave-ci` CLI); never shipped to clients
    pyproject.toml   Member project metadata + its own dependencies
    enclave_ci/  Package code (cli.py, cert_gen/, ...)
  tests/         All Python tests for every package (pytest, shared fixtures/helpers)
ci/              CI/E2E automation invoked by .github, by function (lib, infrastructure, deployment, verification, diagnostics)
scripts/         Host-local helper scripts not invoked by CI
templates/       Jinja2 templates for cluster and registry configs
schemas/         JSON schemas for config and plugin descriptor validation
config/          User-provided cluster configuration (gitignored, examples provided)
defaults/        Default variable values (catalogs, operators, platforms)
docs/            Deployment, configuration, and architecture guides
ci/docs/         CI-related documentation (runner setup, workflow guides, troubleshooting)
```

## Building and testing

```bash
# Python
make python-unit-test     # pytest with coverage required (see pyproject.toml)
make python-linter-test   # ruff formatting check
make python-types-test    # mypy strict type checking
make python-format        # auto-format Python code

# Infrastructure validation (runs on every PR)
make -f Makefile.ci validate             # all checks
make -f Makefile.ci validate-shell       # shellcheck
make -f Makefile.ci validate-yaml        # yamllint
make -f Makefile.ci validate-ansible     # ansible-lint
make -f Makefile.ci validate-plugins     # plugin descriptor validation
```

## Debugging CI failures

Prefer the `gh` CLI over the web UI — it works headless and is scriptable.

```bash
# List checks for a PR and spot which failed
gh pr checks <pr>

# Inspect a run and jump straight to the failing step logs
gh run view <run-id>
gh run view <run-id> --log-failed
gh run view --job <job-id> --log        # full log for a single job

# Watch a run until it settles
gh pr checks <pr> --watch --interval 60
```

Every e2e run uploads a diagnostics bundle produced by
`ci/verification/collect_ci_artifacts.sh` (artifact name
`e2e-{connected,disconnected}-<cluster>-<run_id>`, 7-day retention). Pull it before
reasoning about a deploy failure — the step log alone is usually not enough:

```bash
gh run download <run-id> -n e2e-disconnected-<cluster>-<run_id> -D ./artifacts
```

The bundle captures cluster state at failure time: `cluster/events.txt`
(`FailedScheduling`, `Insufficient memory`, …), `cluster/pods.txt` (Pending/Failed
pods), per-pod describe and logs (including `--previous`), and
`cluster/quay-diagnostics-*/` (QuayRegistry `.status.conditions`, deployment/PVC
overview, health probes). It also captures the redacted enclave config and rendered
plugin Helm values under `landing-zone/config/` and `landing-zone/helm-values/` — check
these when a plugin deploys with unexpected values. See
[ci/docs/CI_TROUBLESHOOTING.md](ci/docs/CI_TROUBLESHOOTING.md) for the full
artifact map and step-by-step recipes.

## Code conventions

### Python (`src/`)

**Workspace layout.** `src/` is a uv workspace. The root project `enclave`
(declared in the repo-root `pyproject.toml`) is the only package shipped to clients;
its `[tool.hatch.build.targets.wheel] only-include = ["src/enclave"]` keeps the
wheel to that package, and `setup_ansible.sh` installs it on the Landing Zone with
`uv tool install .`. CI-only Python lives in workspace members under `src/` (each
with its own `pyproject.toml`), currently `enclave_ci`. Members are declared in the
root `[tool.uv.workspace] members` and are excluded from the release tarball
(`.github/workflows/build-push-tarball.yml`), so they never reach clients.

**Where code goes.**
- Shipped code (runs on a client/Landing Zone): a package under `src/enclave/`.
  New shipped tools go under `src/enclave/tools/` (exposed via `enclave tools`).
- CI/runner-only code (never installed on clients, e.g. certificate generation,
  test-env provisioning): a workspace member under `src/`. Add it to the root
  `[tool.uv.workspace] members`, give it its own `pyproject.toml` (with its own
  dependencies and `[project.scripts]`), and exclude its path from the tarball.
  The root project must **not** declare a dependency or `[tool.uv.sources]` entry on
  a member — a workspace-source reference there makes the client `uv tool install .`
  fail once the member's source is excluded from the tarball.

**CLIs.** Click-based, one console script per package: `enclave = enclave.cli:cli`
(root), `enclave-ci = enclave_ci.cli:cli` (member). Each is a top-level group with
subcommands/subgroups (e.g. `enclave reconcile`, `enclave tools`,
`enclave-ci cert-gen`). The `enclave` entry points call `configure_logging()` from
`src/enclave/utils.py`. A member does not depend on `enclave`, so it must not import
that helper; it handles its own logging (add a member-local helper if one is needed).

**Dependencies.** Pin exact versions with `==` for every runtime dependency,
including the `hatchling` build backend in each `[build-system] requires`. Member
dependencies live in the member's own `pyproject.toml`, not in a root group.

**Style & types.** Strict mypy — every function needs type annotations. ruff with an
88-char line limit, comprehensive linting and import sorting. Use the custom
exception hierarchy with descriptive messages.

**Tests.** A single test tree in `src/tests/` covers every package, including the
members (member tests import the member package, e.g.
`from enclave_ci.cert_gen.main import ...`). Shared helpers/fixtures live there
(`cert_helpers.py`, `fixtures.py`, `conftest.py`) and are imported as `tests.*`. Test
files need unique names across the tree (there is one `tests` package), so name a
member's tests distinctly (e.g. `test_enclave_ci_cli.py`, not a second `test_cli.py`).
The suite runs with `uv run --all-packages` so member packages and their
dependencies are importable.

**Checks** (run before opening a PR; all use `--all-packages` where needed):
`make python-unit-test` (pytest + coverage, see `fail_under` in `pyproject.toml`),
`make python-types-test` (mypy), `make python-linter-test` (ruff), `make
python-format` (auto-format).

### Ansible (`playbooks/`)
- Phase-based structure: numbered playbooks orchestrate reusable tasks from `playbooks/tasks/`
- Tasks must be idempotent and re-runnable
- Use descriptive `name:` fields on all tasks
- Explicit `become: yes` for privilege escalation

### Shell (`ci/`, `scripts/`)
- `set -euo pipefail` at the top of every script
- Source shared utilities from `ci/lib/` (logging, env checks, etc.)

### Plugins
- Each plugin has a single `plugin.yaml` descriptor validated against `schemas/plugin.yaml`
- Optional lifecycle task files: `tasks/early-validate.yaml`, `tasks/deploy.yaml`, `tasks/post-validate.yaml`
- Declarative operator and registry requirements in the descriptor

### Config and schemas (`config/`, `defaults/`, `schemas/`)

- Every property added to a file under `config/` or `defaults/` (including plugin example/default
  configs) must also be added to its matching schema file under `schemas/` (same base filename,
  e.g. `defaults/catalogs.yaml` → `schemas/catalogs.yaml`) in the same PR
- Run `make -f Makefile.ci validate-json-schema` before opening a PR to catch missing schema entries

### Makefiles

- Avoid code blobs inside a Makefile target, write them as separate shell scripts.

## CI test environment — `vm_infra.py` is the single source of truth

`ci/infrastructure/vm_infra.py` owns the entire CI test environment: the cluster
identity (generates the name, derives `WORKING_DIR`), libvirt networks and bridges, DNS
records, the storage pool, volumes, disks, and all VM domains (including the Landing
Zone). It writes everything downstream needs to one file, `$WORKING_DIR/cluster-env.sh`
(also echoed to stdout), which every other script consumes via `load_cluster_env`.

When changing the environment, follow this philosophy so the design holds:

- **Create and own infrastructure only in `vm_infra.py`.** Networks, DNS, pools, volumes,
  domains, MACs, IPs, gateways, endpoints — define them there and emit them in
  `cluster-env.sh`. Do **not** create or mutate libvirt resources out-of-band from other
  scripts (no `virsh net-update`, `virt-install`, pool/volume creation, `net-start`, etc.).
- **Downstream scripts consume, never derive.** `provision`, `install`, `deploy`, `verify`
  and `cleanup` read values from `cluster-env.sh`; they must not recompute gateways/ports/IPs
  or re-scrape libvirt for something `vm_infra.py` already knows. If you need a new value,
  add it to `vm_infra.py` and emit it — don't compute it in a consumer.
- **Legitimate exceptions:** OS-level provisioning *inside* a guest (cloud-init, on-guest
  `nmcli`) stays in `provision_landing_zone.sh`; shared-host safety (the subnet `flock`,
  `reap`/cleanup, per-cluster resource namespacing) stays and must be preserved — the host
  may be shared/persistent again (baremetal/hybrid), not only ephemeral AWS.

See [ci/docs/vm-infra.md](ci/docs/vm-infra.md) for the full design.

## Git workflow

- **NEVER push commits directly to `main`** — all changes must go through pull requests
- This project does not use forks — create feature branches in the main repository
- Branch naming: use descriptive names like `feature/add-xyz`, `fix/bug-description`, `docs/update-readme`
- When work is ready, create a PR for review — do not push to main even if you have permissions

### GitHub CLI (Recommended)

Use the [gh](https://cli.github.com/) tool for efficient GitHub workflow management from the command line.

**Installation** (choose the method that fits your environment):
```bash
brew install gh              # macOS/Linux
sudo dnf install gh          # Fedora/RHEL
sudo apt install gh          # Debian/Ubuntu
# Immutable distros: use toolbox/distrobox or download binary
# Direct download: https://github.com/cli/cli/releases
```

**Common operations**:
```bash
gh auth login  # Initial authentication
gh pr create --title "OSAC-123: Add feature" --body "..."
gh pr view 432
gh pr comment 432 --body "✨ **Claude Code**: Fixed in abc1234"
```

The CLI enables automation and improves AI agent integration with PR workflows.

## Issue tracking

All work in this repository is tracked in the **OSAC** Jira board with the **Enclave** component:
- Create tickets for features, bugs, and documentation updates
- Use component: `Enclave`
- Reference the Jira ticket in commits and PRs (e.g., `OSAC-123: Add feature X`)
- Update Jira tickets with PR links for traceability (a ticket may have multiple PRs if work is split across incremental changes)
- Maintain ticket status throughout the workflow:
  - **In Progress** — when work begins or a PR is created
  - **In Review** — when PR(s) are submitted for review (if available in workflow)
  - **Done/Closed** — when all PRs are merged and work is complete

## Jira Task Management

For detailed Jira task management workflows with this project, see the [jira-task-management skill](https://github.com/osac-project/osac-workspace/blob/main/skills/jira-task-management/SKILL.md).

When creating issues, use component `Enclave` and project `OSAC`.

## Git commits

- If AI assisted the commit, include an `Assisted-by: <tool>` trailer.
- Recommended format: `Assisted-by: Claude Code <noreply@anthropic.com>`

## PR review responses

When responding to PR review comments, clearly identify that the response is from an AI agent:
- Prefix responses with `✨ **Claude Code**:` to indicate the agent is responding
- Include the commit hash that addresses the comment
- Keep responses concise and factual

## CodeRabbit review workflow

All PRs are automatically reviewed by CodeRabbit, configured per Red Hat Product Security requirements (`.coderabbit.yaml`).

**Required workflow**:
- **Address all CodeRabbit comments** before merging
- "Addressed" means either:
  - Fix the issue and commit the change, OR
  - Respond to the comment explaining why you're not fixing it (with reasoning)
- **Out-of-scope comments**: CodeRabbit may flag issues outside the scope of your PR. In such cases, create a separate PR to address them rather than expanding the current PR's scope
- When all feedback is addressed, request approval: `@coderabbitai approve`

CodeRabbit's configuration enforces security best practices across injection prevention, cryptography, container hardening, supply chain security, and more. Comments should be taken seriously as they often flag real security or correctness issues.

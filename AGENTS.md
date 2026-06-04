## Project overview

Red Hat Sovereign Enclave (RHSE) is an optionally disconnected infrastructure platform that
delivers a cloud-like experience based on OpenShift. It provisions and maintains OpenShift clusters
on bare metal hardware, supports a local management plane (ACM, Quay), and controls software
ingress into air-gapped environments.

## Languages and tooling

- **Ansible** — deployment automation (playbooks/, 66+ playbooks in 7 phases)
- **Python 3.12** — reconciliation engine and tools (`src/`)
- **Bash** — provisioning and setup scripts (`scripts/`, 53+ scripts)
- **Jinja2** — cluster config templates (`templates/`)
- **YAML/JSON** — configuration and schema validation (`config/`, `schemas/`)

## Repository layout

```
playbooks/       Ansible playbooks: 01-prepare → 07-configure-discovery
plugins/         Optional components (lvms, odf, openshift-ai, nvidia-gpu, authorino, vast-csi)
experiences/     Experience bundles (collections of plugins, e.g. osac, aiaas)
src/             Python source root (src layout)
  reconcile/     Cluster/operator version reconciliation (enclave reconcile subcommand)
  tools/         Additional Python tools (enclave tools subcommand); new tools go here
  cli.py         Unified CLI entry point (reconcile + tools subcommands)
  utils.py       Shared utilities for all Python packages under src/
  tests/         All Python tests (pytest, shared fixtures)
scripts/         Shell scripts organized by function (setup, infrastructure, deployment, …)
templates/       Jinja2 templates for cluster and registry configs
schemas/         JSON schemas for config and plugin descriptor validation
config/          User-provided cluster configuration (gitignored, examples provided)
defaults/        Default variable values (catalogs, operators, platforms)
docs/            Deployment, configuration, and architecture guides
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

## Code conventions

### Python (`src/`)
- Strict mypy: all functions must have type annotations
- ruff: 88-char line limit, comprehensive linting and import sorting
- Custom exception hierarchy with descriptive messages
- Click-based CLIs with subcommands (one per package)
- Shared utilities live in `src/utils.py` (includes `configure_logging()` for CLI entry points); new Python tools go under `src/tools/`
- Check python-*-test Makefile targets

### Ansible (`playbooks/`)
- Phase-based structure: numbered playbooks orchestrate reusable tasks from `playbooks/tasks/`
- Tasks must be idempotent and re-runnable
- Use descriptive `name:` fields on all tasks
- Explicit `become: yes` for privilege escalation

### Shell (`scripts/`)
- `set -euo pipefail` at the top of every script
- Source shared utilities from `scripts/lib/` (logging, env checks, etc.)

### Plugins
- Each plugin has a single `plugin.yaml` descriptor validated against `schemas/plugin.yaml`
- Optional lifecycle task files: `tasks/early-validate.yaml`, `tasks/deploy.yaml`, `tasks/post-validate.yaml`
- Declarative operator and registry requirements in the descriptor

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

**REQUIRED**: Before performing ANY Jira operations, you MUST read and follow the patterns documented in the [jira-task-management skill](https://github.com/osac-project/osac-workspace/blob/main/skills/jira-task-management/SKILL.md). Use WebFetch to retrieve it if you haven't consulted it yet.

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

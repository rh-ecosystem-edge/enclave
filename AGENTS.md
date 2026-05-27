## Project overview

Red Hat Sovereign Enclave (RHSE) is an optionally disconnected infrastructure platform that
delivers a cloud-like experience based on OpenShift. It provisions and maintains OpenShift clusters
on bare metal hardware, supports a local management plane (ACM, Quay), and controls software
ingress into air-gapped environments.

## Languages and tooling

- **Ansible** — deployment automation (playbooks/, 66+ playbooks in 7 phases)
- **Python 3.12** — reconciliation engine and CLI (`reconcile/`)
- **Bash** — provisioning and setup scripts (`scripts/`, 53+ scripts)
- **Jinja2** — cluster config templates (`templates/`)
- **YAML/JSON** — configuration and schema validation (`config/`, `schemas/`)

## Repository layout

```
playbooks/       Ansible playbooks: 01-prepare → 07-configure-discovery
plugins/         Optional components (lvms, odf, openshift-ai, nvidia-gpu, authorino, vast-csi)
experiences/     Experience bundles (collections of plugins, e.g. osac, aiaas)
reconcile/       Python CLI for cluster/operator version reconciliation
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

### Python (`reconcile/`)
- Strict mypy: all functions must have type annotations
- ruff: 88-char line limit, comprehensive linting and import sorting
- Custom exception hierarchy with descriptive messages
- Click-based CLI with subcommands
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

## Git commits

- If AI assisted the commit, include an `Assisted-by: <tool>` trailer.
- Recommended format: `Assisted-by: Claude Code <noreply@anthropic.com>`

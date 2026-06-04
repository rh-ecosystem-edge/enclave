# Contributing to Red Hat Sovereign Enclave

Thank you for contributing to the Red Hat Sovereign Enclave project! This document outlines the process and expectations for contributing.

## Pull Request Process

All changes must go through pull requests:

1. **Never push directly to `main`** — all changes require PR review
2. Create feature branches in the main repository (we don't use forks)
3. Use descriptive branch names: `feature/add-xyz`, `fix/bug-description`, `docs/update-readme`
4. Reference Jira tickets in commits and PR titles (e.g., `OSAC-123: Add feature X`)

## CodeRabbit Review Requirements

All PRs are automatically reviewed by CodeRabbit, configured per Red Hat Product Security requirements.

**Before merging, you must**:
- **Address all CodeRabbit comments**
- "Addressed" means either:
  - Fix the issue and commit the change, OR
  - Respond to the comment explaining why you're not fixing it (with clear reasoning)
- **Out-of-scope comments**: If CodeRabbit flags issues outside your PR's scope, create a separate PR to address them rather than expanding the current PR
- When all feedback is addressed, request approval: `@coderabbitai approve`

CodeRabbit enforces security best practices across injection prevention, cryptography, container hardening, supply chain security, and more. Take comments seriously as they often flag real security or correctness issues.

## Code Quality Standards

### Python (`src/`)
- Strict mypy: all functions must have type annotations
- ruff: 88-char line limit, comprehensive linting and import sorting
- All tests must pass: `make python-unit-test python-linter-test python-types-test`

### Ansible (`playbooks/`)
- Tasks must be idempotent and re-runnable
- Use descriptive `name:` fields on all tasks
- Explicit `become: yes` for privilege escalation

### Shell (`scripts/`)
- `set -euo pipefail` at the top of every script
- Source shared utilities from `scripts/lib/`

### Infrastructure validation
All PRs must pass:
```bash
make -f Makefile.ci validate
```

## Git Commit Guidelines

- Write clear, descriptive commit messages
- Reference Jira tickets (e.g., `OSAC-123: Fix authentication bug`)
- If AI assisted the commit, include: `Assisted-by: Claude Code <noreply@anthropic.com>`

## Issue Tracking

- All work is tracked in the **OSAC** Jira board with component **Enclave**
- Create tickets for features, bugs, and documentation updates
- Update Jira ticket status throughout the workflow:
  - **In Progress** — when work begins or a PR is created
  - **In Review** — when PR(s) are submitted for review
  - **Done/Closed** — when all PRs are merged

## Questions?

- For AI agents: see [AGENTS.md](./AGENTS.md) for additional technical details
- For general questions: reach out on #wg-osac-enclave

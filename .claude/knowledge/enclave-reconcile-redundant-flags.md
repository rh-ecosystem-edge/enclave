---
title: --no-dry-run and explicit KUBECONFIG are redundant in enclave reconcile playbook tasks
tags: [enclave-cli, reconcile, kubeconfig]
updated: 2026-07-16
---

`--no-dry-run` and an explicit `KUBECONFIG` env var are both dead weight when calling `enclave
reconcile` from a playbook task, but stale copies of both still exist in the codebase — don't copy
them into new tasks.

**Why:** `src/enclave/reconcile/cli.py` sets `--dry-run/--no-dry-run` with `default=False`, so
`--no-dry-run` always just restates the default — it's a no-op flag. `src/enclave/utils.py`'s
`setup_kubeconfig()` (introduced in PR #512) auto-falls back to `~/.config/enclave/kubeconfig`
(symlinked by `migrations.yaml`) whenever `KUBECONFIG` isn't already set in the environment, so
exporting it explicitly in a task's `environment:` block is also unnecessary. PR #584 removed both
from `playbooks/upgrade.yaml` after reviewer rporres flagged them — but the repo-wide cleanup wasn't
applied everywhere; `playbooks/tasks/configure_operator.yaml` still has the redundant `--no-dry-run`
flag as of this writing.

**How to apply:** When writing a new `enclave reconcile` invocation, don't copy
`configure_operator.yaml`'s flag as a template — check `playbooks/upgrade.yaml` instead for the
cleaned-up form, and don't add an explicit `KUBECONFIG` env var unless there's a reason to override
the auto-resolved default.

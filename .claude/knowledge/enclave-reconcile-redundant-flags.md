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
necessarily applied everywhere else that calls `enclave reconcile`, so older task files may still
carry a stale `--no-dry-run` or explicit `KUBECONFIG`.

**How to apply:** When writing or copying a new `enclave reconcile` invocation, don't assume an
existing task file is the cleaned-up reference — check `src/enclave/reconcile/cli.py`'s actual
defaults and `src/enclave/utils.py`'s `setup_kubeconfig()` behavior directly, and drop either flag
unless there's a concrete reason to override the default.

---
title: Remote plugin support (PR #548) — settled design decisions
tags: [plugins, decision, pr-scoped]
updated: 2026-07-16
---

**Scope note:** this file tracks decisions on an open, in-progress PR (#548, branch
`feature/remote-plugins`, tracked under Jira epic OSAC-1590 / OSAC-1966). Update or remove this file
once the PR merges or the design changes further — it documents settled-for-now choices, not a
permanent codebase fact.

Several design questions were explicitly settled during review and should not be re-proposed if this
branch is picked back up:

- Use a `remote:` **field** on existing plugin types (foundation/addon), not a new `type: remote`
  (commit `17d95fd`).
- Remote plugins must be fetched **inside `common/load-vars.yaml`**, before plugin configs are
  loaded, because both entry points (`bootstrap.sh` phases and `make deploy-plugin`) go through
  `load-vars.yaml` (commit `e81076d`). Fetching only in `deploy_plugin.yaml` was rejected as too late
  for var loading.
- Only **enabled** plugins are fetched, to avoid fetch failures for disabled plugins
  (commit `e534725`).
- Re-fetching/overwriting the plugin directory on every run is **intentional by design** (always get
  latest remote content) — not a bug to fix for idempotency.
- `remote.url` is restricted to HTTPS/SSH; `remote.path` is validated against path traversal (`..`,
  absolute paths); raw URLs are redacted from logs/debug output.
- `scripts/verification/validate_plugins.sh` changes were deliberately kept minimal — only "remote"
  added to the list of allowed fields, with full validation logic deferred to a future Python
  refactor rather than done in this PR.

**How to apply:** If continuing or reviewing PR #548, treat the above as settled rather than
re-litigating — e.g. don't re-propose fetching in `deploy_plugin.yaml`, and don't "fix" the
re-fetch-every-run behavior as if it were an idempotency bug.

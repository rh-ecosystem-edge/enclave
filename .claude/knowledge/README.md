# Shared knowledge base

Non-obvious facts, conventions, and decisions about this project that are worth every
contributor's Claude instance knowing — so nobody has to re-derive them (and re-spend the
tokens) from scratch. This is checked into git: it's shared across everyone working in this
repo, unlike a personal `~/.claude` memory which only one person's Claude sees.

## When to read this

Before spending significant tokens investigating something that feels like it might already
be a known gotcha, convention, or settled decision (e.g. "does X need updating when I touch
Y", "has this scope question already been answered"), skim the index below first.

## When to add to this

When you (Claude) discover something during a session that:
- isn't already derivable by reading the code/config/docs directly, AND
- would save a meaningful amount of investigation for the *next* person's Claude session

... add a new file here rather than letting it live only in your private memory or the
conversation transcript. Good candidates: recurring review feedback that generalizes ("every
PR that touches X also needs Y"), scope decisions on tickets/issues that could otherwise be
re-litigated, non-obvious cross-file relationships, gotchas that have bitten more than one PR.

Do NOT put here: information specific to one person's workflow or preferences (that belongs in
their personal memory), anything already stated in `AGENTS.md` or derivable by reading the
code, ephemeral in-progress task state, or decisions scoped to a single still-open PR (those go
stale the moment the PR merges or changes direction, with nobody reliably coming back to
clean them up).

## Treat entries with some skepticism

Every entry here was written by a Claude instance, generally by mining past session
transcripts — not by a human independently verifying the claim. That means a misunderstanding
from one session can get "laundered" into something that reads as settled, citable fact. Before
relying on an entry to make a decision (not just as a lead to investigate further), spot-check
its concrete claims against the current code rather than taking it on faith — especially for
anything load-bearing. Periodically challenge entries you're using: does this still hold, or
was it true once and never revisited?

## Stay alert to staleness

Entries describe the codebase as it was when written, not as it will always be. Prefer
referencing files and describing patterns/behavior over citing specific line numbers, exact
current lists (e.g. "which plugins currently do X"), or the live/merged state of a specific
open PR or branch — all of those drift out of date fast, and a wrong specific is worse than a
vague one because it reads as authoritative. When you notice an entry no longer matches the
code (a referenced file/behavior has changed, a cited PR merged and changed the picture, a
"known gap" got closed), don't just ignore it — fix or delete the entry. If that's unrelated to
what you're currently working on, do it as a small separate PR rather than folding it into an
unrelated change.

## Format

One file per topic, kebab-case filename, with this frontmatter:

```markdown
---
title: Short title
tags: [config, schema]
updated: 2026-07-16
---

Statement of the fact/rule/decision, then:

**Why:** the reasoning or incident that established it.
**How to apply:** what this should change about future work.
```

Add a one-line entry to the index below when you add a file, and remove it when you delete one.

## Index

- [ansible-jinja-gotchas.md](ansible-jinja-gotchas.md) — recurring Ansible/Jinja footguns: eager `assert` templating, folded-scalar indentation, missing `| bool` casts
- [catalog-source-naming.md](catalog-source-naming.md) — two similar-looking operator catalog variables exist on purpose; don't unify them
- [config-schema-sync.md](config-schema-sync.md) — why the documented config/schema-sync rule still gets missed in practice
- [connected-auto-mce.md](connected-auto-mce.md) — `connected_auto` is the standard auto-approval mechanism; MCE needs a second, separate patch
- [disconnected-plugin-mirroring.md](disconnected-plugin-mirroring.md) — two conventions for mirroring addon-plugin registries, and an `installOperators` gotcha
- [enclave-cli-path.md](enclave-cli-path.md) — the `enclave` CLI needs `~/.local/bin` on `PATH` explicitly in playbooks
- [enclave-reconcile-redundant-flags.md](enclave-reconcile-redundant-flags.md) — `--no-dry-run` and explicit `KUBECONFIG` are redundant on `enclave reconcile` calls
- [github-actions-security-checklist.md](github-actions-security-checklist.md) — the standard CodeRabbit security checklist for new GitHub Actions workflows
- [infraenv-discovery-gotchas.md](infraenv-discovery-gotchas.md) — InfraEnv ISO-readiness race condition and a settled `interfaceName` schema decision
- [ironic-metal3-lifecycle.md](ironic-metal3-lifecycle.md) — two distinct Ironic deployments in the pipeline, both fully torn down after install
- [jira-cli-debug-warning.md](jira-cli-debug-warning.md) — never pass `--debug` to the `jira` CLI, it leaks the auth token
- [k8s-info-fail-open.md](k8s-info-fail-open.md) — `k8s_info` + `ignore_errors` as an idempotency gate must not fail open toward a disruptive action
- [loop-label-sensitive-data.md](loop-label-sensitive-data.md) — looping over host/BMC objects needs `loop_control.label` to pass CodeRabbit's sensitive-data check
- [operators-schema-duplication.md](operators-schema-duplication.md) — `schemas/operators.yaml` and `schemas/plugin.yaml` duplicate the operator definition and can drift
- [plugin-e2e-coverage.md](plugin-e2e-coverage.md) — a green e2e run doesn't validate every plugin; check the deploy matrix
- [tls-cert-test-coverage-gap.md](tls-cert-test-coverage-gap.md) — known gap in TLS cert-handling test coverage
- [updateservice-ca-trust.md](updateservice-ca-trust.md) — UpdateService CA trust: chain-completion requirement and a partner-overlay ConfigMap gotcha

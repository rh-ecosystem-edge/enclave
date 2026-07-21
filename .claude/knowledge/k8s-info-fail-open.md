---
title: k8s_info + ignore_errors must not fail open toward a disruptive action
tags: [ansible, coderabbit, idempotency]
updated: 2026-07-16
---

A recurring CodeRabbit red flag: using `kubernetes.core.k8s_info` with `ignore_errors: true` and a
`default([])` fallback as an idempotency/skip-check gate. This treats every lookup failure — a
genuine fresh install, an unreachable API server, or a transient blip against an already-Ready
cluster — identically as "no resources exist," which can trigger a disruptive action (e.g.
reprovisioning/rebooting already-live hosts) on what was actually just a transient error.

**Why:** Flagged (Major, CHANGES_REQUESTED) in PR #570
(`playbooks/tasks/configure_hardware_filter_hosts.yaml`). The accepted fix: check kubeconfig
existence via `stat` first to short-circuit the genuine fresh-install case, and retry with the
repo's standard `k8s_retries`/`k8s_delay` vars (defined in `defaults/k8s.yaml`, already used e.g. in
`playbooks/07-configure-discovery.yaml`) before failing loud — instead of silently swallowing the
error and treating it as "empty."

**How to apply:** Any new `k8s_info` task used as a gate before a disruptive/destructive action
should distinguish "genuinely doesn't exist yet" from "lookup failed" — don't collapse both into the
same `ignore_errors` + empty-default branch. Use the existing `k8s_retries`/`k8s_delay` vars rather
than inventing new retry parameters.

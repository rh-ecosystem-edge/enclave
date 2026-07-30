---
title: CodeRabbit security checklist for new GitHub Actions workflows
tags: [ci, github-actions, coderabbit, security]
updated: 2026-07-16
---

Any new `.github/workflows/*.yml` file gets checked against a consistent set of security findings.
Pre-empting these before opening the PR saves a full review round-trip.

**Why:** PR #577 (`.github/workflows/cherry-pick.yml`, a new self-hosted-runner workflow triggered
by PR comments) got 6 CodeRabbit findings in one pass, all required before `@coderabbitai approve`:
1. Missing job-scoped `permissions:` — add `permissions: {}` at the workflow level, then grant only
   what each job needs.
2. Missing `concurrency:` guard (scoped per-PR/per-ref) to prevent overlapping runs.
3. Writing untrusted text (e.g. a PR body) to `$GITHUB_OUTPUT` without base64-encoding it —
   delimiter-injection risk.
4. Unencoded, attacker-influenced values (e.g. a branch name) interpolated directly into a URL or
   shell command.
5. `actions/checkout` (or any third-party action) not pinned to a full commit SHA.
6. A destructive operation (e.g. deleting a remote branch) happening before the operation it depends
   on is confirmed successful, rather than after.

**How to apply:** Before opening a PR that adds or substantially changes a GitHub Actions workflow,
check it against this list directly rather than waiting for CodeRabbit's first pass to enumerate
them one by one.

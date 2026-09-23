---
title: Never pass --debug to the jira CLI
tags: [security, jira, cli]
updated: 2026-07-16
---

The `jira` CLI (used per the project's jira-task-management workflow) prints the Basic Auth header
— email + API token, base64-encoded — in plaintext to stdout/stderr when invoked with `--debug`.

**Why:** Hit while trying to resolve a Jira user's account ID via `jira issue assign --debug`. The
credential didn't leave the machine, but it was persisted into a session transcript, which is a real
exposure surface (transcripts get read, shared, or mined by tooling like this knowledge base).

**How to apply:** Never add `--debug` to a `jira` CLI invocation. If you need to debug a `jira` CLI
issue, find another way to introspect it (e.g. check `~/.config/.jira/.config.yml` for the account
setup, or use a non-debug verbose flag if one exists) rather than the auth-header dump.

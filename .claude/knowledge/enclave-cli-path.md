---
title: enclave CLI lives in ~/.local/bin — must be added to PATH explicitly
tags: [enclave-cli, ansible, path]
updated: 2026-07-16
---

The `enclave` CLI is installed via `uv tool install`, which places the binary in `~/.local/bin` — a
directory that is NOT on PATH in the Ansible execution environment by default. This is unlike
`oc`/`oc-mirror`/`helm`, which live in `{{ workingDir }}/bin` and are already covered by existing
`environment:` blocks.

**Why:** PR #538 fixed `enclave reconcile operator-versions` failing with "command not found" in
`playbooks/tasks/configure_operator.yaml` by adding `$HOME/.local/bin` to the `PATH` env var in all
7 top-level playbooks. Current form (e.g. `playbooks/05-operators.yaml`):
`PATH: "{{ workingDir }}/bin:{{ lookup('env', 'HOME') }}/.local/bin:{{ lookup('env', 'PATH') }}"`.

**How to apply:** Any new playbook or `environment:` block that shells out to `enclave` needs this
same `$HOME/.local/bin` PATH entry — it's easy to forget since `{{ workingDir }}/bin` alone looks
sufficient by analogy with the other CLI tools, but `enclave` isn't installed there.

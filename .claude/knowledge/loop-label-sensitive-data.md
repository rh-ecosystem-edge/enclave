---
title: Loop over host/BMC objects needs loop_control.label to pass the sensitive-data check
tags: [ansible, coderabbit, ci]
updated: 2026-07-16
---

CodeRabbit's "No-Sensitive-Data-In-Logs" pre-merge check fires whenever a `loop:` iterates over a
host/BMH/credential-bearing object without a `loop_control.label`, because Ansible's default loop
output dumps the whole item (including fields like `redfishPassword` or BMC hostnames) into the
log. It also fires on plain `debug`/task output that logs a full host object directly.

**Why:** Flagged independently on unrelated PRs — PR #552 (`playbooks/07-configure-discovery.yaml`,
logging BMH `metadata.name`/`errorMessage`) and PR #554 (`playbooks/03-deploy.yaml`, logging the
full `agent_host` object containing `redfishPassword`/BMC hostnames). Both were fixed the same way:
add `loop_control.label: "{{ agent_host.name }}"` (or the equivalent identifying field) to the loop.

There is one accepted exception: logging an **operator-chosen hostname** (not a secret) directly is
fine and doesn't need redaction — cited as precedent in PR #570 against the same check, pointing at
existing examples in `playbooks/tasks/configure_hardware_ironic_boot.yaml` ("Display boot status",
`"Node {{ agent_host.name }} ({{ node_uuid }}) boot initiated"`) and
`configure_hardware_ironic_wait.yaml`. CodeRabbit accepted the precedent rather than requiring a
code change.

**How to apply:** Any new `loop:` over a host/BMH/credential-bearing object needs
`loop_control.label` set to a non-sensitive identifying field (e.g. `.name`) before opening a PR —
don't wait for CodeRabbit to catch it. If a similar check fires on a `debug`/log task that only
prints an operator-chosen hostname or UUID (not credentials), it's fine to push back citing the
precedent above rather than restructuring the log line.

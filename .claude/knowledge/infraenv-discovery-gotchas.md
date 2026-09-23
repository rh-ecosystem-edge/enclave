---
title: InfraEnv discovery — ISO readiness race and interfaceName schema decision
tags: [assisted-installer, infraenv, schema]
updated: 2026-07-16
---

Two settled, non-obvious facts about InfraEnv/discovery handling in
`playbooks/07-configure-discovery.yaml`, from PR #552 and PR #553.

## `status.isoDownloadURL` being set does not mean the image is servable

Provisioning a BMH immediately after an InfraEnv reports `isoDownloadURL` can still hit a transient
HTTP 503 from the Assisted Image Service, because the URL being populated doesn't guarantee the
service is actually ready to serve that ISO. Ironic then deprovisions/retries (~5 min), which can
miss the discovery polling window. The fix adds an explicit readiness check (poll for HTTP 200) in
addition to waiting for `isoDownloadURL`, and doubles the retry budget (`retries: 40` → `80`).

**How to apply:** Don't treat `isoDownloadURL` presence alone as "ready to boot" anywhere else this
pattern might get copied — add the same HTTP-200 readiness poll.

## `interfaceName` schema pattern is intentionally alphanumeric-only

`schemas/definitions.yaml`'s `interfaceName` pattern (`^[a-zA-Z0-9]+$`) has been challenged twice by
CodeRabbit, which suggested loosening it to allow hyphens/dots. Both times, reviewer rporres
confirmed RHEL predictable network interface naming is alphanumeric-only and the suggestion was
withdrawn.

**How to apply:** If this loosening is suggested again (by CodeRabbit or otherwise), it should be
rejected on the same grounds rather than re-investigated from scratch.

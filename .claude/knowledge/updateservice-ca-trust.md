---
title: UpdateService CA trust — chain completion and partner overlay ConfigMap gotcha
tags: [tls, certificates, updateservice, quay, plugins]
updated: 2026-07-16
---

Two related, non-obvious facts about how `playbooks/tasks/trust_quay_registry_ca_for_image_config.yaml`
and `mirror_registry.yaml` feed CA trust into the UpdateService operator.

## Externally-issued Quay certs need chain completion

When Quay's route uses an externally-issued certificate (e.g. Let's Encrypt/ZeroSSL) instead of the
OpenShift Ingress Operator's self-signed cert, the TLS handshake returns an incomplete chain
(leaf + intermediate, no root). UpdateService pods then crash on verification unless Enclave
completes the chain by fetching the root from the system trust store. This fix was accidentally
reverted once (commit `aef38d2`, PR #533) and had to be re-added (`6a8a35f`) after someone
re-investigated the crash from scratch — check git blame on this task before assuming the fix is
already correctly in place.

## Partner overlays must merge into the trust ConfigMap, not replace it

If a partner overlay (e.g. IBM's) sets `image.config.openshift.io/cluster`'s
`additionalTrustedCA.name` to point at its *own* ConfigMap (e.g. `mirror-registry-ca`) instead of
merging its CA into Enclave's `quay-registry-ca`, and that new ConfigMap lacks the
`updateservice-registry` key, the UpdateService operator silently stops mounting the CA bundle in
new pods and they crash.

**Why:** This exact failure mode drove PR #546, which made the ConfigMap name configurable
(`quayRegistryCAConfigmapName` in `defaults/deployment.yaml`) with an inline comment warning partner
overlays about the required key.

**How to apply:** Any partner/overlay integration that needs its own CA trusted must merge into the
configured trust ConfigMap (including the `updateservice-registry` key) rather than swapping in a
replacement ConfigMap.

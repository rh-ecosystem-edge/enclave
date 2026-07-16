---
title: Disconnected addon-plugin registries — two conventions and an installOperators gotcha
tags: [plugins, disconnected, mirroring, quay, mce]
updated: 2026-07-16
---

There are two distinct, non-obvious ways an addon plugin's operator images get mirrored in
disconnected mode. Mixing them up wastes real debugging time.

**Convention A (self-declared):** the plugin declares its own `registries:` block in
`plugins/<name>/plugin.yaml` (currently `lvms`, `nvidia-gpu`, `odf`, `vast-csi`, `osac`,
`openshift-ai`). This drives `oc-mirror` via the standalone per-plugin two-hop flow in
`playbooks/tasks/mirror_plugin.yaml` (Landing Zone hop, then a second hop into in-cluster Quay
Enterprise) at `make deploy-plugin` time.

**Convention B (pre-baked):** the operator's registry entries are hardcoded as static
`[[registry]]`/`[[registry.mirror]]` TOML blocks directly in
`operators/multicluster-engine/tasks.yaml` (confirmed for `rhacm2`, `container-native-virtualization`,
plus core OCP registries), installed once when MCE is configured — no `registries:` block needed in
that plugin's own `plugin.yaml` (confirmed for `cnv`, `rhbk`/rh-sso-7, `authorino`, `aap`,
`trust-manager`).

**The gotcha:** `playbooks/tasks/patch_mce_registries.yaml` re-patches the live `custom-registries`
ConfigMap in the `multicluster-engine` namespace for any plugin with its own `registries:` block
(Convention A), gated only on `plugin.registries is defined` + disconnected mode — not on
`installOperators`. Its dedup logic (`rejectattr('location', 'in', _toml_locations)`) makes this a
silent no-op for plugins whose entries are already in the Convention-B static list (e.g. `lvms`,
`odf`), but for a brand-new plugin not already in that static list, it's a first-time real mutation
of a live cluster ConfigMap — an untested code path relative to plugins that only ever hit the no-op
branch.

**Separately:** for an addon-only plugin (`installOperators: false`), `deploy_plugin.yaml` still runs
the LZ-hop and Quay-Enterprise-hop `oc-mirror` pushes unconditionally whenever `plugin.registries` is
defined and disconnected mode is on — only the downstream "apply manifests"/"install operators" steps
are skipped. `installOperators: false` does NOT make a plugin's disconnected mirror path
lighter-weight or less failure-prone; the mirror push (the flakiest observed step) still runs.

**How to apply:** When investigating a new addon plugin's disconnected-mirror failures, check both
`plugin.yaml`'s `registries:` block and whether its entries are already in
`operators/multicluster-engine/tasks.yaml`'s static list — a brand-new plugin needs an entry added
there to match convention. Don't assume `installOperators: false` narrows the failure surface.

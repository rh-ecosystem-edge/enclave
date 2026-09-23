---
title: A green e2e run doesn't validate every plugin — check the deploy matrix
tags: [ci, e2e, plugins]
updated: 2026-07-16
---

`.github/workflows/e2e-deployment.yml`'s connected/disconnected e2e jobs only run a "Deploy `<plugin>`
plugin" step for the plugins explicitly listed in the matrix (plus whichever plugin wins the
`storage-plugin` matrix slot). A plugin not listed there is never exercised by CI at all, no matter
how green the e2e run looks.

**Why:** Discovered while adding a new plugin — the only way to exercise it pre-merge was a temporary
`ENABLED_PLUGINS` override added just for that PR (and explicitly marked to revert before merge).

**How to apply:** Before trusting a green e2e run as validation for a specific plugin, check
`.github/workflows/e2e-deployment.yml`'s deploy matrix to confirm that plugin is actually deployed by
CI — don't assume "e2e passed" implies "this plugin was tested."

---
title: Config/defaults properties need matching schema updates
tags: [config, schema, review]
updated: 2026-07-16
---

When adding a new configuration property (in `config/**/*.yaml`, `defaults/*.yaml`, a plugin's
`defaults.yaml`/example config, etc.), also add or update the corresponding property definition
in the matching schema file under `schemas/` (e.g. `defaults/catalogs.yaml` → `schemas/catalogs.yaml`,
or a plugin-specific config schema) in the same change.

This is already called out in `AGENTS.md` ("Config and schemas" section) — this file exists
because the rule has been missed twice in practice, in ways worth knowing about.

**Why:** Reviewer rporres flagged this exact gap twice independently:
- PR #561: added `fleet_rh_operator_catalog` to `defaults/catalogs.yaml` without updating its schema.
- PR #590: added `osacChartVersion` to `config/plugins/osac.example.yaml` without updating the OSAC
  plugin config schema.

Both were caught in review rather than before submission.

**How to apply:** Whenever editing a file under `config/`, `defaults/`, or a plugin's
example/default config, grep `schemas/` for the sibling schema and add the new key there before
considering the change complete. Run `make -f Makefile.ci validate-json-schema` before opening
the PR. If unsure which schema file governs a given config file, check the `validate-plugins`/
`validate` targets in `Makefile.ci` rather than skipping the schema update.

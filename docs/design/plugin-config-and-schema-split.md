# Design doc: Per-plugin configuration and schema split

## Author/date

rporresm / 2026-05-25

## Tracking JIRA

OSAC-922

## Problem Statement

Three problems converge:

1. **Schema bloat.** `schemas/variables.yaml` is a single file that must grow every time a plugin
   adds configuration. With `additionalProperties: false` it is also a gate: adding a property
   anywhere requires touching the shared schema.
1. **External plugins configuration is blocked.** A plugin delivered outside this repo cannot add
   its own configuration variables without a PR here. The monolithic schema has no seam for extension.
1. **Plugin defaults are unvalidated.** The `defaults:` field in `plugin.yaml` is a free-form
   object. We validate every other defaults file with a JSON Schema; plugin defaults get none.

## Goals

- Plugin configuration lives in `config/plugins/<name>.yaml`, loaded automatically when a
  matching plugin directory exists — no manual wiring.
- A plugin can ship `schemas/config.yaml` and `schemas/defaults.yaml`; both are validated
  automatically. No schema file → no validation (opt-in).
- Plugin defaults can live in `plugins/<name>/defaults.yaml` instead of the `defaults:` field in
  `plugin.yaml` (cannot have both).
- Plugins can opt out schema validation by not adding validation schemas.
- `schemas/variables.yaml` is replaced by three focused schemas, one per config file
  (`global.yaml`, `cloud_infra.yaml`, `certificates.yaml`), each with
  `additionalProperties: false`. Plugin configuration is explicitly outside those schemas, so a
  plugin opting out of schema validation does not weaken the strictness of the core config
  validation.

## Non-objectives

- Migrating all existing plugins. Only `lvms` is migrated as a worked example.
- Removing `defaults:` field support from `plugin.yaml` (kept for backward compatibility until we
migrate all the plugins to the new system).

## Proposal

### Config loading (`playbooks/common/load-vars.yaml`)

After user config files are loaded, discover all `plugins/*/` directories and stat
`config/plugins/<name>.yaml` for each. Load only those that exist. Files in `config/plugins/`
with no matching plugin directory are silently ignored — external plugins just work by dropping
their config file in the right place.

### Plugin defaults file (`plugins/<name>/defaults.yaml`)

An alternative to the `defaults:` field. The bash validator and the Ansible deploy/validate tasks
both enforce mutual exclusion: having both is an error caught at CI time and at deploy time.

### Per-plugin schema validation (`defaults_schema_validation.yaml`)

All schema validation is CI-time only, run via `validate-schema.yaml`. There is no deploy-time
schema check — the playbooks trust that CI has already validated the config.

For every plugin directory:
- If `schemas/defaults.yaml` exists: validate the plugin's defaults (from `defaults.yaml` or the
  `defaults:` field) against it.
- If `schemas/config.yaml` exists and `config/plugins/<name>.yaml` exists: validate the config
  file against it.
- If `test-fixtures/schemas/(defaults|config)/(valid|invalid)/` exist: run fixture tests —
  valid fixtures must pass, invalid fixtures must fail.

### Schema split (`schemas/variables.yaml` → three files)

`config_schema_validation.yaml` now validates each config file against its own schema instead
of merging all three and running one validation. Each schema keeps `additionalProperties: false`;
plugin properties (e.g. `lvmsConfig`) are removed — they live in plugin schemas.

### Property prefix rule (`validate_plugins.sh`)

Since all plugin variables land in the same Ansible variable namespace, top-level properties in
any plugin schema must start with a prefix derived from the plugin name. Single-word names
(`lvms`) → one prefix. Hyphenated names (`vast-csi`) → three variants: `vast_csi`, `vastCsi`,
`vastCSI`. This prevents property collisions across plugins without a central registry.

### Name = directory rule (`validate_plugins.sh`)

The `name:` field in `plugin.yaml` must exactly match the directory name. Plugin names are
therefore unique by filesystem constraint.

## Alternatives considered

**Remove additionalProperties inside variables.yaml**: It's a regression over the current status
and doesn't solve all the problems that we have.

**Plugin config inside `plugin.yaml`.**: Plugins are code. Plugin files must be treated as read-only
and configuration must be outside.

## Milestones

1. Migrate one plugin as part of the initial implementation of this design doc.
1. Migrate the rest of the plugins.
1. Remove defaults plugin property inside plugin.yaml

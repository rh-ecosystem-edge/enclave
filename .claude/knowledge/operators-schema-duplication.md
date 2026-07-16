---
title: schemas/operators.yaml and schemas/plugin.yaml duplicate the operator definition and drift
tags: [schema, operators, plugins]
updated: 2026-07-16
---

`schemas/operators.yaml` and `schemas/plugin.yaml` each define their own separate, independently
maintained `operator` JSON-schema object (not a shared `$ref`), even though plugin operators flow
through the same runtime logic (`playbooks/tasks/configure_operator.yaml` via `deploy_plugin.yaml`'s
loop). Adding a property to one does not add it to the other.

**Why:** PR #471 added `connected_auto` (and earlier, `seed`) to `schemas/operators.yaml`'s operator
definition but not to `schemas/plugin.yaml`'s — so a plugin trying to set `connected_auto: true` (or
`seed`) on one of its operators fails schema validation (`additionalProperties: false`) even though
the runtime would honor it. As of this writing, `schemas/plugin.yaml` still lacks both properties
(confirm with `grep -n "seed\|connected_auto" schemas/plugin.yaml`); a fix exists on branch
`feature/plugin-operator-connected-auto-schema` (PR #597, unmerged).

A discussed but not-yet-implemented follow-up: extract the shared shape into
`schemas/definitions.yaml` (which already backs cross-schema `$ref`s like `ipv4Address` via
`combine(schema_definitions, recursive=True)` in
`playbooks/validation/tasks/defaults_schema_validation.yaml`), and have both files `$ref` it, keeping
only the `namespace`-required constraint local to `schemas/operators.yaml` via `allOf`.

**How to apply:** Any new operator-level property added to `schemas/operators.yaml` should also be
added to `schemas/plugin.yaml`'s operator definition (or vice versa) until the two are unified behind
a shared `$ref` — otherwise the same plugin-schema-validation gap will recur for every new property.

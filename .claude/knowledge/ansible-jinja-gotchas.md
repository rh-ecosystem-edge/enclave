---
title: Ansible/Jinja footguns seen more than once in this codebase
tags: [ansible, jinja]
updated: 2026-07-16
---

Three unrelated but recurring pitfalls in this repo's Ansible/Jinja-heavy playbook style.

## `assert.success_msg` is templated eagerly, even on the failing path

`ansible.builtin.assert` templates `success_msg` *before* deciding whether `that` passed, so if
`success_msg` indexes into a possibly-empty registered variable (e.g. `resources[0].metadata.name`
when `resources` is `[]`), Ansible crashes with an internal templating error instead of surfacing
the clean `fail_msg`. Hit in PR #587 (`operators/quay-operator/clair_validations.yaml`) and fixed
with a ternary guard: `{{ x[0].metadata.name if x | length > 0 else 'unknown' }}`.

**How to apply:** Any `assert` whose `success_msg` indexes a registered variable that could be empty
on the failure path needs the same ternary guard.

## YAML folded-scalar (`>-`) continuation-line indentation

A `>-` folded scalar preserves a literal newline for a continuation line that's indented *more* than
its siblings, instead of collapsing it to a space — this can silently insert a stray newline
mid-Jinja-expression. Keep folded-scalar continuation lines exactly aligned with each other.

## `selectattr()`'s second argument must be a Jinja test name

Not an arbitrary nested filter-chain string — e.g. `selectattr('status.conditions',
'selectattr("type", ...)')` is invalid. Prefer a simple loop or `json_query` over a nested
`selectattr` chain when the condition is more than one level deep.

## Jinja "truthy string" footgun in `when:`/ternaries

A fact or variable holding the *string* `"False"` is truthy in Jinja. Any variable used in a `when:`
or ternary needs an explicit `| bool` cast at the point of use — casting only where the fact is
*defined* isn't enough if it's consumed as a raw variable elsewhere. Reviewer eurijon caught the
same missing `| bool` in three separate spots in one PR (`configure_mch.yaml:20`,
`configure_operator.yaml:85,109`).

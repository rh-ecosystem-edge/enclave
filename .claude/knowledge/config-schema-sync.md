---
title: Config/defaults properties need matching schema updates
tags: [config, schema, review]
updated: 2026-07-16
---

AGENTS.md's "Config and schemas" section already states the rule (new `config/`/`defaults/`
properties need a matching `schemas/` update in the same PR, checked by
`make -f Makefile.ci validate-json-schema`). This file exists only to record *why* the rule is
there and how it's been missed in practice, despite being written down — don't restate the rule
here, refer to AGENTS.md for it.

**Why:** Reviewer rporres flagged this exact gap twice independently despite the AGENTS.md rule
already existing — once for a new default operator catalog variable, once for a new plugin config
property — both caught in review rather than before submission.

**How to apply:** Take this as a sign that "it's documented" isn't sufficient on its own — when
editing a file under `config/`, `defaults/`, or a plugin's example/default config, actively go
look for the sibling schema file rather than assuming you'd naturally remember to.

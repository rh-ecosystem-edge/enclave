# Shared knowledge base

Non-obvious facts, conventions, and decisions about this project that are worth every
contributor's Claude instance knowing — so nobody has to re-derive them (and re-spend the
tokens) from scratch. This is checked into git: it's shared across everyone working in this
repo, unlike a personal `~/.claude` memory which only one person's Claude sees.

## When to read this

Before spending significant tokens investigating something that feels like it might already
be a known gotcha, convention, or settled decision (e.g. "does X need updating when I touch
Y", "has this scope question already been answered"), skim the index below first.

## When to add to this

When you (Claude) discover something during a session that:
- isn't already derivable by reading the code/config/docs directly, AND
- would save a meaningful amount of investigation for the *next* person's Claude session

... add a new file here rather than letting it live only in your private memory or the
conversation transcript. Good candidates: recurring review feedback that generalizes ("every
PR that touches X also needs Y"), scope decisions on tickets/issues that could otherwise be
re-litigated, non-obvious cross-file relationships, gotchas that have bitten more than one PR.

Do NOT put here: information specific to one person's workflow or preferences (that belongs in
their personal memory), anything already stated in `AGENTS.md` or derivable by reading the
code, or ephemeral in-progress task state.

## Format

One file per topic, kebab-case filename, with this frontmatter:

```markdown
---
title: Short title
tags: [config, schema]
updated: 2026-07-16
---

Statement of the fact/rule/decision, then:

**Why:** the reasoning or incident that established it.
**How to apply:** what this should change about future work.
```

There's no separate index file — `ls .claude/knowledge/` or `grep -rl <topic> .claude/knowledge/`
is the way to find what's already here before investigating something from scratch.

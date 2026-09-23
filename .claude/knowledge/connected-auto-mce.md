---
title: connected_auto is the sanctioned way to get auto install-plan approval — MCE needs a second patch
tags: [operators, mce, acm]
updated: 2026-07-16
---

`connected_auto` (`defaults/operators.yaml` + `schemas/operators.yaml`) is the generic, sanctioned
mechanism for automatic OLM install-plan approval, consumed via a single `_use_automatic_approval`
fact in `playbooks/tasks/configure_operator.yaml`. It replaced an earlier, superseded PR (#467) that
hardcoded MCE-specific approval logic.

**Why:** ACM's `MultiClusterHub` controller manages a *separate* MCE subscription object in
`operators/advanced-cluster-management/configure_mch.yaml`, which independently hardcodes
`installPlanApproval: Manual`. Setting `connected_auto` on the MCE operator entry alone does nothing
for MCE unless `configure_mch.yaml` is also patched — this was done explicitly in PR #471 as a
workaround, pending a proper fix filed with the ACM team upstream.

**How to apply:** If MCE's install-plan approval behavior needs to change again, remember there are
two places to update: the operator's `connected_auto` setting AND
`operators/advanced-cluster-management/configure_mch.yaml`'s hardcoded `installPlanApproval`. For any
other operator, `connected_auto` alone is sufficient — MCE is the one exception because of ACM's
separate controller-managed subscription.

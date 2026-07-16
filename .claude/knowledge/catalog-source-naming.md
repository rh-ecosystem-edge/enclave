---
title: mirror_rh_operator_catalog vs fleet_rh_operator_catalog — don't unify
tags: [catalogs, operators, upgrades]
updated: 2026-07-16
---

`defaults/catalogs.yaml` has two catalog-source names that look like redundant duplication but are
not interchangeable:
- `mirror_rh_operator_catalog` = `"redhat-operator-index"` — management cluster.
- `fleet_rh_operator_catalog` = `"mirror-redhat-operators"` — spoke/fleet clusters, deployed via ACM
  policy.

**Why:** Commit `c973e73` renamed the management-cluster catalog to `"mirror-redhat-operators"` for
naming consistency; commit `33f9781` reverted it because it broke upgrades from 0.1.0 — existing
operator `Subscription` objects reference catalog-source names immutably, and writing migrations for
every default operator wasn't judged worth it. But `files/catalogsource-configuration/10-policy.yaml.j2`
and `plugins/openshift-ai/files/10-policy-operators.yaml.j2` still needed the old
`"mirror-redhat-operators"` name for spoke-cluster 0.1.0 compatibility, so it was extracted into the
`fleet_rh_operator_catalog` variable (PR #561) instead of being hardcoded.

**How to apply:** Don't "clean up" the apparent duplication between these two variables — doing so
would silently break spoke-cluster upgrade compatibility from 0.1.0. If a new default operator
catalog name needs to change, check whether existing `Subscription` objects reference it immutably
before assuming a rename is safe.

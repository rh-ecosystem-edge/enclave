# Design doc: Rails-style versioned upgrade migrations

## Author/date

rporresm / 2026-07-09

## Tracking JIRA

[OSAC-2322](https://redhat.atlassian.net/browse/OSAC-2322)

## Problem Statement

The current upgrade process in `playbooks/upgrade.yaml` runs every migration task on every
upgrade. There is no record of which migrations have already been applied to a given installation.
Idempotency of individual tasks is the only safeguard — every upgrade retries work that was
already done in previous upgrades.

This creates several problems:

* As the migration list grows, upgrade time increases unnecessarily. Every installation
  re-executes every migration ever written, regardless of its age.
* There is no way to tell, by looking at an installation, which migrations have been applied
  and which have not.
* There is no mechanism to add a migration that applies only to installations upgrading from a
  specific version range. All migrations must be written to be safe to run unconditionally on all
  installations at all times.
* The migration list (`playbooks/tasks/migrations.yaml`) is a manually maintained, ordered list
  of `include_tasks` calls with ad-hoc `when:` conditions. Adding a migration requires editing
  this shared file, creating merge conflicts and no consistent ordering guarantee.

## Goals

* Introduce a versioned migration system where each migration is a standalone task file with a
  UTC timestamp prefix (e.g., `20260709143200_description.yaml`).
* Track the migration state of each installation using a control file on the Landing Zone host
  (`~/.config/enclave/migration_version`). Only migrations newer than the control file timestamp
  are run on upgrade.
* Introduce a `repo_version` file committed to the repository (updated automatically on every
  merge to main) that serves as the authoritative version reference. The file contains a single
  UTC timestamp (`YYYYMMDDHHMMSS`) and is included in distributed tarballs, so it is available
  even without git history.
* Bootstrap writes the control file on successful completion, so fresh installations skip all
  migrations on their first upgrade.
* Add CI validation that enforces: migration timestamps are not in the future, timestamps are
  unique across all migration files, and existing migration files are never modified after being
  merged.

## Non-objectives

* Rollback support. Ansible migrations are generally not reversible, and this design does not
  attempt to implement a `downgrade` mechanism.
* Dependency declarations between individual migrations. Ordering is strictly chronological
  (by timestamp, alphabetical within the same timestamp).
* Automatic migration of installations that predate this system. Those installations will have no
  control file; the upgrade process will run all existing migrations, which is safe given their
  existing idempotency guarantees.

## Proposal

### `repo_version` — the repository's version stamp

A file named `repo_version` is committed to the repository root. It contains a single UTC
timestamp in `YYYYMMDDHHMMSS` format. A CI job updates this file and commits it back to `main`
on every merge. Because the file is committed, it is present in both git clones and distributed
tarballs.

### Migration file naming

Each migration file is named `YYYYMMDDHHMMSS_description.yaml` where the timestamp is set by
the developer writing the migration (in UTC, at the time of writing). CI enforces:

1. All migration timestamps are ≤ the current UTC date (no future-dated files).
1. No two migration files share the same timestamp prefix (uniqueness).
1. No file already merged to `main` is modified in a subsequent PR (immutability).

Ordering within the same timestamp is alphabetical on the full filename, providing a stable
secondary sort.

### Control file on the Landing Zone host

After a successful upgrade, the upgrade process writes the content of `repo_version` to
`~/.config/enclave/migration_version` on the LZ host. This file is the authoritative record of
the migration state of the installation.

After a successful bootstrap, bootstrap also writes the control file, seeding it with the current
`repo_version`. This means a freshly bootstrapped installation will skip all existing migrations
on its first upgrade.

If the control file is absent (pre-migration-system installations), the upgrade process treats
the installation as if no migrations have ever been run and applies all migrations in order.

### Migration runner

The migration runner (`playbooks/tasks/migrations.yaml`) is rewritten to:

1. Read `repo_version` from the tarball.
1. Read the LZ control file (or use `00000000000000` if absent).
1. Discover all files in `playbooks/tasks/migrations/` matching the `YYYYMMDDHHMMSS_*.yaml`
   pattern.
1. Select those whose timestamp is strictly greater than the control file timestamp.
1. Execute them in ascending order (chronological, alphabetical within same timestamp).
1. Write the `repo_version` timestamp to the LZ control file.

Individual migration files are self-contained and own their own `when:` conditions (e.g.,
"only run if disconnected"). The runner applies no conditions beyond timestamp comparison.

### Existing migrations

The four existing migration files and the `trust_quay_registry_ca_for_image_config.yaml` task
(currently included from migrations but living outside the directory) are renamed with their
original commit timestamps and moved into `playbooks/tasks/migrations/`. Their existing `when:`
conditions move into the files themselves. A utility file (`operator_catalog_source.yaml`) that
is called by other migrations as a subroutine is moved to `playbooks/tasks/` since it is not a
migration itself.

## Alternatives considered

* **Hash-based tracking (like Alembic's revision IDs).** Alembic uses a directed acyclic graph of
migrations with opaque revision IDs and explicit `down_revision` pointers. This enables
independent migration branches and merge migrations. It was considered and rejected because enclave
migrations are always linear (one upgrade path from one release to the next), and the complexity
of a DAG-based system is not justified. Timestamp ordering with alphabetical tiebreaking is
sufficient.

* **A database or structured file as the state store.** Using a YAML or JSON file on the LZ that
records every applied migration by name was considered. A flat timestamp in a single file was
chosen instead because: (1) the comparison logic is trivial, (2) the file is human-readable and
auditable at a glance, and (3) it requires no parsing or schema beyond reading a single string.

* **CI-generated `repo_version` injected at tarball build time (not committed).** The existing
`.version` file pattern in the tarball build CI writes a version transiently without committing.
This was rejected because a committed file is required: users who clone the repository (not the
tarball) need `repo_version` to be present, and a CI-only file would not survive `git clone`.

## Milestones

1. Single PR implementing the idea described in this doc.
2. Implement a CI workflow that runs daily e2e testing the migrations from 0.1.0 -> 0.1.1 -> repo HEAD. This will likely require a dedicated design doc for it so we keep it out of the scope of this doc.

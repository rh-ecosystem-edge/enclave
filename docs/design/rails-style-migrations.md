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
* Track the migration state of each installation using an append-only control file on the
  Landing Zone host (`~/.config/enclave/migration_tasks`). The file records every migration
  that has been successfully applied, one filename per line. Only migrations absent from the
  control file are run on upgrade.
* Bootstrap writes the control file on successful completion, seeding it with all existing
  migration filenames so that fresh installations skip them on their first upgrade.
* If a migration fails, the upgrade stops immediately. The failed migration is not recorded in
  the control file, so it is retried on the next upgrade run.
* Add CI validation that enforces: migration timestamps are strictly greater than the latest
  timestamp already present in the repo (monotonic ordering), timestamps are unique across all
  migration files, and existing migration files are never modified after being merged.

## Non-objectives

* Rollback support. Ansible migrations are generally not reversible, and this design does not
  attempt to implement a `downgrade` mechanism.
* Dependency declarations between individual migrations. Ordering is strictly chronological
  (by timestamp, alphabetical within the same timestamp).
* Automatic migration of installations that predate this system. Those installations will have no
  control file; the upgrade process will run all existing migrations, which is safe given their
  existing idempotency guarantees.

## Proposal

### Migration file naming

Each migration file is named `YYYYMMDDHHMMSS_description.yaml` where the timestamp is set by
the developer writing the migration (in UTC, at the time of writing). CI enforces:

1. The new migration timestamp is strictly greater than the maximum timestamp already present in
   `playbooks/tasks/migrations/` (monotonic ordering). This also rejects future-dated files as a
   side effect. Backport migrations are intentionally disallowed — a fix for an older migration
   is written as a new migration with a current timestamp.
1. No two migration files share the same timestamp prefix (uniqueness).
1. No file already merged to `main` is modified in a subsequent PR (immutability).

Ordering within the same timestamp is alphabetical on the full filename, providing a stable
secondary sort.

### Control file on the Landing Zone host

`~/.config/enclave/migration_tasks` is an append-only file, one applied migration filename
per line:

```
20260601120000_foundation_plugin_catalog_sources.yaml
20260615090000_setup_enclave_kubeconfig_symlink.yaml
20260709143200_trust_custom_ca.yaml
```

The file is the authoritative record of the migration state of the installation. It is
human-readable and auditable at a glance.

After a successful bootstrap, bootstrap writes all existing migration filenames (sorted) to the
control file, seeding it so that fresh installations skip all existing migrations on their first
upgrade.

If the control file is absent (pre-migration-system installations), the upgrade process treats
the installation as if no migrations have ever been run and applies all migrations in order.

### Migration runner

The migration runner (`playbooks/tasks/migrations.yaml`) is rewritten to:

1. Read the control file (or treat as empty if absent) and build the set of already-applied
   migration filenames.
1. Discover all files in `playbooks/tasks/migrations/` matching the `YYYYMMDDHHMMSS_*.yaml`
   pattern.
1. Subtract the applied set from the discovered set to produce the pending list.
1. Sort pending migrations ascending (chronological, alphabetical within same timestamp).
1. For each pending migration in order:
   - Execute the migration task file.
   - If it succeeds, append its filename to the control file.
   - If it fails, stop immediately. The control file reflects only what actually applied.

Individual migration files are self-contained and own their own `when:` conditions (e.g.,
"only run if disconnected"). The runner applies no conditions beyond set membership.

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

* **A single high-watermark timestamp as the state store.** Storing only the latest applied
migration timestamp was considered. An append-only file of all applied migration filenames was
chosen instead because: (1) it provides a full audit trail of what ran on an installation,
(2) pending detection is set subtraction rather than a timestamp comparison, making it robust
to any ordering edge case, and (3) it mirrors the Rails `schema_migrations` table model which
is well-understood.

* **A `repo_version` committed file as the version stamp.** A file updated by a CI bot commit on
every merge to main was considered as the reference timestamp for seeding the control file on
bootstrap. This was dropped in favour of deriving the seed directly from the migration files
themselves, eliminating the CI bot commit infrastructure entirely.

## Milestones

1. Single PR implementing the idea described in this doc.
2. Implement a CI workflow that runs daily e2e testing the migrations from 0.1.0 -> 0.1.1 -> repo HEAD. This will likely require a dedicated design doc for it so we keep it out of the scope of this doc.

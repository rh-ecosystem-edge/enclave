# Design doc: External mirror cache

## Author/date

rporresm / 2026-10-28

## Tracking JIRA

[OSAC-6929](https://redhat.atlassian.net/browse/OSAC-6929)

## Problem Statement

A disconnected Enclave installation always installs mirror-registry on the Landing Zone and
mirrors all content into it, at bootstrap (phase 02) and again on every `sync.sh`. Customers ask
to deploy and operate disconnected clusters from a registry they own instead
([GoRI #609](https://github.com/gori-project/GoRI/issues/609), parent
[GoRI #608](https://github.com/gori-project/GoRI/issues/608)).

1. **The LZ mirror is hardwired.** `install-config-disconnected.yaml.j2` hardcodes
   `imageDigestSources` to `{{ quayHostname }}:8443/...`, the ABI manifests and the applied
   CatalogSources are copied from the oc-mirror workspace, and `templates/registries.conf.j2` is a
   hand-maintained list of upstream namespaces mapped to `{{ quayHostname }}:8443`. There is no seam
   to point the cluster or the LZ at another registry.
1. **The prior art is not usable.** [enclave PR #34](https://github.com/rh-ecosystem-edge/enclave/pull/34)
   only pushes content to an external registry, which is never consumed. It has never been tested,
   duplicates what `playbooks/tasks/mirror_registry.yaml` does, and is embedded in the installation
   workflow, so the cache cannot be maintained outside an installation.
1. **There is no version to tell whether a cache fits.** Enclave is not delivered as a git
   repository and has no version file (and adding one, e.g. bumped on every merge, has been
   rejected in the past). Starting an installation or an upgrade against a cache that lacks content
   fails late, with errors that are hard to relate to the cache.
1. **Disconnected e2e is slow.** Every CI run installs a fresh LZ mirror and mirrors the full
   content from upstream.

## Goals

- Enclave can deploy and operate a disconnected cluster using a customer-owned registry (the
  "cache") instead of the Landing Zone mirror-registry.
- The cache is read-only for every enclave flow (bootstrap, sync, upgrade, plugin deployment).
- Enclave can tell, before changing anything, whether a cache is valid for the current enclave
  content and configuration, without depending on a version file.
- The cache operation and lifecycle are separate from an enclave installation.
- CI exercises the cache code path and uses it to speed up disconnected e2e.

## Non-objectives

- **Using the cache as an upstream source for the LZ mirror-registry.**
- **Non-image content from the cache.** Control binaries, Helm charts and host packages keep being
  fetched from upstream (see [Landing Zone pulls](#landing-zone-pulls)). OCI charts could live in
  the cache later; repo charts would need repackaging.
- **IBM content.** The IBM-side `icr.io` override and IBM content as cache units are a follow-up.
- **Cache pruning.** `build` only adds content (see [Upgrades](#upgrades)). The design keeps pruning
  possible: each unit's metadata records its canonical imageset and the list of images it
  mirrored, enough to drive a later `oc-mirror delete` or tag removal per unit. This includes
  removing cluster objects (IDMS/ITMS, CatalogSources) of units a config no longer requires. CI
  does not need it: snapshots are rebuilt from scratch weekly.
- **LZ redirection without a cache.** Applying the "LZ pulls go through the registry" rule to the
  LZ mirror-registry path requires moving ISO extraction after phase 02. Cache mode only.
- **Phase 02 as "build a local cache, then consume it".** One model for both modes, where LZ mode
  becomes a cache on the LZ. It changes phase 02 (per-unit runs, new object names) and needs a
  migration for existing installations to avoid duplicate IDMS/ITMS and extra MCO rollouts.
- **Long-lived CI cache host.** It avoids per-run restores, but a per-run build is still needed for
  key misses. To be revisited after measuring restore times.
- **Strict CI misses.** Failing PRs that miss without touching mirror inputs (which signals a
  broken publishing pipeline) instead of building on the host.

## Proposal

### Prerequisite

Stable, y-stream agnostic CatalogSource names
([enclave PR #977](https://github.com/rh-ecosystem-edge/enclave/pull/977)). Open point: oc-mirror
v2 appears to append the source tag to generated CatalogSource names (`cs-<targetCatalog>-<tag>`);
it must be confirmed that fresh installations get `cs-<index>`, and not only migrated clusters.

### Consumption model

When a cache is configured it is an **external, runtime dependency**:

- The LZ does not install mirror-registry and the disconnected mirror phase is skipped.
- The cluster (IDMS/ITMS, CatalogSources, install-config mirrors) points at the cache.
- The cache must stay reachable for the cluster lifetime: day 2, upgrades, node replacement.
- `bootstrap.sh`, `sync.sh`, `upgrade.sh` and `deploy-plugin` treat the cache as **read only**.

When no cache is configured, behaviour is unchanged.

**No mode switching.** The mode is fixed at bootstrap: an installation using an external cache
always uses it, and an installation using the LZ mirror never moves to a cache. `sync.sh`,
`upgrade.sh` and `deploy-plugin` refuse to run if the configured mode differs from the one the
installation was bootstrapped with.

The mode is recorded on the LZ following the `migrationTasksFilePath` pattern: a state file whose
path is a default in `defaults/deployment.yaml` (e.g.
`mirrorModeFilePath: "{{ workingDir }}/state/mirror-mode"`, content `lz` or `cache`), overridable
in config and covered by the schema.

- `bootstrap.sh` writes it in an early step (before any mirroring), not at the end like the
  migration control file, so a failed bootstrap that is re-run with a different mode is caught
  too. If the file exists with a different mode, bootstrap fails.
- `sync.sh`, `upgrade.sh` and `deploy-plugin` compare it with the configured mode and exit before
  changing anything on mismatch.
- A missing file means an installation that predates cache mode: treated as `lz`.
- Only the mode is recorded, not the cache endpoint. Every flow validates the cache it is pointed
  at, so moving a cache (new hostname, new registry) is allowed: the next `sync.sh` validates it
  and re-applies the units' IDMS/ITMS, updated in place since object names do not depend on the
  host.

### Configuration

New optional config (schema-validated):

- cache endpoint (host:port, optional namespace/prefix)
- credentials (secret)
- CA bundle used to trust the cache (no `tls-verify=false`)

### Cache maintenance

A separate entry point (working name `cache.sh`) owned by whoever operates the cache:

- `build` — mirror the content required by an enclave config into the cache, reusing the existing
  mirroring tasks (`mirror_registry.yaml` logic) rather than duplicating them.
- `verify` — the same check `bootstrap.sh` / `sync.sh` run (see
  [Validity](#validity-fingerprint--dry-run)), usable standalone.

`build` is the only writer. It is also what CI uses to create a cache.

It runs on any host with the enclave content and the installation's config, using the same config
layout as the LZ:

- `cache.sh build` — every unit the config requires (`enabled_plugins`, platforms, overrides). Used
  for bootstrap and upgrades.
- `cache.sh build --plugin <name>` — only that plugin's units (`catalog-<plugin>`,
  `images-<plugin>`), whether or not it is in `enabled_plugins`, reading
  `config/plugins/<name>.yaml` if present as `deploy_plugin.yaml` does. The cache-mode counterpart
  of `make mirror-plugin PLUGIN=<name>`.

`build` is idempotent and safe to re-run: for every requested unit it runs oc-mirror (which skips
images already in the destination, and restores any deleted after a previous build) and
republishes the unit's metadata image.

#### Reusing the mirroring code

`mirror_registry.yaml` is split, without behaviour change, into:

- **install registry** — mirror-registry install and Postgres migration (LZ mode only)
- **mirror one imageset** — oc-mirror one imageset to a destination and collect its
  `cluster-resources`

Phase 02 keeps a single combined run: it calls "mirror one imageset" once with the combined
imageset, so its behaviour, outputs and object names are unchanged. `cache.sh build` calls it once
per mirroring unit, which gives each unit its own `cluster-resources` without post-processing. One
mirroring code path, two callers.

#### Applying cluster resources

Today three consumers read the oc-mirror workspace or hardcode the LZ mirror:

| Consumer | When | Today |
|---|---|---|
| `mirror_registry_cluster_resources.yaml` (end of `mirror_registry.yaml`) | `sync.sh` | applies `cs-*.yaml` and `signature-configmap.yaml` from the workspace |
| `configure_ocp_abi.yaml` | bootstrap | copies `idms-oc-mirror.yaml`, `itms-oc-mirror.yaml`, signatures and `cs-*.yaml` from the workspace into ABI manifests |
| `install-config-disconnected.yaml.j2` | bootstrap | hardcodes `imageDigestSources` to `{{ quayHostname }}:8443/...` and trusts `quayCAPath` |

Applying is decoupled from mirroring:

- "Mirror one imageset" only mirrors and collects; the apply step moves out of
  `mirror_registry.yaml`.
- A **materialize cluster resources** step fills one mode-independent directory (e.g.
  `{{ workingDir }}/config/cluster-resources/`), rebuilt from scratch on every run:
  - LZ mode: copied from the oc-mirror workspace (same content as today).
  - Cache mode: resolve the units the config requires (shared render step), pull each unit's
    metadata image from the cache (exact key, else `<unit>:latest`, see
    [Validity](#validity-fingerprint--dry-run)), substitute the host/prefix placeholder with the
    configured endpoint.
- Consumers read only that directory:

| Entry point | Units | Action |
|---|---|---|
| `bootstrap.sh` | all required | copy IDMS/ITMS/CatalogSources/signatures into the ABI manifests; install-config `imageDigestSources` from the `ocp-<version>` unit IDMS, `additionalTrustBundle` from the configured cache CA |
| `sync.sh` (and before `upgrade.sh`) | all required | apply CatalogSources and signatures (as today), plus IDMS/ITMS in cache mode (below); wait for CatalogSources ready |
| `deploy-plugin` / `mirror-plugin` | the plugin's units | apply in the single MCO rollout, as today |
| Quay Enterprise seeding, LZ redirection | IDMS/ITMS | generate the per-run registries.conf and the persistent drop-in |

- The ABI copy matches `idms-*.yaml` / `itms-*.yaml` instead of fixed file names (one file per unit
  in cache mode).
- Units no longer required by the config are not removed from the cluster, consistent with the
  cache being additive and with LZ mode, where sync never removes CatalogSources or IDMS either.
  Removing a mirror entry still in use (installed operator, node on an older release) would break
  pulls; removal belongs with pruning (see [Non-objectives](#non-objectives)).
- In cache mode sync also applies the units' IDMS/ITMS. Today, in LZ mode, the LZ IDMS/ITMS only
  reach the cluster through the ABI manifests at install; repositories added later (e.g. a newly
  enabled plugin) are covered only by the Quay Enterprise IDMS/ITMS that `quay-disconnected`
  applies on every sync. That works, but in cache mode the cache must be a mirror for every
  repository, not only the ones present at install. Applying an unchanged IDMS does not trigger an
  MCO rollout.

### Cache metadata

Each build publishes one small metadata image per unit to the cache itself (e.g.
`enclave-cache/<unit>:<key>`). It is a plain container image (`FROM scratch`, one layer with the
files), not a custom OCI artifact type: enclave reads it with `oc image extract`, which it already
ships (no new binary such as `oras` on the LZ), and any registry accepts it. It contains:

- the cache key (see [Cache key](#cache-key)), metadata format version and build timestamp
- the canonical unit imageset and resolved catalog index digests (traceability)
- the list of images the unit mirrored (keeps a later per-unit pruning possible)
- oc-mirror `cluster-resources/` (IDMS, ITMS, CatalogSources, signature ConfigMaps) that enclave
  needs to configure the cluster, already named per unit (see
  [Outputs stored in the cache](#outputs-stored-in-the-cache))

The registry is the only server; no extra service, no version file in the repo.

### Validity: fingerprint + dry-run

No enclave version. Validity is defined by content.

- A cache is **config aware**, not enclave-version aware: validity depends on the enclave content
  *and* the deployment config (enabled plugins, platform/operator overrides).
- One cache can serve several enclave installations with different configs. Its content is the
  union of what they need.
- Content is split in **mirroring units**, each with its own key and metadata image (see
  [Cache key](#cache-key)).
- **The dry-run always decides.** Every product flow (`bootstrap.sh`, `sync.sh`, `deploy-plugin` /
  `mirror-plugin`) runs an oc-mirror dry-run against the cache for the units it needs. Any missing
  image fails early, before installation/upgrade starts. A matching key only proves a unit was
  built from the same inputs, not that its images are still in the registry (deleted or garbage
  collected after the build).
- **Keys** select the metadata image to use, drive CI snapshot lookup, and give diagnostics ("unit
  `catalog-odf` has no matching key"). A key mismatch is not a failure by itself: the cache may
  hold a superset (e.g. a wider operator version range).
- **Metadata lookup:** `build` tags each unit's metadata image with its key and also moves
  `<unit>:latest`. Consumers fetch the exact key if present, otherwise `<unit>:latest`; in both
  cases the dry-run is what makes the cache valid.
- To verify at implementation time: a mirror-to-mirror dry-run checks the destination registry for
  missing images. A mirror-to-disk dry-run does not (observed with oc-mirror v2 4.20: it only
  checks its local storage and never contacts the source).

Catalogs are referenced by floating tags (`redhat-operator-index:v4.20`). Once the cache is built,
it defines what those tags point to for every installation using it:

- The key hashes the tag as written. Computing it never resolves the tag upstream.
- The dry-run validates against the index stored in the cache, not the current upstream index,
  otherwise any newer upstream publish would report missing images.
- Catalog freshness changes owner. Today every `sync.sh` run re-resolves the tags and mirrors the
  latest upstream index into the LZ mirror. In cache mode `sync.sh` does not mirror, so catalog
  content is as fresh as the last `cache.sh build`.

### Cache key

#### Mirroring units

| Unit | Content | Today produced by |
|---|---|---|
| `ocp-<version>` | one OpenShift release payload (+ graph data) | core imageset `platform` |
| `catalog-core` | core operators from `redhat-operator-index` | core imageset `operators` |
| `catalog-<plugin>` | operators of one plugin from its intermediate index `redhat-operator-index-<plugin>` / `mirror-certified-operators-<plugin>` | core imageset (enabled plugins) and `mirror_plugin.yaml` (addon) |
| `images-core` | core `additionalImages` / `blockedImages` (ubi, argocd, vault) | core imageset template |
| `images-<plugin>` | plugin `additionalImages` incl. Helm-extracted images | `mirror_plugin.yaml` |

The plugin catalog target name is the same in the core and addon paths, so a plugin unit means the
same thing regardless of which path mirrors it.

Today the core imageset bundles OCP, core operators and all enabled plugins' catalogs in a single
file, so "core" is not a stable unit: enabling a plugin changes it. In cache mode oc-mirror runs
once per unit (see [Reusing the mirroring code](#reusing-the-mirroring-code)); phase 02 in LZ mode
keeps its single combined run.

OCP releases are keyed per release, not per channel range: bootstrap (`fresh=true`) mirrors only
the default version while `sync.sh` (`fresh=false`) mirrors the `openshift_versions` range, so the
same enclave and config render different core imagesets depending on the entry point.

#### Key contents

```text
key = hash(canonical unit definition + oc-mirror version + key schema version)
```

- **Canonical unit definition:** the structured, fully expanded unit content (after plugin operator
  collection and Helm extraction), serialized with sorted keys and sorted lists. Not the rendered
  template text: template whitespace or dict iteration order must not change the key.
- **oc-mirror version:** an oc-mirror upgrade invalidates the cache, on purpose.
- **Key schema version:** bumped when the key algorithm or unit semantics change.

Keys are computed by a shared render step (Python, see [Testing](#testing)) reused by `cache.sh`,
`bootstrap.sh`, `sync.sh` and `deploy-plugin`, never by hashing repo files (too broad: doc changes
invalidate; too narrow: misses Helm rendering).

#### Deployment-specific values (excluded from the key)

| Value | Where it leaks today | Rule |
|---|---|---|
| `quayHostname` / cache endpoint | skopeo destination of intermediate indexes; `sed` rewrite of `catalog:` to `<host>:8443/redhat/...`; oc-mirror destination | Normalize to a placeholder before hashing |
| Cache path prefix (customer namespace) | oc-mirror destination | Excluded; affects outputs only |
| Credentials / pull secrets / CA | authfiles | Excluded; never stored in metadata |
| `workingDir`, workspace paths, log paths | oc-mirror flags | Excluded |
| `fresh` | changes `openshift_versions` | Neutralized by per-release OCP units |
| oc-mirror tuning (`parallel-*`, retries, timeouts) | oc-mirror flags | Excluded |

Config values that legitimately change content and therefore select units/keys: `enabled_plugins`
(including the auto-added `storage_plugin`), `config/platforms.yaml`,
`config/plugins/<name>.yaml`, operator and catalog overrides.

#### Outputs stored in the cache

oc-mirror `cluster-resources/` (IDMS, ITMS, CatalogSources, signature ConfigMaps) embed the
destination host and prefix. Different installations may reach the same cache under different
names, so stored outputs use a placeholder for host/prefix that consumers substitute with their
configured endpoint.

Object names:

- **IDMS/ITMS** are named per unit at build time (e.g. `idms-<slice>-<unit>`). Consumers apply them
  as they are; the `*-plugin-<name>` renaming that `mirror_plugin.yaml` does today after each run
  is not needed in cache mode. Bootstrap and day-2 plugins get the same names, which is not the
  case today (bootstrap plugins land in the core IDMS).
- **CatalogSources** follow the stable convention from enclave PR #977: `cs-<index>` and
  `cs-<index>-<plugin>`, derived only from the target catalog name. `cache.sh build` uses the same
  templates, so it produces the same names and enclave keeps building them by convention. A
  catalog version bump does not rename them.

Object names differ between LZ mode and cache mode; this is fine because modes are never mixed
(see [Consumption model](#consumption-model)).

### Landing Zone pulls

With a cache configured, no image the LZ uses may bypass it. Today the LZ resolves three images
from the release payload and pulls them straight from upstream:

| Image | Where | Phase |
|---|---|---|
| `machine-os-images` (RHCOS ISO via `oc image extract`) | `download_content_single_version.yaml` | 01-prepare (`download-content`) |
| `ironic` | `deploy_ironic.yaml` | 07 / hardware setup / upgrade |
| `baremetal-operator` | `deploy_bmo.yaml` | 07 / hardware setup / upgrade |

All three are resolved with `oc adm release info quay.io/openshift-release-dev/ocp-release:<v>` and
pulled by digest, so they are already part of the `ocp-<version>` unit. No new cache units: the fix
is consumer side, redirecting the LZ to the cache using the same source→mirror mapping as the
cluster (see [Registry redirection on the LZ](#registry-redirection-on-the-lz)).

Ordering: with a cache the content exists before phase 01. Applying the same rule to the LZ
mirror-registry path (no cache) would require moving ISO extraction after phase 02; out of scope,
cache mode only.

**Decision:** non-image upstream fetches stay as they are and do not go through the cache. The LZ
keeps upstream access for:

- Control binaries from `mirror.openshift.com` (`oc`, `helm`, `oc-mirror`; `mirror-registry` not
  needed in cache mode).
- Helm charts from remote repos (e.g. `charts.jetstack.io`) and OCI charts (e.g. `ghcr.io`).
- Host setup (`dnf`, ansible collections).

The cache guarantee is therefore: every container image used by the cluster or the LZ comes from
the cache.

### Registry redirection on the LZ

**Problem.** In cache mode, LZ-side tools must pull from the cache instead of upstream or the LZ
mirror. Nothing in place today does it:

| LZ consumer | Today | Cache mode needs |
|---|---|---|
| release-payload pulls (ISO, ironic, BMO; see [Landing Zone pulls](#landing-zone-pulls)) | no redirection: pulled from upstream | redirected to the cache, permanently |
| oc-mirror runs seeding Quay Enterprise (`quay-disconnected`, `mirror_plugin.yaml`) | temporary `~/.config/containers/registries.conf` from the static `templates/registries.conf.j2`, pointing at the LZ mirror, deleted after the run | redirected to the cache, per run |

The existing mechanism cannot be reused as is:

- the template is a hand-maintained list of upstream namespaces hardcoded to
  `{{ quayHostname }}:8443`, duplicating what oc-mirror records in IDMS/ITMS
- `oc adm release info` / `oc image extract` (release-payload resolution) do not read
  registries.conf at all
- the user-level file it creates disables system-wide redirection for rootless podman (verified,
  see below), so it conflicts with a permanent redirection

What is new is permanent LZ redirection; per-run redirection for oc-mirror exists today and changes
source.

**Scope: cache mode only.** In LZ mode the LZ keeps pulling release-payload images from upstream
and Quay seeding keeps using `templates/registries.conf.j2` and the user-level file, unchanged.

**Design.** The upstream→mirror mapping exists only once: the IDMS/ITMS stored per unit. Every LZ
consumer derives its format from it at consume time:

- `oc adm release info` / `oc image extract` take the IDMS directly (`--idms-file`); they do not
  read registries.conf.
- podman and oc-mirror get a registries.conf generated from IDMS/ITMS (digest-only for IDMS
  entries, tag-only for ITMS), with the cache endpoint substituted.

In cache mode `templates/registries.conf.j2` and the plugin descriptor `registries:` entries are
not used for LZ redirection; they are hand-maintained copies of what oc-mirror already records.

File ownership:

- **Persistent LZ redirection** (release-payload images) goes in a system drop-in, e.g.
  `/etc/containers/registries.conf.d/enclave-cache.conf`, since ironic/BMO run as root with
  `metal3_persistent`.
- **No user-level `~/.config/containers/registries.conf` in cache mode.** Verified on a RHEL LZ:
  root and rootless podman honor the system drop-in, but rootless podman ignores system drop-ins
  entirely when the user-level file exists, and silently pulls upstream. Today
  `quay_disconnected_start.yaml` and `mirror_plugin.yaml` create that file (and leave it behind if
  they fail). Cache-mode validation fails if it exists.
- **oc-mirror runs** (Quay Enterprise seeding, plugins) use a per-run file via
  `CONTAINERS_REGISTRIES_CONF` instead of creating and deleting
  `~/.config/containers/registries.conf`; required by the previous point. Verified with oc-mirror
  v2 (4.20): a source on a non-resolvable host mirrored successfully only when the variable pointed
  to a per-run file with a mirror for it.

### Day 2: Quay Enterprise seeding

With a cache configured there is no LZ mirror, so the cache is the only source to seed Quay
Enterprise in the management cluster (`quay-disconnected` in `06-day2.yaml`, and addon plugins in
`mirror_plugin.yaml`). Quay Enterprise is a write target; the cache stays read only.

Today the seeding reads from the LZ mirror through two LZ-specific mechanisms:

- an `*.internal.yaml` copy of the rendered imageset whose `catalog:` entries are rewritten to
  `{{ quayHostname }}:8443/redhat/...`
- a temporary `~/.config/containers/registries.conf` from `templates/registries.conf.j2`, a
  hand-maintained list of upstream namespaces mapped to `{{ quayHostname }}:8443/...`, plus plugin
  `registries` entries; deleted when the mirror finishes

In cache mode:

- Source catalogs and the upstream→mirror mapping point to the cache endpoint/prefix.
- The imageset comes from the shared render step, since phase 02 no longer produces it.
- The oc-mirror run uses a per-run registries.conf generated from the IDMS/ITMS stored in the cache
  (see [Registry redirection on the LZ](#registry-redirection-on-the-lz)), not the static template,
  and does not touch the persistent LZ redirection.
- The cache CA must be trusted wherever the LZ mirror CA is trusted today.

### Plugin deployment

Two paths deploy plugins:

- **Bootstrap** (`05-operators.yaml` → `deploy_plugins.yaml`): enabled plugins' operators are
  already mirrored by the core imageset in phase 02; `deploy_plugin.yaml` runs without
  `plugin_mirror`.
- **Day 2** (`make deploy-plugin` / `make mirror-plugin`, `deploy-plugin.yaml` with
  `plugin_mirror=true`): `mirror_plugin.yaml` runs oc-mirror upstream → LZ mirror, renames the
  resulting IDMS/ITMS to `*-plugin-<name>`, re-mirrors LZ → Quay Enterprise, patches the MCE custom
  registries, and applies everything in one MCO rollout. The plugin may not be in
  `enabled_plugins`.

Per-unit keys make day 2 cheap to validate: a plugin only needs `catalog-<plugin>` and
`images-<plugin>`, independent of the rest of the config. In cache mode:

1. Adding a plugin is a cache operator job first: `cache.sh build --plugin <name>`.
1. `deploy-plugin` / `mirror-plugin` validate the plugin units (dry-run) and exit before changing
   anything if they are missing. Helm image extraction runs as part of the shared render step, so
   the plugin units are resolved exactly as in `build`.
1. The upstream → LZ oc-mirror step is skipped. The plugin's `cluster-resources` come from the
   cache metadata, already named per unit, with the host placeholder substituted, and are applied
   in the same single MCO rollout as today.
1. Quay Enterprise seeding reads from the cache (see
   [Day 2: Quay Enterprise seeding](#day-2-quay-enterprise-seeding)), with a per-run
   registries.conf.
1. MCE custom registries are unchanged: they already point spokes to Quay Enterprise.
1. Subscriptions use the stable CatalogSource name `cs-<index>-<plugin>` (enclave PR #977).

`mirror-plugin` no longer mirrors into a registry it owns in cache mode; it becomes "validate,
apply mirror manifests, seed Quay Enterprise". If the plugin units are missing it fails and points
to `cache.sh build --plugin <name>`.

### Upgrades

Preparing the cache for an upgrade is the cache operator's job; enclave only verifies.

1. The cache operator runs `cache.sh build` with the new enclave content and the installation's
   config (`fresh=false`, full `openshift_versions` range).
1. `sync.sh` validates the cache (dry-run). If the cache is not suitable, it exits before changing
   anything. Otherwise it runs as today minus mirroring.
1. `upgrade.sh` runs as today.

`cache.sh build` is additive only: the running cluster (partially upgraded nodes, node replacement,
rollback, catalogs on old digests) and other installations sharing the cache may still need the
previous content.

### Security

- Credentials handled as secrets end to end (`no_log`, not in artifacts).
- User-provided CA bundle trusted by LZ and cluster.
- Diagnostics bundle redacts cache credentials.

### CI

CI-only snapshot/restore. Purpose: test the cache path and speed up disconnected e2e.

#### Where the cache runs

On the **hypervisor host** (the AWS host), not inside the LZ VM, created per run from an EBS
snapshot:

- it is genuinely external to the enclave, which makes the test representative of a customer cache
- the EBS volume mounts directly, no disk passthrough into the guest
- LZ cleanup/reinstall cannot affect it

`vm_infra.py` owns it: stable cache hostname, DNS record, endpoint and CA emitted in
`cluster-env.sh`. The hostname and CA must be **stable across runs** (independent of the generated
cluster name) so the snapshot's certs stay valid; the CI CA lives in CI secrets.

#### Storage: EBS snapshot with Provisioned Rate for Volume Initialization

- The snapshot holds all cache state: Quay storage (blobs, ~150 GB), Postgres, config.
- Restore with provisioned initialization rate (~$0.0036/GB, ~$0.55 per run at 150 GB), not FSR.
  Predictable time to fully initialized volume.
- S3 → EC2 in the same region has no transfer charge, so S3 remains a fallback if EBS proves
  awkward, but EBS avoids a download/extract step.
- Requires IAM permissions on the runner (create/attach/detach/delete volume, create/describe
  snapshot) and reliable cleanup to avoid orphaned volumes.

#### Snapshot lookup and publishing

- A snapshot holds the union of the units needed by the CI configs. Its **snapshot key** is a hash
  of the sorted unit keys of that union; snapshots are tagged with it and a `ready` marker set only
  after `verify` passes. Lookup = describe snapshots by snapshot key + ready.
- **Only post-merge runs on protected branches (`main`, `stable`) publish snapshots.** PR runs
  never do.
- Weekly rebuild from scratch on each protected branch to pick up new catalog content.
- **Retention:** snapshots older than 14 days are deleted. The weekly rebuild guarantees every
  protected branch always has a snapshot younger than 14 days, so no "last used" tracking is
  needed. A run whose key was deleted (e.g. an old unrebased PR) misses and builds on the host.

#### Flows

| Run | Cache key found | Behaviour |
|---|---|---|
| PR | yes | Restore snapshot, deploy via cache path |
| PR | no (PR changes mirror inputs, or main snapshot not ready yet) | Build cache as pre-install step on the host, deploy via cache path, report the miss |
| protected branch post-merge | no | Build, verify, snapshot, tag ready |
| nightly | n/a | No cache: exercise LZ mirror-registry install end to end |

The content key removes the race condition: a PR that needs content not yet published misses and
builds its own; once the protected branch publishes, later PRs hit. No moving "latest" snapshot
pointer to coordinate (unlike the per-unit metadata `<unit>:latest` tag inside a cache, which only
selects metadata).

#### Mirror validation gate

The e2e "Phase 2.5 - Validate mirror artifacts" step (`make -f Makefile.ci validate-mirror`,
`mirror_validation.yaml`) checks LZ Quay health, the LZ pull secret and the oc-mirror workspace
IDMS/ITMS/CatalogSource manifests. None of these exist in cache mode. Cache-mode runs replace it
with `cache.sh verify` plus checks on the materialized `cluster-resources` directory; LZ-mode runs
(nightly) keep the current gate.

### Testing

#### Key unit tests

The render, canonicalize and hash logic lives in Python under `src/tools/` (e.g. an
`enclave tools cache-key` subcommand), not in Jinja inside Ansible, so it is covered by pytest like
the rest of `src/`:

- **Stable:** reordering plugins or packages, template whitespace, hostname, prefix, `workingDir`,
  oc-mirror tuning → same key.
- **Sensitive:** changing an operator version range, an OCP release, additional or blocked images,
  Helm-extracted images, enabling a plugin, an oc-mirror upgrade → only the affected unit's key
  changes.
- **Schema version bump** → every key changes.
- Floating tags are hashed as written, never resolved.

End to end, the CI flows already exercise the hit path (PRs), the miss-and-build path (PRs changing
mirror inputs) and LZ mode (nightly).

#### Validation scenarios

After the proof of concept. A throwaway local registry and a fixture imageset with a few tiny
images (no OCP payload, no hardware, minutes per run), run on PRs touching the cache logic:

- exact hit → valid
- superset (wider range, extra plugin) → key mismatch, dry-run passes → valid
- missing unit → fails before changing anything
- day-2 plugin with units missing / present
- oc-mirror version change → key mismatch, dry-run decides
- key matches but an image was deleted after the build → fails
- host alias / prefix → placeholder substituted correctly
- wrong CA or credentials → clear error, not an oc-mirror failure

## Alternatives considered

**Extending the prior art** ([enclave PR #34](https://github.com/rh-ecosystem-edge/enclave/pull/34)).
It pushes to an external registry from inside the installation workflow, duplicating
`mirror_registry.yaml`, and nothing consumes the result. The cache lifecycle must be separate from
an installation, and the mirroring code must be shared, not copied.

**An enclave version attached to the cache.** It needs a version for an enclave that is not
delivered as a git repository, i.e. a version file in the repo, which the team has been reluctant
to add. It is also too coarse: any enclave change would invalidate the cache, while only changes to
mirrored content should. Content keys plus a dry-run remove that moving part.

**A key alone, without dry-run.** A matching key proves a unit was built from the same inputs, not
that its images are still in the registry. A key mismatch is also not proof of a missing image when
the cache holds a superset.

**Hashing repo files for the key.** Too broad (documentation changes invalidate the cache) and too
narrow (misses Helm rendering and config-driven content).

**A dedicated metadata service, or a custom OCI artifact type.** Both add a moving part: an extra
server next to the registry, or a new binary such as `oras` on the LZ and a dependency on registry
artifact support. A plain image read with `oc image extract` works with any registry and with what
enclave already ships.

**Storing registries.conf in the cache metadata.** It would be a second, podman-specific copy of
the upstream→mirror mapping that can drift from the IDMS/ITMS.

**Keying OCP per channel range.** Bootstrap and `sync.sh` mirror different ranges, so the same
enclave and config would produce different keys depending on the entry point.

**Switching modes on an existing installation.** Object names differ between modes, and moving
between them would need a migration of IDMS/ITMS, CatalogSources and LZ redirection.

**Running the CI cache inside the LZ VM.** It would avoid nothing (the LZ is a VM anyway), needs a
disk passthrough into the guest, is less representative of a customer cache, and LZ
cleanup/reinstall could affect it.

**Restoring the CI cache from S3, or from EBS with FSR.** S3 needs a download/extract step. FSR
costs more than a provisioned initialization rate and does not give a predictable time to a fully
initialized volume. S3 stays as a fallback.

**Failing PRs that miss the CI snapshot.** A PR that changes mirror inputs, or runs before the
protected branch publishes, would always fail. Building on the host keeps those PRs green; strict
misses for PRs that do not touch mirror inputs are listed in [Non-objectives](#non-objectives).

## Milestones

1. Split `mirror_registry.yaml` into "install registry" and "mirror one imageset", and introduce
   the materialize cluster resources step consumed by bootstrap and sync. No change in behaviour
   for existing installations.
1. Shared render step and cache key (`enclave tools cache-key`) with its unit tests.
1. `cache.sh build` / `verify`, cache configuration and mode file, and cache-mode consumption in
   `bootstrap.sh`, `sync.sh` and `upgrade.sh`, including LZ registry redirection.
1. Cache mode for day 2: Quay Enterprise seeding and `deploy-plugin` / `mirror-plugin`.
1. CI: cache host owned by `vm_infra.py`, EBS snapshot publishing and lookup, cache-mode mirror
   validation gate, and the validation scenarios.

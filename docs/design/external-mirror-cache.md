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
- **Porting cache-mode improvements to LZ mode.** LZ redirection, generated registries.conf, TLS
  verification and a fail-closed source policy stay cache mode only (see
  [Differences from LZ mode](#differences-from-lz-mode)); each is a separate change.
- **Phase 02 as "build a local cache, then consume it".** One model for both modes, where LZ mode
  becomes a cache on the LZ. It changes phase 02 to per-unit runs; since cache mode keeps the LZ
  mode object names, existing installations would not need their IDMS/ITMS renamed.
- **Changing the cache endpoint of an installation.** The endpoint is embedded in the units'
  IDMS/ITMS and CatalogSources, the installer-created `image-digest-mirror` IDMS (not managed by
  enclave), the cluster pull secret and CA trust, and on the LZ in the persistent registries.conf
  drop-in, the containers CA trust and the authfile. Supporting it would mean re-pointing all of
  them, including the IDMS/ITMS of units no longer required but kept on the cluster, after
  checking that the new endpoint holds their content.
- **Long-lived CI cache host.** It avoids per-run restores, but a per-run build is still needed for
  key misses. To be revisited after measuring restore times.
- **Strict CI misses.** Failing PRs that miss without touching mirror inputs (which signals a
  broken publishing pipeline) instead of building on the host.

## Proposal

### Terminology

A **mirroring unit** (or unit) is the smallest piece of cache content that enclave builds, keys
and validates on its own. Each unit is one oc-mirror imageset with its own cache key, metadata
image and cluster resources (IDMS/ITMS, CatalogSources). The content of a config is split into
units so that a config change only affects the units whose content it changes. A unit is defined
by a filter, and oc-mirror resolves it into images (see
[Resolving a unit's images](#resolving-a-units-images)). See [Mirroring units](#mirroring-units)
for the full list.

Examples:

- A config with `openshift_versions` 4.20.21 and 4.20.32 and `enabled_plugins: [lvms, cnv]`
  requires `ocp-4.20.21`, `ocp-4.20.32`, `catalog-core`, `images-core`, `catalog-lvms` and
  `catalog-cnv`, plus `images-<plugin>` for plugins that declare additional images.
- Changing the channel of the `cnv` operator changes only the `catalog-cnv` key; the other units
  keep their keys, so the metadata and catalogs already in the cache for them still match.
- Deploying `openshift-ai` on day 2 only needs `catalog-openshift-ai` (and `images-openshift-ai`
  if it declares additional images), built with `cache.sh build --plugin openshift-ai`, whatever
  the rest of the config is.

### Design principle

Cache mode behaves like LZ mode, with the same objects, names and code paths, except where that
would break the cache guarantee (see [Landing Zone pulls](#landing-zone-pulls)) or prevent
several installations from sharing a cache. Every exception is listed in
[Differences from LZ mode](#differences-from-lz-mode). Where cache mode does better, LZ mode is
fixed separately to match it, rather than keeping two ways of working.

### Prerequisite

Stable, y-stream agnostic CatalogSource names
([enclave PR #977](https://github.com/rh-ecosystem-edge/enclave/pull/977), merged). oc-mirror v2
names the CatalogSources it generates `cs-<targetCatalog>-<tag>` (e.g.
`cs-redhat-operator-index-v4-20`); LZ mode applies them and creates a stable alias `cs-<index>`
with the same spec, which Subscriptions use (`create_stable_catalog_source_aliases.yaml`). Cache
mode produces the same versioned CatalogSources and reuses the same alias step (see
[Outputs stored in the cache](#outputs-stored-in-the-cache)).

### Consumption model

When a cache is configured it is an **external, runtime dependency**:

- The LZ does not install mirror-registry and the disconnected mirror phase is skipped.
- The cluster (IDMS/ITMS, CatalogSources, install-config mirrors) points at the cache.
- The cache must stay reachable for the cluster lifetime: day 2, upgrades, node replacement.
- `bootstrap.sh`, `sync.sh`, `upgrade.sh` and `deploy-plugin` treat the cache as **read only**.

When no cache is configured, behaviour is unchanged.

**No mode or endpoint switching.** The mode is fixed at bootstrap: an installation using an
external cache always uses it, and an installation using the LZ mirror never moves to a cache. In
cache mode the cache endpoint (host:port and prefix) is fixed too. `sync.sh`, `upgrade.sh` and
`deploy-plugin` refuse to run if the configured mode or endpoint differs from the one the
installation was bootstrapped with. Relocating the registry behind a cache is possible as long as
it keeps the same endpoint (e.g. the same DNS name) and its content; changing the endpoint is a
non-objective (see [Non-objectives](#non-objectives)).

The mode and endpoint are recorded on the LZ following the `migrationTasksFilePath` pattern: a
state file whose path is a default in `defaults/deployment.yaml` (e.g.
`mirrorModeFilePath: "{{ workingDir }}/state/mirror-mode"`, content `lz`, or `cache` plus the
endpoint), overridable in config and covered by the schema.

- `bootstrap.sh` writes it in an early step (before any mirroring), not at the end like the
  migration control file, so a failed bootstrap that is re-run with a different mode or endpoint
  is caught too. If the file exists with a different mode or endpoint, bootstrap fails.
- `sync.sh`, `upgrade.sh` and `deploy-plugin` compare it with the configured mode and endpoint and
  exit before changing anything on mismatch.
- A missing file means an installation that predates cache mode: treated as `lz`.

### Configuration

New optional config (schema-validated):

- cache endpoint (host:port, optional namespace/prefix)
- pull-only credentials (secret)
- CA bundle used to trust the cache (no `tls-verify=false`)

The installation never holds credentials that can write to the cache. Push credentials belong to
`cache.sh build` only (see [Cache maintenance](#cache-maintenance)) and are not part of an
installation's config.

### Cache maintenance

A separate entry point (working name `cache.sh`) owned by whoever operates the cache:

- `build` — mirror the content required by an enclave config into the cache, reusing the existing
  mirroring tasks (`mirror_registry.yaml` logic) rather than duplicating them.
- `verify` — the same check `bootstrap.sh` / `sync.sh` run (see
  [Validity](#validity-fingerprint--dry-run)), usable standalone.

`build` is the only writer, and the only user of push credentials, which it takes separately from
the installation's config. It is also what CI uses to create a cache.

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

`build` never overwrites what another key published. Images are only added, and everything a
build writes under a tag (filtered catalogs and metadata images) is tagged with the unit key, so
builds from different configs sharing the cache cannot replace each other's content. Re-running
`build` with the same key republishes that key's catalog and metadata together; for catalog units
this refreshes the catalog from the current upstream index, as `sync.sh` does today in LZ mode
(see [Validity](#validity-fingerprint--dry-run)).

#### Reusing the mirroring code

`mirror_registry.yaml` is split, without behaviour change, into:

- **install registry** — mirror-registry install and Postgres migration (LZ mode only)
- **mirror one imageset** — oc-mirror one imageset to a destination and collect its
  `cluster-resources`

Phase 02 keeps a single combined run: it calls "mirror one imageset" once with the combined
imageset, so its behaviour, outputs and object names are unchanged. `cache.sh build` calls it once
per mirroring unit, which gives each unit its own `cluster-resources` without post-processing. One
mirroring code path, two callers.

TLS verification is an input of "mirror one imageset", not hardcoded. Today `mirror_registry.yaml`
runs oc-mirror with `--src-tls-verify=false` and `--dest-tls-verify=false`, and the intermediate
index `skopeo copy` with `--dest-tls-verify=false`:

- Phase 02 in LZ mode passes the current values, so its behaviour does not change (see
  [Differences from LZ mode](#differences-from-lz-mode)).
- In cache mode every access to the cache verifies TLS against the configured CA bundle:
  `cache.sh build` pushes, dry-runs and Quay Enterprise seeding reads. The CA is installed in the
  containers trust for the cache host (`/etc/containers/certs.d/<host:port>/`), never bypassed.
  Upstream access verifies against the system trust.

The prior art, `playbooks/tasks/mirror_cache.yaml` (the opt-in `mirror-cache` tag of
`02-mirror.yaml`), is replaced by `cache.sh` and removed once cache mode lands.

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
    metadata image selected for it (see [Metadata selection](#metadata-selection)), substitute the
    host/prefix placeholder with the configured endpoint, and write the IDMS/ITMS and
    CatalogSources with the LZ mode file and object names (see
    [Outputs stored in the cache](#outputs-stored-in-the-cache)).
- Consumers read only that directory:

| Entry point | Units | Action |
|---|---|---|
| `bootstrap.sh` | all required | copy IDMS/ITMS/CatalogSources/signatures into the ABI manifests; install-config `imageDigestSources` from the `ocp-<version>` unit IDMS, `additionalTrustBundle` from the configured cache CA; ABI `mirror/registries.conf` generated from the IDMS/ITMS with `blocked = true` |
| `sync.sh` (and before `upgrade.sh`) | all required | apply CatalogSources and signatures (as today), plus IDMS/ITMS in cache mode (below); wait for CatalogSources ready |
| `deploy-plugin` / `mirror-plugin` | the plugin's units | apply in the single MCO rollout, as today |
| Quay Enterprise seeding, LZ redirection | IDMS/ITMS | generate the per-run registries.conf and the persistent drop-in |

- The ABI copy and the apply steps are unchanged: the directory holds the same file names in both
  modes.
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
- In cache mode the materialized IDMS/ITMS set `mirrorSourcePolicy: NeverContactSource` on every
  source (see [Fail-closed source policy](#fail-closed-source-policy)). The cache is a runtime
  dependency, so not falling back when it is unavailable is consistent.

### Cache metadata

Each build publishes one small metadata image per unit to the cache itself (e.g.
`enclave-cache/<unit>:<key>`). It is a plain container image (`FROM scratch`, one layer with the
files), not a custom OCI artifact type: enclave reads it with `oc image extract`, which it already
ships (no new binary such as `oras` on the LZ), and any registry accepts it. It contains:

- the cache key (see [Cache key](#cache-key)), metadata format version and build timestamp
- the canonical unit imageset and resolved catalog index digests (traceability)
- for `ocp-<version>` units, the release image digest (see
  [Landing Zone pulls](#landing-zone-pulls))
- the list of images the unit mirrored, read from oc-mirror's resolution (see
  [Resolving a unit's images](#resolving-a-units-images)); used by
  [Metadata selection](#metadata-selection), and keeps a later per-unit pruning possible
- oc-mirror `cluster-resources/` (IDMS, ITMS, CatalogSources, signature ConfigMaps) that enclave
  needs to configure the cluster, as oc-mirror generated them for the unit; the materialize step
  gives them the LZ mode names (see [Outputs stored in the cache](#outputs-stored-in-the-cache))

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
- To verify at implementation time: a mirror-to-mirror dry-run checks the destination registry for
  missing images. A mirror-to-disk dry-run does not (observed with oc-mirror v2 4.20: it only
  checks its local storage and never contacts the source).

Catalogs are referenced by floating tags (`redhat-operator-index:v4.20`). Once the cache is built,
it defines what those tags point to for every installation using it:

- The key hashes the tag as written. Computing it never resolves the tag upstream.
- Packages listed without channels float the same way: they resolve to the head of their default
  channel at build time.
- The dry-run validates against the index stored in the cache (the catalog of the metadata being
  checked, see [Metadata selection](#metadata-selection)), not the current upstream index,
  otherwise any newer upstream publish would report missing images.
- Catalog freshness changes owner. Today every `sync.sh` run re-resolves the tags and mirrors the
  latest upstream index into the LZ mirror. In cache mode `sync.sh` does not mirror, so catalog
  content is as fresh as the last `cache.sh build` of that key.

#### Metadata selection

Each unit's metadata image is tagged with its key (`enclave-cache/<unit>:<key>`). There is no
moving tag such as `<unit>:latest`: it would point at the metadata of the last build, which can
describe less than another config needs (fewer packages, so IDMS/ITMS missing repositories it
pulls from) even when the dry-run passes on images other builds mirrored.

For each unit it needs, a flow selects metadata as follows:

1. **Exact key** present: use it. The dry-run against its catalog must report no missing images.
1. Otherwise, try the unit's other metadata tags, newest build first. A candidate is accepted when
   the dry-run of the required unit against the candidate's catalog reports no missing images,
   and the candidate's recorded image list covers every image that dry-run resolved, so its
   IDMS/ITMS map every repository the config pulls from. Its catalog and cluster resources are
   then used as a pair.
1. No candidate accepted: fail before changing anything, naming the unit and pointing to
   `cache.sh build`.

Catalogs follow the same rule: `build` pushes each filtered catalog with `targetTag` derived from
the unit key, never to the source tag. Today's imagesets set `targetCatalog` only, so every run
pushes its filtered catalog to the same tag (e.g. `redhat/redhat-operator-index:v4.20`), and a
build for a config with fewer packages would remove operators from catalogs that other
installations already use.

Each candidate is checked through one **resolved imageset**, used for everything that follows from
selecting it:

- It is the unit imageset from the shared render step with each `catalog:` rewritten to the keyed
  catalog in the cache (`<cache>/<prefix>/redhat/<targetCatalog>:<candidate key>`), the same
  rewrite the `*.internal.yaml` imagesets do today for the LZ mirror.
- The dry-run runs on it with a per-run registries.conf generated from the candidate's IDMS/ITMS
  (see [Registry redirection on the LZ](#registry-redirection-on-the-lz)), so everything oc-mirror
  fetches resolves to the cache and nothing goes upstream. A repository missing from the
  candidate's IDMS/ITMS also makes the dry-run fail, backing the image list coverage check.
- For the accepted candidate, the materialized CatalogSource `image:` is the same keyed reference,
  and Quay Enterprise seeding uses the same imageset (see
  [Day 2: Quay Enterprise seeding](#day-2-quay-enterprise-seeding)). The catalog that was
  validated is the one deployed and seeded.

### Cache key

#### Mirroring units

| Unit | Content | Defined by | Today produced by |
|---|---|---|---|
| `ocp-<version>` | one OpenShift release payload (+ graph data) | one `openshift_versions` entry (`defaults/platforms.yaml`, config) as a `platform.channels` entry with `minVersion` = `maxVersion` | core imageset `platform` |
| `catalog-core` | core operators from `redhat-operator-index` | catalog `rh_operator_catalog:rh_operator_catalog_version` (`defaults/catalogs.yaml`); packages, channels and version ranges from `operators` (`defaults/operators.yaml`), incl. `csvNames` and `extraMirrorPackages` | core imageset `operators`, key `core` of `catalog_operators` (`02-mirror.yaml`) |
| `catalog-<plugin>` | operators of one plugin from its intermediate index `redhat-operator-index-<plugin>` / `mirror-certified-operators-<plugin>` | `operators` and `catalog` of `plugins/<plugin>/plugin.yaml` | core imageset (enabled plugins, `collect_core_plugin_operators.yaml`) and `mirror_plugin.yaml` (`make deploy-plugin`) |
| `images-core` | core `additionalImages` / `blockedImages` (ubi, argocd, vault) | hardcoded in `templates/imagesetconfiguration.yaml.j2` | core imageset template |
| `images-<plugin>` | plugin `additionalImages` incl. Helm-extracted images | `additionalImages` / `blockedImages` of `plugins/<plugin>/plugin.yaml` plus `helm_extract_images.yaml` | `mirror_plugin.yaml` |

The plugin catalog target name is the same in the core and addon paths, so a plugin unit means the
same thing regardless of which path mirrors it.

Today the core imageset bundles OCP, core operators and all enabled plugins' catalogs in a single
file, so "core" is not a stable unit: enabling a plugin changes it. In cache mode oc-mirror runs
once per unit (see [Reusing the mirroring code](#reusing-the-mirroring-code)); phase 02 in LZ mode
keeps its single combined run.

OCP releases are keyed per release, not per channel range: bootstrap (`fresh=true`) mirrors only
the default version while `sync.sh` (`fresh=false`) mirrors the `openshift_versions` range, so the
same enclave and config render different core imagesets depending on the entry point.

#### Resolving a unit's images

A unit is defined by a filter, known before any mirroring: the catalog and the package, channel
and version ranges (or release versions, or image names) above. Its cache key hashes that
definition. The images that belong to it are only known once oc-mirror resolves the filter
against the source:

- **Catalog units:** oc-mirror reads the index image at its tag, keeps the bundles of each package
  inside the channel and version range, collects each bundle image and its `relatedImages`
  (operator and operand images), and builds a **filtered index**: a catalog image whose
  file-based catalog (`/configs`) only contains the selected packages, channels and bundles. It
  pushes the filtered index to `targetCatalog`, which is what the CatalogSource serves. The
  filtered index is the source index image plus two layers: a whiteout removing `/configs` and
  the filtered `/configs`. The source layers stay in the image as shared blobs, so reading the
  filtered catalog from the image must apply the whiteout (extracting `/configs` with
  `oc image extract` returns the union of both).
- **OCP units:** oc-mirror resolves the release through the update graph to the release image
  and every component image of the payload, by digest, plus the graph image.
- **Image units:** the listed images, as written.

The unit's image list stored in its metadata is read from that resolution: for catalog units, the
bundle images and `relatedImages` of the filtered catalog that oc-mirror leaves in its workspace
(`working-dir/operator-catalogs/<catalog>/<source digest>/filtered-catalogs/<filter hash>/`),
minus `blockedImages`. oc-mirror names that directory after the filter only, not the source
digest, which matches keying units by their definition. As a reference, `catalog-core` for the
current 4.20 content resolves to 34 bundles and 564 images in 300 repositories, and
`ocp-4.20.32` to 191 release images.

Packages listed without channels (e.g. `kubevirt-hyperconverged`, an `extraMirrorPackages` entry of
`metallb-operator`) resolve to the head of their default channel, so they move with upstream on
every build, like floating tags (see [Validity](#validity-fingerprint--dry-run)).

In LZ mode the source and destination of each catalog are the same repository and tag in the LZ
mirror: the skopeo step copies the full upstream index there, and oc-mirror then pushes the
filtered index over it. Any oc-mirror run that does not repeat the skopeo copy first (e.g. an
Ansible retry after a partial run) reads the already filtered index as its source, and stacks a
second whiteout and filtered layer pair on top. In cache mode
the source is the upstream index and the destination a keyed tag, so the source is never
overwritten.

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

Tag references in `additionalImages` (e.g. `registry.redhat.io/ubi8/ubi:latest`) are mirrored
under their source tag, not keyed. A later build may move the tag to the current upstream digest,
as `sync.sh` does today in LZ mode. Unlike filtered catalogs, configs never disagree on what such a
tag selects, only on when it was resolved, so no build removes content another config chose.
Content that must not move is referenced by digest or by a versioned tag in enclave.

Object names:

- **IDMS/ITMS** are the same objects as in LZ mode. The units required by the config (OCP, core
  catalog and images, enabled plugins) are merged into `idms-oc-mirror.yaml` /
  `itms-oc-mirror.yaml`, with the object names oc-mirror uses for the combined phase 02 run
  (`idms-release-0`, `idms-operator-0`, `itms-release-0`, `itms-generic-0`). The units of a plugin
  deployed with `make deploy-plugin` go to `idms-oc-mirror-plugin-<name>.yaml` /
  `itms-oc-mirror-plugin-<name>.yaml`, with the `-plugin-<name>` suffix `mirror_plugin.yaml` adds.
  The per-unit oc-mirror runs stay internal to `cache.sh build`.
- **CatalogSources** are the same objects as in LZ mode: the versioned
  `cs-<targetCatalog>-v<y>` (e.g. `cs-redhat-operator-index-v4-20`), followed by the stable alias
  `cs-<index>` / `cs-<index>-<plugin>` that `create_stable_catalog_source_aliases.yaml` creates
  from it (enclave PR #977), and that Subscriptions use. oc-mirror names the CatalogSource after
  the destination tag, which in cache mode is the key, so `cache.sh build` stores it renamed to
  the name LZ mode gets, derived from the catalog source tag. Only its `image:` differs: it points
  at the catalog tagged with the selected key (see [Metadata selection](#metadata-selection)).
  A new key within a y-stream updates the versioned CatalogSource in place, and the alias step
  copies it. A y-stream change (e.g. catalog `v4.20` to `v4.21`) adds `cs-<index>-v4-21`, the
  alias switches to it at `sync.sh`, and `cs-<index>-v4-20` is left on the cluster, as in LZ mode.

Object names are the same in both modes (see [Design principle](#design-principle)).

### Landing Zone pulls

With a cache configured, no image the LZ uses may bypass it. Today the LZ resolves three images
from the release payload and pulls them straight from upstream:

| Image | Where | Phase |
|---|---|---|
| `machine-os-images` (RHCOS ISO via `oc image extract`) | `download_content_single_version.yaml` | 01-prepare (`download-content`) |
| `ironic` | `deploy_ironic.yaml` | 07 / hardware setup / upgrade |
| `baremetal-operator` | `deploy_bmo.yaml` | 07 / hardware setup / upgrade |

All three are resolved with `oc adm release info quay.io/openshift-release-dev/ocp-release:<v>`
(a tag) and pulled by digest, so they are already part of the `ocp-<version>` unit. No new cache
units: the fix is consumer side, redirecting the LZ to the cache using the same source→mirror
mapping as the cluster (see [Registry redirection on the LZ](#registry-redirection-on-the-lz)).

IDMS only applies to digest references, so the tagged release lookup would still go upstream. In
cache mode consumers reference the release by the digest recorded in the `ocp-<version>` unit
metadata (`ocp-release@sha256:...`) instead of the tag, so the release lookup and the component
pulls all go through the IDMS mapping. The tag is never resolved, as for catalogs.

Ordering: with a cache the content exists before phase 01. LZ mode keeps pulling from upstream
(see [Differences from LZ mode](#differences-from-lz-mode)).

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

**Scope: cache mode only** (see [Differences from LZ mode](#differences-from-lz-mode)).

**Design.** The upstream→mirror mapping exists only once: the IDMS/ITMS stored per unit. Every LZ
consumer derives its format from it at consume time:

- `oc adm release info` / `oc image extract` take the IDMS directly (`--idms-file`), with the
  release referenced by digest (see [Landing Zone pulls](#landing-zone-pulls)); they do not read
  registries.conf.
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

#### Fail-closed source policy

Fail closed means that a pull of an image missing from the cache fails with an error, instead of
falling back to the upstream registry (fail open, the default for IDMS/ITMS and registries.conf).

A mirror miss must fail, never fall back to the source: otherwise a dry-run passes on an image
missing from the cache whenever upstream is reachable (CI hosts, sites with some egress), and
pulls silently bypass the cache.

- **LZ:** every source `[[registry]]` in generated registries.conf files (persistent drop-in and
  per-run files for dry-runs and Quay Enterprise seeding) has `blocked = true` next to its cache
  mirror, which is how MCO renders `NeverContactSource` on nodes.
- **Cluster:** IDMS/ITMS applied in cache mode set `mirrorSourcePolicy: NeverContactSource` on
  every source (see [Applying cluster resources](#applying-cluster-resources)).
- **Agent ISO:** `bootstrap.sh` supplies `mirror/registries.conf` in the agent installer asset
  directory, generated from the unit IDMS/ITMS with `blocked = true` like the LZ files. The
  agent installer loads it instead of rendering one from install-config, so pulls on the hosts
  during discovery (agent, assisted-service, release image) fail closed.
- **Bootstrap host:** cannot fail closed. assisted-service writes the cluster install-config with
  `imageDigestSources` carrying only source and mirrors (the install-config `sourcePolicy` field
  only exists from 4.21, and assisted-service drops it anyway), and the bootstrap host's
  registries.conf is rendered from it. `bootstrap.sh` relies instead on the fail-closed dry-run
  it runs for every required unit, including `ocp-<version>`, before generating the ISO. Known
  limitation: an image removed from the cache during bootstrap can be pulled from upstream by the
  bootstrap host at a site with egress. Sites that require strictly cache-only pulls block the
  bootstrap host's egress to upstream registries.
- **Masters and installed cluster:** the installer also creates an `image-digest-mirror` IDMS from
  `imageDigestSources`, without a policy. MCO merges all IDMS per source and blocks a source if
  any of them sets `NeverContactSource`, and the bootstrap machine-config controller renders from
  both the installer manifests and the ABI extra manifests, so masters are fail closed from their
  first boot. This needs the install-config sources to match the unit IDMS sources exactly, which
  holds because `imageDigestSources` is generated from the `ocp-<version>` unit IDMS.
- `oc adm release info` and `oc image extract` honor `NeverContactSource` with `--idms-file`
  (oc 4.20), so LZ release lookups fail closed too.

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
- The imageset is the resolved imageset of the selected metadata (see
  [Metadata selection](#metadata-selection)), which replaces `*.internal.yaml`, since phase 02 no
  longer produces an imageset.
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
   cache metadata, named `*-plugin-<name>` as `mirror_plugin.yaml` names them, with the host
   placeholder substituted, and are applied in the same single MCO rollout as today.
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
- Installations only hold pull-only credentials: a compromised LZ cannot overwrite cache images
  or metadata that other installations share.
- User-provided CA bundle trusted by LZ and cluster.
- Diagnostics bundle redacts cache credentials.

### Differences from LZ mode

Cache mode guarantees that every container image used by the cluster or the LZ comes from the
cache, over verified TLS, and that a gap in the cache is an error rather than a silent pull from
upstream (see [Landing Zone pulls](#landing-zone-pulls)). The current disconnected flow does not:
some images bypass the LZ mirror, TLS is not verified, and mirror misses fall back to upstream
whenever it is reachable. Following the [Design principle](#design-principle), the rows below
are the only places where cache mode differs, either to meet that guarantee or to let several
installations share a cache. LZ mode stays unchanged in this design; porting each difference is a
separate change.

| Area | LZ mode today | Cache mode | Porting to LZ mode |
|---|---|---|---|
| LZ release-payload pulls (ISO, ironic, BMO) | pulled from upstream | redirected to the cache by digest (see [Landing Zone pulls](#landing-zone-pulls)) | needs ISO extraction moved after phase 02 |
| registries.conf for Quay Enterprise seeding | user-level file from the hand-maintained `templates/registries.conf.j2` | per-run file generated from IDMS/ITMS, no user-level file (see [Registry redirection on the LZ](#registry-redirection-on-the-lz)) | needs the LZ redirection above |
| TLS verification | `tls-verify=false` for source and destination | verified against the cache CA and the system trust (see [Reusing the mirroring code](#reusing-the-mirroring-code)) | needs TLS trust for the LZ mirror |
| Source policy | no `mirrorSourcePolicy` or `blocked`: a mirror miss silently falls back to upstream when it is reachable | fail closed: `NeverContactSource` on the cluster, `blocked = true` on the LZ (see [Fail-closed source policy](#fail-closed-source-policy)) | quick fix; a latent bug today, tracked in [OSAC-7042](https://redhat.atlassian.net/browse/OSAC-7042) |
| Agent ISO registries.conf | rendered by the agent installer from install-config `imageDigestSources`, without `blocked` | `mirror/registries.conf` supplied by `bootstrap.sh`, generated from the unit IDMS/ITMS with `blocked = true` (see [Fail-closed source policy](#fail-closed-source-policy)) | part of [OSAC-7042](https://redhat.atlassian.net/browse/OSAC-7042) |
| Images of enabled plugins (`additionalImages`, Helm-extracted) | not mirrored at bootstrap; only `make deploy-plugin` mirrors them | `images-<plugin>` built by `cache.sh build` and validated for every enabled plugin | a latent bug today, tracked in [OSAC-7046](https://redhat.atlassian.net/browse/OSAC-7046) |
| Catalog destination tags | the source tag (`v4.20`); the filtered index overwrites the full copy | a tag derived from the unit key, so builds for different configs never overwrite each other (see [Metadata selection](#metadata-selection)) | not needed: an LZ mirror serves one installation |
| IDMS/ITMS applied by `sync.sh` | only through the ABI manifests at install; later repositories are covered by the Quay Enterprise IDMS/ITMS | `sync.sh` applies the units' IDMS/ITMS, so the cache mirrors every repository (see [Applying cluster resources](#applying-cluster-resources)) | optional |
| oc-mirror runs | one combined run in phase 02 | one run per unit, for per-unit keys and metadata; internal, the cluster gets the same objects (see [Reusing the mirroring code](#reusing-the-mirroring-code)) | see [Non-objectives](#non-objectives) (phase 02 as a local cache) |

Not a difference: the bootstrap host cannot fail closed in either mode (see
[Fail-closed source policy](#fail-closed-source-policy)).

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
pointer to coordinate.

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
- newest candidate metadata does not cover the required images, an older one does → older one
  selected
- two configs build the same unit with different packages → neither catalog nor metadata is
  overwritten
- missing unit → fails before changing anything
- release image missing from the cache → `bootstrap.sh` fails at the dry-run, before generating
  the ISO
- day-2 plugin with units missing / present
- oc-mirror version change → key mismatch, dry-run decides
- key matches but an image was deleted after the build → fails
- host alias / prefix → placeholder substituted correctly
- wrong CA or credentials → clear error, not an oc-mirror failure
- LZ release lookups in cache mode → no request to upstream registries
- configured cache endpoint differs from the recorded one → `sync.sh`, `upgrade.sh` and
  `deploy-plugin` fail before changing anything
- image missing from the cache while upstream is reachable → dry-run fails

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

**A moving `<unit>:latest` metadata tag and catalogs pushed to their source tag.** Each build
would replace what the previous one published, so a build for a config with fewer packages
would remove operators from catalogs and repositories from IDMS/ITMS that other installations
sharing the cache rely on. Everything a build tags is keyed instead (see
[Metadata selection](#metadata-selection)).

**Keying OCP per channel range.** Bootstrap and `sync.sh` mirror different ranges, so the same
enclave and config would produce different keys depending on the entry point.

**Switching modes on an existing installation.** The mirror entries, CatalogSource images and LZ
redirection all point at a different registry in each mode, so moving between modes would need
re-pointing all of them, as for a cache endpoint change.

**Following cache endpoint changes on sync.** Re-applying only the required units' IDMS/ITMS
leaves units no longer required, but kept on the cluster, pointing at the previous endpoint, and
with `NeverContactSource` their pulls fail once it is retired. Doing it safely needs the work
listed in [Non-objectives](#non-objectives), for an operation a stable registry DNS name avoids.

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
   `bootstrap.sh`, `sync.sh` and `upgrade.sh`, including LZ registry redirection. Removal of
   `playbooks/tasks/mirror_cache.yaml`.
1. Cache mode for day 2: Quay Enterprise seeding and `deploy-plugin` / `mirror-plugin`.
1. CI: cache host owned by `vm_infra.py`, EBS snapshot publishing and lookup, cache-mode mirror
   validation gate, and the validation scenarios.

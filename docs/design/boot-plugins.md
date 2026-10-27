# Design doc: Boot plugins

## Author/date

rporresm / 2026-10-27

## Tracking JIRA

[OSAC-6629](https://redhat.atlassian.net/browse/OSAC-6629)

## Problem Statement

Enclave boots the management cluster hosts in one way only: an ephemeral Ironic on the Landing
Zone mounts the Agent-Based Installer ISO through Redfish virtual media and powers the hosts on.
Some of the hardware used to deploy Enclave does not follow Redfish well enough for this, or BMC
access is blocked altogether. In those cases the installation gets stuck, even though an
administrator could boot the machines by other means
([GoRI #973](https://github.com/gori-project/GoRI/issues/973)).

The Redfish path is also hardwired across the codebase, which makes adding an alternative hard:

1. **Boot logic lives in core.** The Ironic tasks are included directly from
   `playbooks/03-deploy.yaml` and `playbooks/tasks/wait_for_deployment.yaml`. An alternative boot
   method can only be added by sprinkling conditionals over those files, e.g.
   [enclave PR #979](https://github.com/rh-ecosystem-edge/enclave/pull/979).
1. **Redfish settings are core configuration.** `redfish`, `redfishUser`, `redfishPassword` and
   `bmcSystemId` are required fields of every `agent_hosts` entry, and `lzBmcIP` is required
   globally, even for an installation that never talks to a BMC.
1. **Validation assumes Redfish.** `validations.sh` always checks Redfish connectivity of the
   agent hosts.
1. **Plugins are not an option as they are today.** The existing plugin system mixes very
   different things in a flat `plugins/` directory (`foundation` plugins, which are storage
   providers, and `addon` plugins), merges plugin configuration into the global variable space,
   and validates plugin structure only in CI (`scripts/verification/validate_plugins.sh`), so an
   out-of-tree plugin is never checked.

## Goals

- Booting the management cluster hosts is done by a **boot plugin** selected in configuration,
  with a default for all hosts and an optional per-host override, so mixed hardware is supported.
  Boot plugins live in `plugins/boot/<name>/` and have a code path fully separate from the
  existing plugins, including their validation.
- The current Ironic/Redfish flow becomes the `ironic` boot plugin, the default. Redfish is a
  configurable driver of that plugin, not a plugin of its own. Core no longer contains boot
  logic, so CI exercises the plugin code path on every run.
- A `manual` boot plugin lets an administrator boot the hosts by any means (BMC console, USB,
  ...) while the installation flow keeps going without interaction.
- Boot plugin configuration follows the existing convention (`config/plugins/boot/<name>.yaml`,
  validated by the plugin's own schema) and lives in its own variable namespace.
- An upgrade migration moves existing Redfish settings to the `ironic` plugin configuration.
- The boot plugin contract is validated at install time by code shipped in `src/enclave`, so
  third-party boot plugins are held to the same contract as ours.
- CI tests both plugins, picking which ones to run from the files changed in a PR.

## Non-objectives

- **Day 2 host discovery.** `playbooks/07-configure-discovery.yaml` keeps creating Redfish-backed
  BareMetalHosts for `discovery_hosts`, unchanged. The Ironic stack used for booting is ephemeral
  and torn down once the management cluster is installed, so the two paths are already
  independent. Day 2 is also a much harder problem with a different shape: the hosts are driven
  by the Bare Metal Operator, Metal3 and the ACM/MCE operators, which expect power control and
  virtual media for provisioning, reprovisioning and deprovisioning hosts over their lifetime. A
  pluggable equivalent there would mean integrating with those operators' interfaces, not
  swapping the step that boots an ISO once.
- **Migrating the existing plugins.** Moving `foundation` plugins to `plugins/storage/` and `addon`
  plugins to `plugins/addons/` is a follow-up. Until then they stay in the flat `plugins/` layout,
  and `validate_plugins.sh` keeps validating them.
- **Boot plugins other than `ironic` and `manual`.**
- **Ironic drivers other than `redfish`.** The `ironic` plugin makes the driver configurable, but
  only `redfish` is supported initially.

## Proposal

### Selection and configuration

A new optional top-level setting in `config/global.yaml` selects the default boot plugin, and each
`agent_hosts` entry can override it with the same property:

```yaml
bootPlugin: ironic       # default for all hosts; ironic when omitted
agent_hosts:
  - name: master-0       # booted by ironic
  - name: master-1       # booted by ironic
  - name: master-2
    bootPlugin: manual   # per-host override, e.g. a host whose BMC does not work
```

The per-host `bootPlugin` is validated by name pattern only, since third-party plugin names are
not known in advance; that the referenced plugins exist and honour the contract is checked by
[validation](#validation).

`schemas/global.yaml` rejects unknown top-level properties, so it gains two entries:

- `bootPlugin`: string, plugin name pattern, optional, default `ironic`.
- `bootDiscoveryTimeout`: integer, seconds, minimum 60, optional (see
  [Hook points](#hook-points)).

`agent_hosts` stays in `config/global.yaml`: almost all of its fields (`name`, `macAddress`,
`ipAddress`, `rootDisk`, `interfaceName`, `networkConfig`, `mapInterfaces`) feed the ABI
`agent-config`/`nmstateconfig` templates, host filtering, pre-install validation and plugins. Only
the four boot-only fields move out: `redfish`, `redfishUser`, `redfishPassword` and
`bmcSystemId`. They go to the `ironic` plugin configuration, keyed by host name:

```yaml
# config/plugins/boot/ironic.yaml
driver: redfish
hosts:
  master-0:                       # agent_hosts[].name
    address: 100.64.1.24          # BMC address (ip or ip:port)
    username: admin
    password: secret
    systemId: System.Embedded.1   # optional, only for multi-system BMCs
```

The boot plugin runner loads the plugin's `defaults.yaml` merged with
`config/plugins/boot/<name>.yaml` (user values win) into a single variable, `bootPluginConfig`.
Boot plugin settings therefore cannot collide with global variables, and the property-prefix rule
that `validate_plugins.sh` enforces for existing plugins is not needed.

`hostEntry` in `schemas/definitions.yaml` is shared today by `agent_hosts` and `discovery_hosts`.
It is split: the `agent_hosts` variant drops the four BMC fields and gains the optional
`bootPlugin`; `discovery_hosts` keeps the BMC fields.

`lzBmcIP`, `lzBmcHostname` and the Ironic HTTPS certificates stay in global and certificates
configuration, since the Day 2 discovery path needs them as well. `lzBmcIP` is no longer
unconditionally required by `schemas/global.yaml`; the `ironic` plugin (when any host uses it) and
the discovery path each check it explicitly. As a consequence, the `httpd` check in
`validations.sh` (which guards the LZ host httpd serving the RHCOS live ISOs) switches from
`lzBmcIP` to `quayHostname`, the address its consumer actually uses: the MCE
`AgentServiceConfig.osImages` reference the ISOs as `http://{{ quayHostname }}/...`
(`operators/multicluster-engine/configure_asc.yaml`).

### Boot plugin layout

```
plugins/boot/<name>/
  plugin.yaml              # descriptor, validated against schemas/boot-plugin.yaml
  defaults.yaml            # optional plugin defaults
  schemas/
    config.yaml            # optional, validates config/plugins/boot/<name>.yaml
    defaults.yaml          # required if defaults.yaml exists
  tasks/
    validate.yaml          # optional
    setup.yaml             # optional
    boot.yaml              # required
    post-install.yaml      # optional
    cleanup.yaml           # optional
```

The descriptor is small:

```yaml
name: manual               # must match the directory name
description: Boot hosts manually from the agent ISO
discoveryTimeout: 3600     # optional, seconds; default 600
```

Defaults live only in `defaults.yaml`; boot plugin descriptors have no `defaults:` field.

### Hook points

Core keeps everything that does not depend on how hosts are booted: ISO generation (including the
installation gate), filtering out hosts already in the cluster, waiting for the rendezvous host and
for all hosts to be `known` to Assisted Service, plugin pre-install validation, releasing the
installation gate, and `openshift-install agent wait-for`. Each boot plugin task file plugs in at a
fixed point of the existing bootstrap steps:

| Plugin file | Bootstrap step | Where it is included | Replaces today |
|---|---|---|---|
| `tasks/validate.yaml` | `validate` | config validation, after the boot plugin contract and configuration checks | Redfish checks in `validations.sh` |
| `tasks/setup.yaml` | `acquire-hardware` | `03-deploy.yaml` (`hardware` tag), after host filtering; skipped when no host needs provisioning | `configure_hardware_ironic_setup.yaml` |
| `tasks/boot.yaml` | `acquire-hardware` | right after `setup.yaml`; skipped when no host needs provisioning | `configure_hardware_ironic_boot.yaml`, `configure_hardware_ironic_wait.yaml` |
| `tasks/post-install.yaml` | `deploy` | `wait_for_deployment.yaml`, after `install-complete` | `configure_hardware_ironic_detach_vmedia.yaml` |
| `tasks/cleanup.yaml` | `deploy` | `wait_for_deployment.yaml`, after `post-install.yaml` | `configure_hardware_ironic_cleanup.yaml` |

The runner groups the hosts by their effective `bootPlugin` and runs each hook once per plugin in
use, with that plugin's hosts only:

- `validate.yaml`, `post-install.yaml` and `cleanup.yaml` run for every plugin in use.
- `setup.yaml` and `boot.yaml` run, one plugin after the other, for every plugin with at least one
  host to provision. Plugins are ordered by `discoveryTimeout`, largest first: a plugin that waits
  for its hosts (as `ironic` waits for its nodes to become active) would otherwise delay the
  instructions of a human-driven plugin such as `manual`.

Variables available to every hook:

- `bootPluginConfig`: the configuration of the plugin being run, set before each plugin's hooks
- `bootHosts`: the `agent_hosts` entries assigned to that plugin
- `workingDir`
- `bootIsoPath` (`<workingDir>/ocp-cluster/abi-iso/agent.x86_64.iso`)

`setup.yaml` and `boot.yaml` also get `bootHostsToProvision`, the subset of `bootHosts` not
already in the cluster. Hooks never read `agent_hosts` directly. `boot.yaml` receives its whole host
list and loops itself if it needs to.

Each plugin serves the ISO in whatever way it needs; core only guarantees `bootIsoPath`.
`post-install.yaml` and `cleanup.yaml` run on every deployment, so they must be idempotent.

Between `boot.yaml` and `post-install.yaml`, core runs `pre_install_validate_plugins.yaml`: it
waits for the rendezvous host and for all hosts to be `known`, then releases the installation gate.
Two things change there:

- **Timeouts.** Today both waits are hardcoded (600 seconds, and 60 × 5 seconds), which is fine
  when Ironic powers on all hosts at once but not when a person boots them one after another. Both
  now use, in order of precedence: the user's global `bootDiscoveryTimeout`; otherwise the largest
  `discoveryTimeout` among the plugins in use; otherwise 600 seconds.
- **Host identity.** Today the check only counts hosts (at least as many registered and `known`
  hosts as `agent_hosts` entries) and never compares them with `agent_hosts`. With `manual`, a
  person picks the machines, so booting a wrong or extra machine from the ISO is a realistic
  mistake that the count would not catch. Core instead waits until every `agent_hosts` entry is
  `known`, matching Assisted Service host records by MAC address (`macAddress`, or the MACs in
  `mapInterfaces`, against the interfaces in the host inventory). It fails immediately, with a
  message naming the host, if a host registers whose MACs match no `agent_hosts` entry.

### The `ironic` boot plugin

```
plugins/boot/ironic/
  plugin.yaml
  defaults.yaml            # driver: redfish
  schemas/config.yaml      # driver enum [redfish], hosts map
  schemas/defaults.yaml
  tasks/validate.yaml      # Redfish connectivity and systemId checks, out-of-config dependencies
  tasks/setup.yaml         # deploy ephemeral Ironic (shared library)
  tasks/boot.yaml          # enroll nodes, set boot_iso, deploy, wait for active
  tasks/post-install.yaml  # detach virtual media
  tasks/cleanup.yaml       # tear down ephemeral Ironic (shared library)
```

The existing `configure_hardware_ironic_{boot,wait,detach_vmedia}.yaml` tasks move into the
plugin. The hardcoded `driver: redfish`, `boot_interface: redfish-virtual-media` and
`driver_info.redfish_*` node fields become driven by `bootPluginConfig.driver` and
`bootPluginConfig.hosts`. Ironic's own httpd keeps serving the ISO, as today (the ISO directory is
mounted at `/iso` and referenced as `file:///iso/agent.x86_64.iso` in `instance_info.boot_iso`).
The per-node virtual media detach work belongs to this plugin's `post-install.yaml`.

`tasks/validate.yaml` ports the Redfish checks from `validations.sh` to Ansible. It also checks
explicitly every setting the plugin needs from outside its own configuration file: `lzBmcIP`,
`lzBmcHostname` and the Ironic certificates when HTTPS is configured, and that the `hosts` keys
match the names in `bootHosts` exactly, so hosts booted by another plugin need no BMC entry (the
plugin also reads `macAddress` from `bootHosts` to create the Ironic ports).

### Shared Ironic tasks library

Some Ironic tasks are used both by the boot path and by Day 2 (`07-configure-discovery.yaml` and
`playbooks/tasks/upgrade/metal3-components.yaml`). They move to `playbooks/tasks/ironic/`, and
both the `ironic` boot plugin and the Day 2 playbooks include them from there:

- `deploy_metal3_common.yaml`
- `deploy_ironic.yaml`, with `deploy_ironic_containers.yaml` and `deploy_ironic_quadlet_units.yaml`
- the ephemeral teardown currently in `configure_hardware_ironic_cleanup.yaml`

The existing `metal3_persistent` switch remains the mode selector (unset: ephemeral rootless pod
for booting; `true`: persistent root quadlets for Day 2). The library's input variables are
documented in a header comment, since out-of-tree boot plugins may include it too. The Bare Metal
Operator tasks (`deploy_bmo*.yaml`) are Day 2 only and stay where they are.

### The `manual` boot plugin

```
plugins/boot/manual/
  plugin.yaml              # discoveryTimeout: 3600
  tasks/boot.yaml
  tasks/post-install.yaml
```

No configuration file, no setup and no cleanup. `tasks/boot.yaml` prints what the administrator
needs: `bootIsoPath`, and the name and MAC address of each host to boot. It tells them to boot each
host from the ISO with a **one-time boot** option, and that the playbook continues on its own once
all hosts have registered. `tasks/post-install.yaml` reminds them to remove the boot media.

**There is no pause.** The plugin does not wait for the administrator to press a key. The core
wait for the rendezvous host and for all hosts to be `known`, bounded by `discoveryTimeout`, is
already the synchronization point: an interactive prompt would add nothing, and it would break
non-interactive runs, including CI.

**Boot media must not stay as a permanent boot device.** After the installation is complete, the
masters reboot several times (for instance on MachineConfig changes). If the ISO is still attached
and set as a permanent boot device, a host boots the agent installer again. The `ironic` plugin
avoids this with a one-time boot and by detaching the media after `install-complete`; with
`manual`, this is the administrator's responsibility, which the plugin's instructions make
explicit.

### Validation

Boot plugin validation has three layers:

| Layer | What | Owner | When |
|---|---|---|---|
| 1. Contract | Does the directory honour the boot plugin contract? | core, `src/enclave` | install time (every plugin referenced by `bootPlugin`) and CI (all in-repo boot plugins) |
| 2. Configuration | `config/plugins/boot/<name>.yaml` against the plugin's `schemas/config.yaml` | core runs it, the plugin's schema holds the rules | install time, `validate` step |
| 3. Preflight | Plugin-specific runtime checks, e.g. BMC reachability | the plugin's `tasks/validate.yaml` | install time, `validate` step |

Layer 1 is a new `enclave plugins validate --type boot <dir>` command. It is a new top-level
`plugins` group of the `enclave` CLI, not part of `enclave tools`, whose commands assume an
existing cluster. The `enclave` CLI is already installed on the Landing Zone and called from
playbooks, so the same checks run on every installation, including for third-party boot plugins
dropped into `plugins/boot/`. Checks:

- `plugin.yaml` exists and validates against `schemas/boot-plugin.yaml`, a core-owned schema that
  is part of the contract.
- `name` matches the directory name, and the directory is under `plugins/boot/`.
- `tasks/boot.yaml` exists, and `tasks/` contains only the known hook files, so a misspelled hook
  such as `post_install.yaml` fails instead of being silently skipped. Each is a valid YAML task
  list.
- `schemas/` contains only `config.yaml` and `defaults.yaml`, each a valid JSON Schema.
  `defaults.yaml` exists if and only if `schemas/defaults.yaml` exists, and validates against it.
- No other files.

The content of a plugin's own schemas is the plugin's business; layer 1 only checks they are
valid JSON Schema. `schemas/boot-plugin.yaml` ships with the `enclave` package as package data,
or is read from the repository on the Landing Zone.

`validate_plugins.sh` keeps validating `foundation` and `addon` plugins. When they are migrated to
`plugins/storage/` and `plugins/addons/`, each gets its own `--type` in the same command and the
shell script is removed, so every plugin is validated at install time.

### Coexistence with existing plugins

Boot plugins live one level deeper than the `foundation`/`addon` discovery looks: the recursive
`plugin.yaml` searches use `depth: 2` and do not reach `plugins/boot/<name>/plugin.yaml`, and they
also filter by `type` or `enabled_plugins`, so they ignore boot plugins. `load-vars.yaml` lists
`plugins/boot` as a directory but keeps only names in `enabled_plugins`, which never contains
`boot`. Two directory-level scanners have no such filter and must skip `plugins/boot`:

- `playbooks/validation/tasks/defaults_schema_validation.yaml`: its directory pass (`depth: 1`)
  would look up `plugins/boot/plugin.yaml`, which does not exist, and fail.
- `scripts/verification/validate_plugins.sh`: it iterates every directory under `plugins/` and
  would validate `plugins/boot` as a `foundation`/`addon` plugin.

`boot` becomes a reserved name: no `foundation`/`addon` plugin may be called `boot`.

### Upgrade migration

A versioned migration under `playbooks/tasks/migrations/` moves `redfish`, `redfishUser`,
`redfishPassword` and `bmcSystemId` from each `agent_hosts` entry into
`config/plugins/boot/ironic.yaml`. It runs only if any `agent_hosts` entry still has those fields.
`bootPlugin` is not written, since `ironic` is the default. `discovery_hosts` is not touched.

It is the first migration that rewrites user configuration, and the migration runner only records
a migration once it succeeds, so a failed run is retried. The migration is therefore safe to run
again:

1. For each host, the BMC fields are merged into `hosts.<name>` of `ironic.yaml`: added if the
   entry is missing, left as is if it holds identical values. If it holds different values, the
   migration fails with a message naming the host and changes nothing.
1. `ironic.yaml` is written first, then the fields are removed from `global.yaml`. Each file is
   written atomically (temporary file, then rename), after keeping a backup of the original.
1. A run interrupted between the two writes leaves the values in both files. The next run finds
   identical values, skips the merge and only removes them from `global.yaml`, so credentials are
   neither lost nor duplicated.

Diagnostics redaction (`scripts/diagnostics/gather_lz.sh`, which redacts known password fields of
`global.yaml`) must cover the new file.

### CI

**Boot plugin selection.** The boot plugin becomes a second matrix dimension in
`.github/workflows/e2e-deployment.yml`, next to `storage-plugin`. A new `boot_plugins` output of
`.github/actions/check-e2e-needed` feeds it, based on three groups of paths:

- manual: `plugins/boot/manual/**`, the CI operator script
- ironic: `plugins/boot/ironic/**`, `playbooks/tasks/ironic/**`
- shared: everything else the boot flow depends on, so that a change there tests both plugins:
  - the boot plugin runner and `schemas/boot-plugin.yaml`
  - the boot plugin validation code in `src/enclave`
  - `playbooks/03-deploy.yaml` and `playbooks/tasks/wait_for_deployment.yaml` (hook points)
  - `playbooks/tasks/configure_ocp_abi.yaml` and `templates/agent-config.yaml.j2` (ISO and
    installation gate)
  - `playbooks/tasks/configure_hardware_filter_hosts.yaml`
  - `playbooks/tasks/pre_install_validate_plugins.yaml` (discovery waits and gate release)
  - `scripts/infrastructure/generate_enclave_vars.sh` (writes the boot plugin configuration)
  - `.github/actions/check-e2e-needed/**` (the rule itself)

`manual` runs if manual or shared files changed. `ironic` runs unless the only E2E-relevant changes
are manual files. A change that touches nothing boot-related runs `ironic`, as today. paths-filter
quantifiers cannot express "every E2E-relevant change is under X", so the decision is computed in
the existing bash step from the changed-file list (`list-files: json`).

Scheduled runs only use `ironic`: `manual` coverage comes from the path rule, which avoids spending
a daily run on it.

| Trigger | Mode | Boot plugins |
|---|---|---|
| PR | disconnected (the only mode run on PRs) | path rule |
| Nightly | connected | `ironic` |
| Nightly | disconnected | `ironic` |

Callers that force a run (OSAC, schedule, dispatch) get a `boot-plugin` input defaulting to
`ironic`, like `storage-plugin`. `ENCLAVE_CLUSTER_SEED`, the concurrency group and the job name
include the boot plugin: otherwise two boot plugin jobs in the same run and mode would get the same
cluster name on separate runners (`vm_infra.py` only avoids clashes on the same host), and their
diagnostics artifacts would clash.

**Configuration.** A `BOOT_PLUGIN` environment variable is consumed by
`scripts/infrastructure/generate_enclave_vars.sh`, which writes the top-level `bootPlugin:` into
`config/global.yaml`. For `ironic` it writes `config/plugins/boot/ironic.yaml` instead of the
Redfish fields in `agent_hosts`; for `manual` it writes no plugin file. Unlike `STORAGE_PLUGIN`,
the value must not go through `extra_vars.yaml`: config validation only reads the configuration
files.

**Exercising `manual`.** In CI there is no person to boot the hosts, so a new script
`scripts/deployment/manual_boot_operator.sh` takes their place. Enclave and the `manual` plugin are
unaware of it. The CI hosts are libvirt VMs whose BMC is sushy-tools, so the script calls
sushy-tools' Redfish API, as a person would click through a BMC console. Enclave touches no BMC,
and no libvirt resource is changed outside `vm_infra.py`. It runs as two workflow steps, only when
the boot plugin is `manual`:

1. `make -f Makefile.ci manual-boot-hosts`, after "Build local cache" (which already builds the
   ISO) and before "Acquire hardware". It copies the ISO into `/var/www/html` on the Landing Zone,
   which the LZ host httpd serves (sushy-tools only accepts HTTP(S) URLs). Then, for each master,
   it inserts the ISO and powers the VM on. The script finds the virtual media endpoint by
   following Redfish links (`Systems/<id>` → `VirtualMedia` → `Cd`) rather than a hardcoded path,
   since the sushy-tools image is not pinned. Booting before the plugin runs is safe: the
   installation gate holds the installation until Enclave has validated the hosts.
1. `make -f Makefile.ci manual-eject-media`, after "Deploy cluster", ejects the ISO, as Ironic does
   today after `install-complete`. The VMs boot from cdrom first, so without this the later
   MachineConfig reboots would boot the agent ISO again.

No boot override call is needed: sushy-tools runs with `SUSHY_EMULATOR_IGNORE_BOOT_DEVICE`, so the
VMs always follow the `cdrom, hd` boot order defined by `vm_infra.py`. The script logs to the
working directory, and `collect_ci_artifacts.sh` adds the log to the diagnostics bundle.

**Other CI script changes.** `scripts/verification/validate.sh` expects the task
`Configure and boot hosts via Ironic` behind the `hardware` tag of `03-deploy.yaml`, which becomes
the boot plugin runner. `collect_ci_artifacts.sh` needs no change for configuration: it already
collects `config/plugins/` recursively and redacts `password` keys.

**Prerequisite.** The master VM UUIDs (needed for `systemId`, and by the CI operator) come from
`cluster-env.sh` (`ENCLAVE_MASTER_<i>_UUID`): `vm_infra.py` sets deterministic domain UUIDs and
emits them, replacing the `virsh domuuid` call in `generate_enclave_vars.sh`
([PR #1011](https://github.com/rh-ecosystem-edge/enclave/pull/1011), OSAC-6911).

## Alternatives considered

**Boot plugins as a new `type: boot` in the flat `plugins/` directory**
([PR #979](https://github.com/rh-ecosystem-edge/enclave/pull/979)). It keeps the Ironic flow in core
next to a plugin path, so two boot code paths coexist and CI only exercises the default one. It
also mixes a third kind of plugin into a directory and schema shared with very different ones.

**Flags guarding the Redfish steps.** It adds conditionals to core files and skips the
corresponding validations instead of moving the logic.

**Extension point inside Ironic.** Ironic already abstracts hardware through drivers, and it stays
the engine of the `ironic` plugin. But a `manual` boot gives Ironic nothing to drive, and custom
behaviour inside Ironic would mean custom Python drivers in the Ironic image.

**Moving `agent_hosts` to the boot plugin configuration.** Most `agent_hosts` fields are used
outside booting. Only the four BMC fields are boot-specific.

**A single boot plugin for all hosts.** Simpler runner, but a management cluster with one host whose
BMC does not work could not use `ironic` for the others. Supporting the per-host override from the
start costs the grouping loop, the plugin ordering and the largest-timeout rule, and keeps the hook
contract (`bootHosts`) stable.

**Pausing in the `manual` plugin.** See [The `manual` boot plugin](#the-manual-boot-plugin).

**Exercising `manual` in CI through libvirt directly**, attaching the ISO and starting the VMs
from a CI script. It would change libvirt resources outside `vm_infra.py`, or need new `vm_infra.py`
commands only for this. sushy-tools is already the VMs' BMC.

## Milestones

1. Boot plugin runner and hook points, `schemas/boot-plugin.yaml`, `enclave plugins validate --type
   boot`, exclusion of `plugins/boot/` from the existing plugin discovery, shared Ironic tasks
   library, `ironic` boot plugin, upgrade migration and the CI configuration changes. No change in
   behaviour for existing installations.
1. `manual` boot plugin, `manual_boot_operator.sh`, and the CI boot plugin matrix and path rule.

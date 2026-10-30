# Enclave Boot Plugin Architecture

## Overview

A boot plugin boots the management cluster hosts (`agent_hosts` in `config/global.yaml`) from the
Agent-Based Installer ISO. Enclave ships the `ironic` boot plugin, which drives the hosts' BMCs
through an ephemeral Ironic on the Landing Zone. Other boot plugins, in-tree or third-party, plug
into the same hook points without changes to core.

Boot plugins live under `plugins/boot/` and have their own contract, runner and validation, separate
from the foundation and addon plugins described in [Plugin Architecture](PLUGIN_ARCHITECTURE.md).
They only cover the initial installation of the management cluster: Day 2 discovery of
`discovery_hosts` (`playbooks/07-configure-discovery.yaml`) is not a boot plugin.

The boot plugin of each host is selected in `config/global.yaml`:

```yaml
bootPlugin: ironic       # default for all hosts; ironic when omitted (defaults/deployment.yaml)
agent_hosts:
  - name: master-0       # booted by ironic
  - name: master-1
    bootPlugin: vendor-x # per-host override
```

Core and boot plugins split the work as follows:

| Core | Boot plugins |
|------|--------------|
| ISO generation, including the installation gate | Validating their own settings and runtime dependencies |
| Filtering out hosts already in the cluster | Setting up whatever they need to boot hosts (e.g. Ironic) |
| Grouping hosts by boot plugin and running the hooks | Booting their hosts from the ISO |
| Waiting for every host to register with Assisted Service, matched by MAC | Anything needed once the cluster is installed (e.g. detaching media) |
| Releasing the installation gate, `openshift-install agent wait-for` | Undoing their setup |

The design and its rationale are in [docs/design/boot-plugins.md](design/boot-plugins.md). This
document describes what is implemented.

## Directory Structure

A boot plugin is a directory under `plugins/boot/` named after the plugin:

```
plugins/boot/<name>/
  plugin.yaml              <- the descriptor (required)
  defaults.yaml            <- plugin defaults (optional)
  schemas/
    config.yaml            <- JSON Schema validating config/plugins/boot/<name>.yaml (optional)
    defaults.yaml          <- JSON Schema validating defaults.yaml (required if defaults.yaml exists)
  tasks/
    validate.yaml          <- preflight checks (optional)
    setup.yaml             <- set up what booting needs (optional)
    boot.yaml              <- boot the hosts (required)
    post-install.yaml      <- after the cluster is installed (optional)
    cleanup.yaml           <- undo setup.yaml (optional)
    lib/                   <- task files included by the hooks only (optional)
```

Nothing else is allowed: a misspelled hook such as `tasks/post_install.yaml` fails validation
instead of being silently skipped. Fixtures for the plugin schemas live outside the plugin, under
`test-fixtures/plugins/boot/<name>/`.

### The Descriptor: `plugin.yaml`

```yaml
name: ironic               # must match the directory name
type: boot                 # always boot
description: Boot hosts from the agent ISO through an ephemeral Ironic on the Landing Zone
discoveryTimeout: 3600     # optional, seconds (minimum 60); default 600
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Plugin name, matching the directory name |
| `type` | Yes | Always `boot`. Code that reads every `plugin.yaml` under `plugins/` and filters on `type` (such as the applied catalog migrations) then skips boot plugins |
| `description` | Yes | One line describing how the plugin boots hosts |
| `discoveryTimeout` | No | Seconds core waits for the plugin's hosts to register with Assisted Service. A plugin driven by a person (booting hosts by hand) needs a larger value than one driven by a BMC |

The descriptor is validated against `schemas/boot-plugin.yaml`, which ships with the `enclave`
package. Descriptors have no `defaults:` field: defaults live only in `defaults.yaml`.

## Configuration

User configuration of a boot plugin goes in `config/plugins/boot/<name>.yaml`, validated against
the plugin's `schemas/config.yaml`. These files hold BMC credentials: they are ignored by git, and
should be created with mode 600:

```bash
install -m 600 config/plugins/boot/ironic.example.yaml config/plugins/boot/ironic.yaml
```

Before each hook, core loads the plugin's `defaults.yaml` merged with its configuration file (user
values win) into a single variable, `bootPluginConfig`. Boot plugin settings therefore never collide
with global variables, and need no name prefix.

## Boot Lifecycle

Each hook plugs into a fixed point of the bootstrap steps (`bootstrap.sh --step <step>`):

```
validate            config validation (schemas, unique MACs)
                    boot plugin validation:
                      contract and configuration of each boot plugin in use
                      tasks/validate.yaml               <- every plugin in use

build-cache         ISO generation with the installation gate

acquire-hardware    filter out hosts already Ready in the cluster
                    for each plugin with hosts to provision, largest discoveryTimeout first:
                      tasks/setup.yaml
                      tasks/boot.yaml
                    wait for the rendezvous host
                    wait until every agent host is matched by MAC and known
                    pre-install-validate.yaml of enabled foundation/addon plugins
                    release the installation gate

deploy              openshift-install agent wait-for bootstrap-complete / install-complete
                    tasks/post-install.yaml           <- every plugin in use
                    tasks/cleanup.yaml                <- every plugin in use, even if a
                                                         post-install hook failed
```

Plugins with hosts to provision run one after the other. Ordering by `discoveryTimeout` makes a
plugin that waits on a person print its instructions before a plugin that blocks waiting for its
hosts (as `ironic` does until its nodes are active).

### Hook Reference

| Hook | Runs | Hosts | Expected of it |
|------|------|-------|----------------|
| `tasks/validate.yaml` | `validate` step, after the contract and configuration checks | `bootHosts` | Check every setting the plugin needs, including settings from outside its configuration file (e.g. global variables), and runtime dependencies such as BMC reachability. Fail early, before downloads and mirroring |
| `tasks/setup.yaml` | `acquire-hardware`, only if the plugin has hosts to provision | `bootHosts`, `bootHostsToProvision` | Prepare what booting needs. Idempotent |
| `tasks/boot.yaml` | `acquire-hardware`, right after `setup.yaml` | `bootHosts`, `bootHostsToProvision` | Boot every host in `bootHostsToProvision` from the ISO. It may wait for its hosts, but must not wait for input: core's discovery wait is the synchronization point |
| `tasks/post-install.yaml` | `deploy`, after `install-complete` | `bootHosts` | Work that needs the installed cluster, such as detaching boot media. Runs on every deployment, including reruns where `setup.yaml` did not run, so it must not fail for that reason |
| `tasks/cleanup.yaml` | `deploy`, after every `post-install.yaml` | `bootHosts` | Undo exactly what `setup.yaml` did. Idempotent, and must not fail when `setup.yaml` did not run |

`post-install.yaml` is not limited to hosts provisioned in the current run: a run can stop after
`install-complete` but before `post-install.yaml`, and on the rerun every host is already in the
cluster.

### Variables Available to the Hooks

| Variable | Description |
|----------|-------------|
| `bootPluginConfig` | The plugin's `defaults.yaml` merged with `config/plugins/boot/<name>.yaml` |
| `bootHosts` | The `agent_hosts` entries booted by the plugin |
| `bootHostsToProvision` | `setup.yaml` and `boot.yaml` only: the `bootHosts` not yet Ready in the cluster |
| `bootIsoPath` | The agent ISO on the Landing Zone (`<workingDir>/ocp-cluster/abi-iso/agent.x86_64.iso`) |
| `workingDir` | The Enclave working directory |

Hooks read hosts only from `bootHosts` and `bootHostsToProvision`, never from `agent_hosts`. Each
plugin serves the ISO in whatever way it needs; core only guarantees `bootIsoPath`.

## Core Around the Hooks

| File | Role |
|------|------|
| `playbooks/tasks/boot/load_plugins.yaml` | Groups `agent_hosts` by effective `bootPlugin`, loads the descriptors, orders the plugins by `discoveryTimeout` and sets the discovery timeout |
| `playbooks/tasks/boot/run_hook.yaml` | Runs one hook of one plugin if the plugin has it, with `bootPluginConfig` and the host variables |
| `playbooks/tasks/boot/validate.yaml` | Boot plugin validation (see [Validation](#validation)) |
| `playbooks/tasks/boot/boot_hosts.yaml`, `provision_plugin.yaml` | `setup.yaml` and `boot.yaml` of each plugin with hosts to provision (`03-deploy.yaml`, `hardware` tag) |
| `playbooks/tasks/boot/finish_hosts.yaml` | `post-install.yaml` of every plugin, then `cleanup.yaml` of every plugin in an `always` block (`wait_for_deployment.yaml`) |
| `playbooks/tasks/pre_install_validate_plugins.yaml` | Discovery waits and installation gate release |
| `playbooks/validation/tasks/boot_plugins_validation.yaml` | Entry point of the `validate-boot-plugins` tag of `playbooks/validation/validate-schema.yaml` |

### Host Identity

After the boot hooks, core waits until every `agent_hosts` entry is registered with Assisted Service
and in status `known`, with `enclave boot wait-for-hosts` (`src/enclave/boot/`). It matches the host
records, requested with their inventory, to `agent_hosts` by MAC address (`macAddress` and the MACs
in `mapInterfaces`). The match must be one-to-one, and the wait fails at once, naming the hosts, if:

- a registered host matches no `agent_hosts` entry (e.g. the wrong machine was booted)
- a registered host matches more than one entry
- an entry is matched by more than one registered host

MAC addresses must therefore be unique across `agent_hosts`, which config validation checks.

### Discovery Timeout

The wait for the rendezvous host and the wait for the hosts use, in order of precedence:

1. `bootDiscoveryTimeout` in `config/global.yaml`
1. the largest `discoveryTimeout` among the boot plugins in use
1. 600 seconds (the default `discoveryTimeout`)

## Validation

Boot plugins are validated in three layers:

| Layer | What | When |
|-------|------|------|
| 1. Contract | `enclave plugins validate --type boot <dir>...` (`src/enclave/plugins/boot.py`) | Install time, for every plugin in use; CI (`make -f Makefile.ci validate-plugins`), for every plugin under `plugins/boot/` |
| 2. Configuration | `config/plugins/boot/<name>.yaml` against the plugin's `schemas/config.yaml` | Install time, for every plugin in use; schema fixtures in CI (`make -f Makefile.ci validate-json-schema`) |
| 3. Preflight | The plugin's `tasks/validate.yaml` | Install time, for every plugin in use |

The contract check verifies that:

- `plugin.yaml` validates against `schemas/boot-plugin.yaml`, and `name` matches the directory name
- the directory is under `plugins/boot/`
- `tasks/boot.yaml` exists, `tasks/` holds only the hook files and `lib/`, and `tasks/lib/` holds
  only YAML files; every task file is a YAML list of tasks
- `schemas/` holds only `config.yaml` and `defaults.yaml`, each a valid JSON Schema
- `defaults.yaml` exists if and only if `schemas/defaults.yaml` does, and validates against it
- there are no other files

Install-time validation runs in the bootstrap `validate` step under its own tag,
`validate-boot-plugins`. `sync.sh` does not run it, so it keeps working on an installation whose
configuration has not been migrated by `upgrade.sh` yet.

Configuration fixtures go under `test-fixtures/plugins/boot/<name>/config/valid/` (must pass) and
`.../config/invalid/` (must fail).

## Coexistence with Foundation and Addon Plugins

- `boot` is a reserved plugin name: no foundation or addon plugin may be called `boot`.
- The discovery of foundation and addon plugins passes its `find` paths through `realpath`, so the
  `depth` limits hold and `plugins/boot/<name>/plugin.yaml` is never read as a foundation or addon
  descriptor. The directory-level scanners (`defaults_schema_validation.yaml`,
  `scripts/verification/validate_plugins.sh`) skip `plugins/boot`.
- The applied catalog migrations are immutable and still read every `plugin.yaml` two levels deep;
  their `type: foundation` filter skips boot plugins thanks to `type: boot`.

## The `ironic` Boot Plugin

The `ironic` plugin boots each host through its BMC: it deploys an ephemeral Ironic on the Landing
Zone, enrolls each host as an Ironic node with the agent ISO as its boot ISO, and deploys it, which
powers the host on from virtual media.

```
plugins/boot/ironic/
  plugin.yaml
  schemas/config.yaml               <- hosts map, one host definition per driver
  tasks/validate.yaml               <- out-of-config dependencies, then each driver's checks
  tasks/setup.yaml                  <- deploy the ephemeral Ironic
  tasks/boot.yaml                   <- enroll, set the boot ISO, deploy, wait for active
  tasks/post-install.yaml           <- detach virtual media
  tasks/cleanup.yaml                <- tear down the ephemeral Ironic
  tasks/lib/settings.yaml           <- Ironic settings of each host, from its driver
  tasks/lib/redfish-settings.yaml   <- redfish driver: node settings
  tasks/lib/redfish-validate.yaml   <- redfish driver: preflight checks
```

### Configuration

```yaml
# config/plugins/boot/ironic.yaml
hosts:
  master-0:                         # agent_hosts[].name
    driver: redfish
    address: bmc0.example.com       # [http(s)://]host[:port], https when no scheme
    username: admin
    password: secret
    redfish:                        # only valid for driver redfish
      systemId: System.Embedded.1   # optional, only for multi-system BMCs
```

The `hosts` keys must match exactly the names of the `agent_hosts` booted by `ironic`: hosts booted
by another boot plugin have no entry. See
[CONFIGURATION_REFERENCE.md](CONFIGURATION_REFERENCE.md#configpluginsbootironicyaml) for every field.

### Hooks

**`validate.yaml`** checks, before anything is downloaded:

- `lzBmcIP`, and that `ironicHTTPSCertificate` and `ironicHTTPSKey` are both set or both unset
- that the `hosts` keys match the names in `bootHosts`, and that every host has a `macAddress`
  (used for its Ironic port)
- the driver-specific checks: `tasks/lib/<driver>-validate.yaml` for the hosts of each driver in use

**`setup.yaml`** and **`cleanup.yaml`** use the shared Ironic tasks library in
`playbooks/tasks/ironic/`, which the Day 2 discovery and upgrade playbooks also use.
`metal3_persistent` selects the mode: unset, as here, deploys an ephemeral rootless `metal3-ironic`
pod whose Ironic mounts the ISO directory at `/iso`; `true` (Day 2) deploys persistent root
quadlets. The library documents its input variables in each file's header. `cleanup.yaml` removes
the ephemeral pod (`teardown_ephemeral.yaml`) and does nothing if it does not exist.

**`boot.yaml`**, for `bootHostsToProvision`:

1. Computes each host's Ironic settings from its driver (`tasks/lib/settings.yaml`).
1. Creates the missing Ironic nodes with their driver, boot interface and `driver_info`, without the
   BMC password, so the task output stays debuggable.
1. Replaces the whole `driver_info`, password included, on every node not deployed yet (states
   `enroll`, `manageable`, `available`), in a single request. Only this task has `no_log`; its
   outcome is reported from records holding only the node name, the HTTP status and Ironic's error.
   A node left by an earlier run therefore gets the current settings, never the password with a
   stale address. Nodes whose deployment is in progress are left alone.
1. Creates the ports (the host MAC), moves the nodes to `manageable` and `available`, sets
   `instance_info.boot_iso` to `file:///iso/agent.x86_64.iso` and deploys them (`active`), then waits
   until every node is `active`.

Nodes are booted from the ISO only once (`force_persistent_boot_device: Never`).

**`post-install.yaml`** detaches the virtual media of every host in `bootHosts` known to the running
ephemeral Ironic, if the host's driver supports detaching it. It never fails because a host cannot be
detached; it warns, naming the hosts, when:

- the ephemeral Ironic is not running (e.g. a rerun after the run that booted the hosts)
- a host is not enrolled in the running Ironic (it was not provisioned in this run)
- a host's driver does not support detaching virtual media

It does not enroll hosts just to detach media, which would put hosts running in the cluster under
Ironic management. Detaching waits until `install-complete` on purpose: on some hardware
coreos-installer streams the disk image from the mounted ISO, and detaching earlier aborts the
disk write. Leftover media only matters on hardware that ignores one-time boot.

## Ironic Drivers

Each host selects its Ironic hardware type with `driver`. Only `redfish` is supported; the
configuration shape and the code are organized so that a driver can be added without changing
existing files.

### Configuration Schema

`plugins/boot/ironic/schemas/config.yaml` gives each driver a complete host definition: the common
fields (`address`, `username`, `password`) plus a section named after the driver for its own
settings. `driver` selects the definition:

```yaml
definitions:
  host:
    properties:
      driver:
        enum: [redfish]
    required: [driver]
    allOf:
      - if: {properties: {driver: {const: redfish}}}
        then: {"$ref": "#/definitions/redfishHost"}
  redfishHost:
    additionalProperties: false
    properties:
      driver: {const: redfish}
      address: {...}               # [http(s)://]host[:port]
      username: {...}
      password: {...}
      redfish:                     # Redfish-only settings
        properties:
          systemId: {...}
    required: [address, username, password]
```

A host can only carry the section of its own driver, and settings outside it (such as `systemId` at
the host level) are rejected.

### Driver Implementation

The driver-specific code lives in `tasks/lib/`, as two task files per driver:

| File | Input | Does |
|------|-------|------|
| `tasks/lib/<driver>-settings.yaml` | `_driver_hosts`: the hosts of the driver | Adds each host's Ironic settings to `_ironic_settings` |
| `tasks/lib/<driver>-validate.yaml` | `_driver_hosts` | The driver's preflight checks |

`tasks/lib/settings.yaml` runs `<driver>-settings.yaml` for every driver among the hosts it is given
and leaves, for each host:

| Key | Description |
|-----|-------------|
| `driver` | Ironic hardware type |
| `boot_interface` | Ironic boot interface that boots the ISO from virtual media |
| `driver_info` | Ironic `driver_info`, without the BMC password |
| `password_field` | `driver_info` field that holds the BMC password |
| `vmedia_detach` | Whether Ironic can detach the driver's virtual media (vmedia API) |

`boot.yaml` and `post-install.yaml` only use these settings, so they stay driver-agnostic.

The plugin boots the ISO from virtual media, so only drivers with a virtual media boot interface fit.
`ipmi` has none: it would need PXE and DHCP, which is a different boot mechanism, closer to another
boot plugin than to another driver.

### The `redfish` Driver

| Setting | Value |
|---------|-------|
| Hardware type and boot interface | `redfish`, `redfish-virtual-media` |
| `redfish_address` | `address`, prefixed with `https://` when it has no scheme |
| `redfish_username` | `username` |
| `redfish_password` | `password` (set in the separate PATCH) |
| `redfish_system_id` | `/redfish/v1/Systems/<redfish.systemId>`, only when `systemId` is set |
| `redfish_verify_ca` | `false`; optional certificate verification is tracked in OSAC-6973 |
| `force_persistent_boot_device` | `Never` |
| Virtual media detach | Supported |

`tasks/lib/redfish-validate.yaml` queries `<address>/redfish/v1/Systems` on each BMC with its
credentials, and checks that `redfish.systemId` is one of the BMC's systems when set, or that the
BMC manages a single system when it is not.

### Adding a Driver

1. Add the driver to the `driver` enum in `plugins/boot/ironic/schemas/config.yaml`, with a
   `<driver>Host` definition (common fields plus a `<driver>:` section) and an `if`/`then` entry
   selecting it.
1. Add `tasks/lib/<driver>-settings.yaml`, setting every key listed in
   [Driver Implementation](#driver-implementation) for each host in `_driver_hosts`.
1. Add `tasks/lib/<driver>-validate.yaml` with the driver's preflight checks.
1. Make sure the Ironic image enables the hardware type and its virtual media boot interface.
1. Add valid and invalid fixtures under `test-fixtures/plugins/boot/ironic/config/`, and update
   `config/plugins/boot/ironic.example.yaml` and
   [CONFIGURATION_REFERENCE.md](CONFIGURATION_REFERENCE.md#configpluginsbootironicyaml).

Existing configuration files keep working unchanged.

## Upgrading from Redfish Settings in `agent_hosts`

Before boot plugins, the BMC settings were fields of each `agent_hosts` entry (`redfish`,
`redfishUser`, `redfishPassword`, `bmcSystemId`). `./upgrade.sh` moves them to
`config/plugins/boot/ironic.yaml` with a versioned migration that calls
`enclave migrations redfish-to-ironic` (`src/enclave/migrations/redfish_to_ironic.py`), keeping the
comments of both files. Boot plugin validation rejects `agent_hosts` entries that still carry those
fields. See [UPGRADE.md](UPGRADE.md) for the details.

## CI

- `scripts/infrastructure/generate_enclave_vars.sh` writes `bootPlugin` (from `BOOT_PLUGIN`, default
  `ironic`) to `config/global.yaml`, and for `ironic` writes `config/plugins/boot/ironic.yaml`: one
  `redfish` host per master, pointing at sushy-tools, with the VM UUID as `redfish.systemId`.
  `scripts/deployment/install_enclave.sh` copies it to the Landing Zone.
- `scripts/diagnostics/gather_lz.sh` collects `config/plugins/boot/*.yaml` with every password
  redacted, and skips a file it cannot parse rather than risk leaking it.

## Adding a New Boot Plugin

1. Create `plugins/boot/your-plugin/plugin.yaml` with `name` (the directory name), `type: boot`,
   `description` and, if a person or slow process boots the hosts, a larger `discoveryTimeout`.
1. Add `tasks/boot.yaml`, booting every host in `bootHostsToProvision` from `bootIsoPath`. Do not
   wait for input; core waits for the hosts to register.
1. If the plugin needs user settings, add `schemas/config.yaml` and a
   `config/plugins/boot/your-plugin.example.yaml`, with fixtures under
   `test-fixtures/plugins/boot/your-plugin/config/`. Add `defaults.yaml` with `schemas/defaults.yaml`
   for defaults.
1. Add `tasks/validate.yaml` to check every setting and dependency the plugin needs.
1. Add `tasks/setup.yaml` and `tasks/cleanup.yaml` if booting needs something set up and torn down,
   and `tasks/post-install.yaml` for work after `install-complete` (e.g. removing boot media).
1. Put helper task files in `tasks/lib/`.
1. Run `uv run enclave plugins validate --type boot plugins/boot/your-plugin` (or
   `make -f Makefile.ci validate-plugins`).
1. Set `bootPlugin: your-plugin` in `config/global.yaml`, or on the `agent_hosts` entries it boots.

No core files need to be modified. A third-party boot plugin dropped into `plugins/boot/` is held to
the same contract, checked at install time.

## Current Boot Plugins

| Plugin | Default | `discoveryTimeout` | What it does |
|--------|---------|--------------------|--------------|
| `ironic` | Yes | 600 (default) | Boots hosts through their BMC with an ephemeral Ironic; drivers: `redfish` |

A `manual` boot plugin, for hosts booted by an administrator, is planned (see
[docs/design/boot-plugins.md](design/boot-plugins.md)).

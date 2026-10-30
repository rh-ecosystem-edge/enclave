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

The original design and its rationale are in [docs/design/boot-plugins.md](design/boot-plugins.md),
consider it the initial inspiration for this project. This document describes what it is currently
implemented which may differ from the original design as time passes.

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

## Hooks

### Hook Reference

The hooks are listed in the order they run (see [Boot Lifecycle](#boot-lifecycle) for when each one
runs, between core steps). Only `boot.yaml` is required.

| Hook | Expected of it |
|------|----------------|
| `tasks/validate.yaml` | Check every setting the plugin needs, including settings from outside its configuration file (e.g. global variables), and runtime dependencies such as BMC reachability. It runs before anything is downloaded, so failures surface early |
| `tasks/setup.yaml` | Prepare what booting needs (e.g. deploy Ironic). Idempotent |
| `tasks/boot.yaml` | Boot every host in `bootHostsToProvision` from the ISO. It may wait for its hosts, but must not wait for input: core's discovery wait is the synchronization point |
| `tasks/post-install.yaml` | Work that needs the installed cluster, such as detaching boot media, for every host in `bootHosts`: a run can stop after `install-complete` and be rerun with every host already in the cluster. Must not fail when `setup.yaml` did not run |
| `tasks/cleanup.yaml` | Undo exactly what `setup.yaml` did. Idempotent, and must not fail when `setup.yaml` did not run |

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

## Boot Lifecycle

Core steps and hooks (in bold), in the order they run, by bootstrap step
(`bootstrap.sh --step <step>`). Hooks run through `playbooks/tasks/boot/run_hook.yaml`, which skips
hooks a plugin does not have.

| Step | What | Run by | Implemented in |
|------|------|--------|----------------|
| `validate` | Configuration schemas, unique MACs across `agent_hosts` | Core | `playbooks/validation/tasks/config_schema_validation.yaml` |
| `validate` | Boot plugin contract and configuration (see [Validation](#validation)) | Core, for each boot plugin in use | `playbooks/tasks/boot/validate.yaml` |
| `validate` | **`tasks/validate.yaml`** | Each boot plugin in use | `playbooks/tasks/boot/validate.yaml` |
| `build-cache` | ISO generation, with the installation gate | Core | `playbooks/tasks/configure_ocp_abi.yaml` |
| `acquire-hardware` | Filter out hosts already Ready in the cluster | Core | `playbooks/tasks/configure_hardware_filter_hosts.yaml` |
| `acquire-hardware` | Group hosts by effective `bootPlugin`, order plugins by `discoveryTimeout`, largest first | Core, for each boot plugin in use | `playbooks/tasks/boot/load_plugins.yaml` |
| `acquire-hardware` | **`tasks/setup.yaml`**, then **`tasks/boot.yaml`**, one plugin after the other | Each boot plugin with hosts to provision | `playbooks/tasks/boot/boot_hosts.yaml` |
| `acquire-hardware` | Wait for the rendezvous host, then for every agent host to be matched by MAC and `known` (see [Host Identity](#host-identity)) | Core | `playbooks/tasks/pre_install_validate_plugins.yaml`, `enclave boot wait-for-hosts` |
| `acquire-hardware` | `pre-install-validate.yaml` of the enabled foundation/addon plugins, then release the installation gate | Core | `playbooks/tasks/pre_install_validate_plugins.yaml` |
| `deploy` | `openshift-install agent wait-for bootstrap-complete` and `install-complete` | Core | `playbooks/tasks/wait_for_deployment.yaml` |
| `deploy` | **`tasks/post-install.yaml`** | Each boot plugin in use | `playbooks/tasks/boot/finish_hosts.yaml` |
| `deploy` | **`tasks/cleanup.yaml`**, even if a `post-install.yaml` failed | Each boot plugin in use | `playbooks/tasks/boot/finish_hosts.yaml` |

Ordering by `discoveryTimeout` makes a plugin that waits on a person print its instructions before
a plugin that blocks waiting for its hosts (as `ironic` does until its nodes are active).

### Host Identity

The discovery wait matches the host records registered with Assisted Service to `agent_hosts` by
MAC address (`macAddress` and the MACs in `mapInterfaces`). The match must be one-to-one, and the
wait fails at once, naming the hosts, if a registered host matches no entry or several entries, or
an entry is matched by several hosts (e.g. the wrong machine was booted).

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

The `ironic` plugin boots hosts through their BMC. It runs an ephemeral Ironic on the Landing Zone,
enrolls each host as an Ironic node with the agent ISO as its boot ISO, and deploys it: Ironic
inserts the ISO as virtual media and powers the host on, booting it from the ISO once.

| Hook | Role |
|------|------|
| `validate.yaml` | Checks the settings it needs from global configuration (e.g. `lzBmcIP`), that `hosts` matches the hosts it boots, then each driver's checks |
| `setup.yaml` | Deploys the ephemeral Ironic from the shared Ironic tasks library (`playbooks/tasks/ironic/`), also used by Day 2 discovery |
| `boot.yaml` | Enrolls and deploys the nodes, then waits until they are active |
| `post-install.yaml` | Detaches the virtual media; warns instead of failing when it cannot (e.g. Ironic no longer running on a rerun) |
| `cleanup.yaml` | Tears down the ephemeral Ironic |

Hook logic is driver-agnostic: everything that depends on how a BMC is driven lives in the driver
files described below. Media is detached only after `install-complete`, because on some hardware
coreos-installer streams the disk image from the mounted ISO.

### Configuration

```yaml
# config/plugins/boot/ironic.yaml
hosts:
  master-0:                               # agent_hosts[].name
    driver: redfish                       # Ironic hardware type of the BMC
    driverInfo:                           # Ironic driver_info, camelCase keys
      redfishAddress: bmc0.example.com
      redfishUsername: admin
      redfishPassword: secret
      redfishSystemId: /redfish/v1/Systems/System.Embedded.1
```

The `hosts` keys must match the `agent_hosts` booted by `ironic`. See
[CONFIGURATION_REFERENCE.md](CONFIGURATION_REFERENCE.md#configpluginsbootironicyaml) for every field.

### Drivers

The configuration follows Ironic's own conventions: each host gives the Ironic hardware type of its
BMC (`driver`) and its Ironic `driver_info` (`driverInfo`), with the keys of
[Ironic's driver documentation](https://docs.openstack.org/ironic/latest/admin/drivers/redfish.html)
in camelCase (`redfishAddress` for `redfish_address`).
Hosts can use different drivers. Only `redfish` is supported today.

- **Configuration.** `plugins/boot/ironic/schemas/config.yaml` has one host definition per driver,
  selected by `driver`, listing the `driverInfo` keys that driver accepts. Unknown or misspelled
  keys are rejected, and so are keys the plugin sets itself (Ironic's
  `force_persistent_boot_device`, which makes the ISO boot only once).
- **Code.** Each driver provides two task files in `tasks/lib/`:
  - `<driver>-settings.yaml` gives what the plugin decides for the driver: the hardware type, the
    virtual media boot interface, the mapping from `driverInfo` keys to Ironic `driver_info` keys,
    the `driver_info` keys it adds (defaults and plugin-owned keys), the key holding the BMC
    password (sent to Ironic apart from the rest, so it never appears in task output), and whether
    Ironic can detach its virtual media.
  - `<driver>-validate.yaml` runs the driver's preflight checks, e.g. querying the BMC.

  The hooks call them for the hosts of each driver in use, through `tasks/lib/settings.yaml`.

The plugin boots from virtual media, so only drivers with a virtual media boot interface fit (e.g.
not `ipmi`, which would need PXE).

`redfish` is the reference driver: it uses `redfish-virtual-media`, defaults `redfishVerifyCA` to
`false`, and its preflight checks that each BMC answers on `/redfish/v1/Systems` and exposes
`redfishSystemId` when set, or a single system when not. `redfishVerifyCA` only accepts a
boolean for now: a CA bundle would have to be mounted into the Ironic container (OSAC-6973).

### Adding a Driver

1. Add the driver to the `driver` enum in `plugins/boot/ironic/schemas/config.yaml`, with its host
   definition (its `driverInfo` keys, camelCase forms of Ironic's) selected by an `if`/`then` entry.
1. Add `tasks/lib/<driver>-settings.yaml` and `tasks/lib/<driver>-validate.yaml`, using the
   `redfish` files as a template.
1. Make sure the Ironic image enables the hardware type and its virtual media boot interface.
1. Add configuration fixtures under `test-fixtures/plugins/boot/ironic/config/`, and document the
   driver in `config/plugins/boot/ironic.example.yaml` and
   [CONFIGURATION_REFERENCE.md](CONFIGURATION_REFERENCE.md#configpluginsbootironicyaml).

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
  `redfish` host per master, pointing at sushy-tools, with the VM UUID in `redfishSystemId`.
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

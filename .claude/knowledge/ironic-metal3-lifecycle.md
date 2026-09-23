---
title: Two distinct Ironic deployments, both fully ephemeral per install
tags: [ironic, metal3, bmo, architecture]
updated: 2026-07-16
---

The pipeline runs Ironic twice, for two unrelated purposes — easy to conflate when reading either
task file in isolation.

1. **`playbooks/03-deploy.yaml`** (Phase 3, initial cluster install): Ironic is stood up as a
   transient instance solely to attach virtual media and boot the Agent-Based Installer ISO onto
   bare-metal hosts. No Baremetal Operator (BMO) is involved here.
2. **`playbooks/07-configure-discovery.yaml`** (later, ongoing-operations phase): Ironic is
   redeployed as a persistent instance (standalone podman container, optionally via a systemd podman
   quadlet unit, controlled by `metal3_persistent`) alongside BMO, to support Metal3
   `BareMetalHost`/`InfraEnv` discovery of additional hosts post-install.

`docs/DEPLOYMENT_GUIDE.md` only says "Configures bare metal servers using BareMetalOperator and
Ironic" and doesn't distinguish these two lifecycles — it isn't written down anywhere else.

Separately: `playbooks/tasks/wait_for_deployment.yaml` runs
`configure_hardware_ironic_cleanup.yaml` immediately after `openshift-install agent
wait-for install-complete` succeeds, tearing the phase-3 Ironic instance down entirely. A phase-3
rerun after a completed install would otherwise recreate every host in Ironic from scratch and
reboot already-live production nodes — this is why hardware-configuration idempotency work
(e.g. PR #570) matters.

**How to apply:** When touching Ironic/BMO logic, first identify which of the two deployments (phase
3 transient vs phase 7 persistent) the change actually targets — e.g.
`src/enclave/reconcile/ironic_version.py`'s `ironic-version` reconcile subcommand only targets the
phase-7 persistent/quadlet deployment, not the phase-3 transient one. When touching bootstrap
rerun/reprovision logic, remember phase-3 Ironic is fully torn down on success — a rerun sees a
clean slate, not existing state.

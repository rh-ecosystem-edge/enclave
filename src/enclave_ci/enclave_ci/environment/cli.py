"""Click commands for the libvirt CI test environment.

The vm_infra module is imported inside each command so that loading this group does
not require the libvirt bindings.
"""

import logging
import math
import os
import sys
from pathlib import Path

import click

DEFAULT_REAP_AGE_HOURS = 12.0
# A cluster's define-age exceeding the longest possible job runtime is what proves
# it is leaked rather than in-flight: the e2e jobs cap out at 660 minutes (11h, the
# scheduled run with the SNO VM) via timeout-minutes, so any cluster older than that
# cannot belong to a running job.
# Destructive reaps refuse thresholds below this floor (use --force to override on
# a host you know is idle); --dry-run is always allowed for inspection.
MIN_REAP_AGE_HOURS = 12.0


def _reap_age_from_env() -> float:
    """Reap threshold in hours from REAP_AGE_HOURS, falling back to the default."""
    raw = os.environ.get("REAP_AGE_HOURS", "")
    if not raw:
        return DEFAULT_REAP_AGE_HOURS
    try:
        return float(raw)
    except ValueError:
        sys.exit(f"ERROR: REAP_AGE_HOURS must be a number, got {raw!r}")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """Manage libvirt VM infrastructure for Enclave CI runs."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


@cli.command()
@click.option(
    "--print-subnet",
    is_flag=True,
    help="Dry-run: print the subnet create would use (creating nothing), then exit.",
)
def create(print_subnet: bool) -> None:
    """Create the cluster's networks, pool, volumes and VMs."""
    from enclave_ci.environment import vm_infra  # ruff: ignore[import-outside-top-level]

    # create may generate the cluster name; print_subnet requires it.
    if print_subnet:
        vm_infra.print_subnet(vm_infra.Config.from_env(generate_name=False))
    else:
        vm_infra.create(vm_infra.Config.from_env(generate_name=True))


@cli.command()
def destroy() -> None:
    """Destroy the cluster's VMs, networks and pool."""
    from enclave_ci.environment import vm_infra  # ruff: ignore[import-outside-top-level]

    vm_infra.destroy(vm_infra.Config.from_env(generate_name=False))


@cli.command()
@click.option(
    "--age-hours",
    type=float,
    default=None,
    help="Only tear down CI clusters older than this many hours "
    f"(default: $REAP_AGE_HOURS or {DEFAULT_REAP_AGE_HOURS:g}).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report which clusters would be reaped without destroying anything.",
)
@click.option(
    "--force",
    is_flag=True,
    help=f"Allow a threshold below the {MIN_REAP_AGE_HOURS:g}h safety floor for a "
    "destructive run (only on a host known to be idle).",
)
def reap(age_hours: float | None, dry_run: bool, force: bool) -> None:
    """Tear down all CI clusters older than a threshold (cross-run GC)."""
    from enclave_ci.environment import vm_infra  # ruff: ignore[import-outside-top-level]

    # reap discovers clusters from libvirt; it needs neither a cluster name nor a
    # working dir, so it must not go through Config.from_env().
    if age_hours is None:
        age_hours = _reap_age_from_env()
    # Allow-list the threshold: it must be a finite positive number. NaN would
    # slip past a bare `<= 0` check and make `age < threshold` always false, so
    # every discovered cluster — including active ones — would be reaped.
    if not math.isfinite(age_hours) or age_hours <= 0:
        raise click.UsageError("reap age must be a finite positive number of hours")
    # Below the safety floor a destructive sweep could tear down an in-flight
    # run sharing the host; only --dry-run or an explicit --force may go lower.
    if age_hours < MIN_REAP_AGE_HOURS and not dry_run and not force:
        raise click.UsageError(
            f"reap age {age_hours:g}h is below the {MIN_REAP_AGE_HOURS:g}h safety "
            "floor (longest job timeout); pass --force to override on an idle host"
        )
    base_raw = os.environ.get("BASE_WORKING_DIR", "")
    vm_infra.reap(age_hours, Path(base_raw) if base_raw else None, dry_run=dry_run)

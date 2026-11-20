"""Click commands for post-deployment verification."""

import ipaddress
import os
import re
import sys
from collections.abc import Callable

import click

from enclave_ci.verify.cluster import verify_cluster
from enclave_ci.verify.common import Cluster, LandingZone, Report, hub_kubeconfig
from enclave_ci.verify.sno import verify_sno, verify_sno_update_service

# Allow-lists for values that reach SSH or a path on the Landing Zone.
_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_DNS1123_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")


def _lz_ip(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as err:
        raise click.BadParameter(f"not an IP address: {value!r}") from err


def _lz_user(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    if not _USER_RE.fullmatch(value):
        raise click.BadParameter(f"not a valid user name: {value!r}")
    return value


def _sno_name(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    if not _DNS1123_RE.fullmatch(value):
        raise click.BadParameter(f"not a DNS-1123 label: {value!r}")
    return value


@click.group()
def cli() -> None:
    """Verify an Enclave deployment."""


@cli.command()
@click.option(
    "--lz-ip", required=True, callback=_lz_ip, help="Landing Zone IP address."
)
@click.option(
    "--mode",
    type=click.Choice(["connected", "disconnected"]),
    default="disconnected",
    show_default=True,
    help="Deployment mode; disconnected also checks the Landing Zone mirror.",
)
@click.option("--lz-user", default="cloud-user", show_default=True, callback=_lz_user)
@click.option(
    "--session-dir",
    default="/home/cloud-user/sessions/1",
    show_default=True,
    help="Enclave session directory on the Landing Zone (bin/, ocp-cluster/).",
)
@click.option(
    "--config",
    "config_path",
    default="/home/cloud-user/enclave/config/global.yaml",
    show_default=True,
    help="Enclave global.yaml on the Landing Zone (for the mirror hostname).",
)
def cluster(
    lz_ip: str, mode: str, lz_user: str, session_dir: str, config_path: str
) -> None:
    """Check the hub once and list every problem; exit 1 if unhealthy."""
    report = Report(os.environ.get("GITHUB_STEP_SUMMARY") or None, click.echo)
    report.line(f"✅ Landing Zone IP: {lz_ip}")
    hub = Cluster(LandingZone(lz_ip, lz_user), session_dir, hub_kubeconfig(session_dir))
    if not verify_cluster(hub, mode, config_path, report):
        sys.exit(1)


def _sno_options(func: Callable[..., None]) -> Callable[..., None]:
    """Options shared by the SNO commands."""
    for option in reversed((
        click.option(
            "--lz-ip", required=True, callback=_lz_ip, help="Landing Zone IP address."
        ),
        click.option(
            "--sno-name",
            required=True,
            callback=_sno_name,
            help="SNO cluster name (DNS-1123 label).",
        ),
        click.option(
            "--lz-user", default="cloud-user", show_default=True, callback=_lz_user
        ),
        click.option(
            "--session-dir",
            default="/home/cloud-user/sessions/1",
            show_default=True,
            help="Enclave session directory on the Landing Zone.",
        ),
    )):
        func = option(func)
    return func


def _sno_clusters(
    lz_ip: str, lz_user: str, session_dir: str, sno_name: str
) -> tuple[Cluster, Cluster, Report]:
    lz = LandingZone(lz_ip, lz_user)
    hub = Cluster(lz, session_dir, hub_kubeconfig(session_dir))
    sno = Cluster(lz, session_dir, f"{session_dir}/{sno_name}-kubeconfig")
    report = Report(os.environ.get("GITHUB_STEP_SUMMARY") or None, click.echo)
    return hub, sno, report


@cli.command()
@_sno_options
def sno(lz_ip: str, sno_name: str, lz_user: str, session_dir: str) -> None:
    """Check the SNO managed cluster and its ACM import; exit 1 if unhealthy."""
    hub, sno_cluster, report = _sno_clusters(lz_ip, lz_user, session_dir, sno_name)
    if not verify_sno(hub, sno_cluster, sno_name, report):
        sys.exit(1)


@cli.command("sno-update-service")
@_sno_options
def sno_update_service(
    lz_ip: str, sno_name: str, lz_user: str, session_dir: str
) -> None:
    """Check the update service policy patched the SNO; exit 1 if not."""
    hub, sno_cluster, report = _sno_clusters(lz_ip, lz_user, session_dir, sno_name)
    if not verify_sno_update_service(hub, sno_cluster, sno_name, report):
        sys.exit(1)

"""Click commands for post-deployment verification."""

import os
import sys

import click

from enclave_ci.verify.cluster import verify_cluster
from enclave_ci.verify.common import Cluster, LandingZone, Report, hub_kubeconfig


@click.group()
def cli() -> None:
    """Verify an Enclave deployment."""


@cli.command()
@click.option("--lz-ip", required=True, help="Landing Zone IP address.")
@click.option(
    "--mode",
    type=click.Choice(["connected", "disconnected"]),
    default="disconnected",
    show_default=True,
    help="Deployment mode; disconnected also checks the Landing Zone mirror.",
)
@click.option("--lz-user", default="cloud-user", show_default=True)
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

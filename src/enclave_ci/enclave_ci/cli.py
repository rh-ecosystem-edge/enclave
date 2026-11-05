"""Unified CLI entry point for the enclave-ci tooling."""

import click

from enclave_ci.cert_gen.main import cli as cert_gen_cli
from enclave_ci.scrub_artifacts.main import cli as scrub_artifacts_cli


@click.group()
def cli() -> None:
    """Red Hat Sovereign Enclave CI tooling."""


cli.add_command(cert_gen_cli, name="cert-gen")
cli.add_command(scrub_artifacts_cli, name="scrub-artifacts")

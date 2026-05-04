import click
import sys

from operator_versions import reconcile as operator_versions_reconcile


@click.group()
def cli():
    pass


@cli.command()
@click.option("--operators", default="[]")
@click.option("--dry-run/--no-dry-run", default=False)
def operator_versions(operators, dry_run):
    # rebuild sys.argv for the standalone script
    sys.argv = ["operator_versions.py", operators, str(dry_run)]

    operator_versions_reconcile()


@cli.command()
def mgmt_cluster_version():
    raise click.ClickException("mgmt-cluster-version is not implemented yet")


if __name__ == "__main__":
    cli()

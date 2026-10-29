import json

import click

from enclave.boot.hosts import HostMatchError, assisted_hosts_fetcher, wait_for_hosts


def _parse_host(value: str) -> tuple[str, frozenset[str]]:
    """Parse a --host value NAME=MAC[,MAC...] into the name and its lowercase MACs."""
    name, sep, macs = value.partition("=")
    mac_set = frozenset(m.strip().lower() for m in macs.split(",") if m.strip())
    if not sep or not name or not mac_set:
        raise click.BadParameter(
            f"{value!r} is not NAME=MAC[,MAC...]", param_hint="--host"
        )
    return name, mac_set


@click.group()
def cli() -> None:
    """Enclave boot CLI."""


@cli.command("wait-for-hosts", no_args_is_help=True)
@click.option(
    "--url",
    required=True,
    help="Assisted Service API base URL, e.g. http://<rendezvousIP>:8090/api/assisted-install/v2.",
)
@click.option("--cluster-id", required=True, help="Assisted Service cluster ID.")
@click.option(
    "--host",
    "hosts",
    required=True,
    multiple=True,
    help="Expected agent host as NAME=MAC[,MAC...]; repeat for each host.",
)
@click.option(
    "--timeout",
    required=True,
    type=click.IntRange(min=1),
    help="Seconds to wait for the hosts.",
)
@click.option(
    "--interval",
    default=5.0,
    show_default=True,
    type=click.FloatRange(min=0),
    help="Seconds between polls.",
)
@click.option(
    "--token",
    envvar="ASSISTED_AUTH_TOKEN",
    required=True,
    help="Assisted Service auth token (read from ASSISTED_AUTH_TOKEN).",
)
def wait_for_hosts_cmd(
    url: str,
    cluster_id: str,
    hosts: tuple[str, ...],
    timeout: int,
    interval: float,
    token: str,
) -> None:
    """Wait until every agent host is registered with Assisted Service and known.

    Hosts are matched by MAC address, one-to-one. Prints the host records as
    JSON on success.
    """
    expected = dict(_parse_host(h) for h in hosts)
    if len(expected) != len(hosts):
        raise click.BadParameter("host names must be unique", param_hint="--host")
    try:
        records = wait_for_hosts(
            assisted_hosts_fetcher(url, cluster_id, token),
            expected,
            timeout,
            interval,
        )
    except HostMatchError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(records))

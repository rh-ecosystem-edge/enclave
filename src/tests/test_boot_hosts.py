"""Tests for waiting for the agent hosts to register with Assisted Service."""

import json
from collections.abc import Callable
from typing import Any

import pytest
import requests
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.boot.cli import cli
from enclave.boot.hosts import (
    HostMatchError,
    assisted_hosts_fetcher,
    match_hosts,
    wait_for_hosts,
)
from enclave.cli import cli as enclave_cli

EXPECTED = {
    "m0": frozenset({"aa:00:00:00:00:01"}),
    "m1": frozenset({"aa:00:00:00:00:02", "aa:00:00:00:00:12"}),
    "m2": frozenset({"aa:00:00:00:00:03"}),
}


def _record(
    host_id: str, *macs: str, status: str = "known", name: str | None = None
) -> dict[str, Any]:
    record: dict[str, Any] = {"id": host_id, "status": status}
    if name:
        record["requested_hostname"] = name
    if macs:
        record["inventory"] = json.dumps({
            "interfaces": [
                {"name": f"nic{i}", "mac_address": m} for i, m in enumerate(macs)
            ]
        })
    return record


ALL_KNOWN = [
    _record("a", "AA:00:00:00:00:01"),
    _record("b", "aa:00:00:00:00:12", "ff:ff:ff:ff:ff:ff"),
    _record("c", "aa:00:00:00:00:03"),
]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _wait(
    responses: list[list[dict[str, Any]] | None], timeout: int = 20
) -> list[dict[str, Any]]:
    clock = FakeClock()
    pending = iter(responses)
    last: list[list[dict[str, Any]] | None] = [None]

    def fetch() -> list[dict[str, Any]] | None:
        last[0] = next(pending, last[0])
        return last[0]

    return wait_for_hosts(
        fetch, EXPECTED, timeout, interval=5, clock=clock.time, sleep=clock.sleep
    )


def test_match_by_any_mac_case_insensitively() -> None:
    match = match_hosts(ALL_KNOWN, EXPECTED)
    assert {name: [r["id"] for r in rs] for name, rs in match.records.items()} == {
        "m0": ["a"],
        "m1": ["b"],
        "m2": ["c"],
    }
    assert match.pending(EXPECTED) == []


def test_wait_returns_records_once_all_known() -> None:
    first = [_record("a", "aa:00:00:00:00:01", status="discovering")]
    assert _wait([first, None, ALL_KNOWN]) == ALL_KNOWN


@pytest.mark.parametrize(
    ("records", "message"),
    [
        (
            [_record("x", "bb:00:00:00:00:01", name="stray")],
            r"match no agent_hosts entry: stray \(bb:00:00:00:00:01\)",
        ),
        (
            [_record("x", "aa:00:00:00:00:01", "aa:00:00:00:00:02")],
            (
                r"more than one agent_hosts entry: x \(aa:00:00:00:00:01, "
                r"aa:00:00:00:00:02\) matches m0, m1"
            ),
        ),
        (
            [_record("d", "aa:00:00:00:00:03"), _record("e", "aa:00:00:00:00:03")],
            r"matched by more than one host .*: m2 \(d \(aa:00:00:00:00:03\), e ",
        ),
    ],
)
def test_wait_fails_fast_on_mismatch(
    records: list[dict[str, Any]], message: str
) -> None:
    calls: list[int] = []

    def fetch() -> list[dict[str, Any]]:
        calls.append(1)
        return records

    with pytest.raises(HostMatchError, match=message):
        wait_for_hosts(fetch, EXPECTED, 600, sleep=lambda _: None)
    assert len(calls) == 1


def test_wait_times_out_naming_pending_hosts() -> None:
    records = [
        _record("a", "aa:00:00:00:00:01"),
        _record("c", "aa:00:00:00:00:03", status="insufficient"),
        _record("z", name="no-inventory"),
    ]
    with pytest.raises(HostMatchError) as excinfo:
        _wait([records])
    assert str(excinfo.value) == (
        "Timed out after 20 seconds waiting for agent_hosts to be matched to a host "
        "registered with Assisted Service and reach status known: m1 (not matched), "
        "m2 (insufficient). Hosts registered without an inventory yet, so they cannot "
        "be matched by MAC address: no-inventory. Set bootDiscoveryTimeout in "
        "config/global.yaml to wait longer."
    )


def test_wait_times_out_when_the_api_never_answers() -> None:
    with pytest.raises(HostMatchError, match=r"m0 \(not matched\), m1 \(not matched\)"):
        _wait([None])


@pytest.mark.parametrize(
    "inventory", ["not json", json.dumps(["a list"]), json.dumps({"interfaces": None})]
)
def test_unusable_inventory_counts_as_no_inventory(inventory: str) -> None:
    match = match_hosts([{"id": "a", "inventory": inventory}], EXPECTED)
    assert match.no_inventory == ["a"]


def _response(payload: object, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()  # ruff: ignore[private-member-access]
    return response


def test_fetcher_requests_inventory(mocker: MockerFixture) -> None:
    get = mocker.patch(
        "enclave.boot.hosts.requests.get",
        return_value=_response([*ALL_KNOWN, "not a host"]),
    )
    fetch = assisted_hosts_fetcher("http://as:8090/api/v2/", "c1", "tok")

    assert fetch() == ALL_KNOWN
    get.assert_called_once_with(
        "http://as:8090/api/v2/clusters/c1/hosts",
        params={"with-inventory": "true"},
        headers={"Authorization": "tok"},
        timeout=30,
    )


@pytest.mark.parametrize(
    "side_effect",
    [
        lambda *_, **__: _response({}, status=401),
        lambda *_, **__: _response({"not": "a list"}),
        requests.ConnectionError("refused"),
    ],
)
def test_fetcher_returns_none_on_failure(
    mocker: MockerFixture, side_effect: Callable[..., object] | Exception
) -> None:
    mocker.patch("enclave.boot.hosts.requests.get", side_effect=side_effect)
    assert assisted_hosts_fetcher("http://as", "c1", "tok")() is None


HOST_ARGS = [
    "--host",
    "m0=AA:00:00:00:00:01",
    "--host",
    "m1=aa:00:00:00:00:02,aa:00:00:00:00:12",
    "--host",
    "m2=aa:00:00:00:00:03",
]
BASE_ARGS = ["wait-for-hosts", "--url", "http://as", "--cluster-id", "c1"]


def test_cli_prints_records(mocker: MockerFixture) -> None:
    wait = mocker.patch("enclave.boot.cli.wait_for_hosts", return_value=ALL_KNOWN)
    result = CliRunner().invoke(
        cli,
        [*BASE_ARGS, "--timeout", "600", *HOST_ARGS],
        env={"ASSISTED_AUTH_TOKEN": "tok"},
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ALL_KNOWN
    _, expected, timeout, interval = wait.call_args.args
    assert expected == EXPECTED
    assert (timeout, interval) == (600, 5.0)


def test_cli_reports_match_errors(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.boot.cli.wait_for_hosts", side_effect=HostMatchError("no match")
    )
    result = CliRunner().invoke(
        enclave_cli,
        ["boot", *BASE_ARGS, "--timeout", "5", *HOST_ARGS],
        env={"ASSISTED_AUTH_TOKEN": "tok"},
    )
    assert result.exit_code == 1
    assert "Error: no match" in result.output


@pytest.mark.parametrize(
    ("host_args", "message"),
    [
        (["--host", "m0"], "is not NAME=MAC"),
        (["--host", "m0="], "is not NAME=MAC"),
        (
            ["--host", "m0=aa:00:00:00:00:01", "--host", "m0=aa:00:00:00:00:02"],
            "unique",
        ),
    ],
)
def test_cli_rejects_bad_hosts(host_args: list[str], message: str) -> None:
    result = CliRunner().invoke(
        cli,
        [*BASE_ARGS, "--timeout", "5", *host_args],
        env={"ASSISTED_AUTH_TOKEN": "tok"},
    )
    assert result.exit_code == 2
    assert message in result.output


def test_cli_requires_token() -> None:
    result = CliRunner().invoke(
        cli, [*BASE_ARGS, "--timeout", "5", *HOST_ARGS], env={"ASSISTED_AUTH_TOKEN": ""}
    )
    assert result.exit_code == 2
    assert "ASSISTED_AUTH_TOKEN" in result.output or "--token" in result.output

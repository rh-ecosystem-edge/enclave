"""Wait for the agent hosts to register with Assisted Service.

Host records are matched to the expected agent hosts by MAC address: the MACs
of an expected host against the interfaces in the record's inventory. The match
must be one-to-one. A record matching no expected host or several of them, or
an expected host matched by several records, ends the wait immediately; the
wait succeeds once every expected host is matched by one record in status
known.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

KNOWN = "known"
REQUEST_TIMEOUT = 30


class HostMatchError(Exception):
    pass


@dataclass
class HostMatch:
    unknown: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    no_inventory: list[str] = field(default_factory=list)
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def duplicated(self) -> list[str]:
        return [name for name, matched in self.records.items() if len(matched) > 1]

    def pending(self, expected: dict[str, frozenset[str]]) -> list[str]:
        return [
            name
            for name in expected
            if len(self.records.get(name, [])) != 1
            or self.records[name][0].get("status") != KNOWN
        ]


def _record_macs(record: dict[str, Any]) -> frozenset[str]:
    inventory = record.get("inventory") or "{}"
    try:
        interfaces = json.loads(inventory).get("interfaces") or []
    except (TypeError, ValueError, AttributeError):
        return frozenset()
    return frozenset(
        str(i["mac_address"]).lower()
        for i in interfaces
        if isinstance(i, dict) and i.get("mac_address")
    )


def _label(record: dict[str, Any], macs: frozenset[str]) -> str:
    name = record.get("requested_hostname") or record.get("id") or "unnamed host"
    return f"{name} ({', '.join(sorted(macs))})" if macs else str(name)


def match_hosts(
    records: list[dict[str, Any]], expected: dict[str, frozenset[str]]
) -> HostMatch:
    """Match Assisted Service host records to the expected hosts by MAC."""
    match = HostMatch()
    for record in records:
        macs = _record_macs(record)
        label = _label(record, macs)
        if not macs:
            match.no_inventory.append(label)
            continue
        names = [name for name, host_macs in expected.items() if host_macs & macs]
        if not names:
            match.unknown.append(label)
        elif len(names) > 1:
            match.ambiguous.append(f"{label} matches {', '.join(names)}")
        else:
            match.records.setdefault(names[0], []).append(record)
    return match


def check_match(match: HostMatch) -> None:
    """Raise HostMatchError if the match can no longer succeed."""
    if match.unknown:
        raise HostMatchError(
            "Hosts registered with Assisted Service whose MAC addresses match no "
            f"agent_hosts entry: {'; '.join(match.unknown)}. Check that the right "
            "machines were booted from the agent ISO and that the "
            "macAddress/mapInterfaces of agent_hosts are correct."
        )
    if match.ambiguous:
        raise HostMatchError(
            "Hosts registered with Assisted Service whose MAC addresses match more "
            f"than one agent_hosts entry: {'; '.join(match.ambiguous)}."
        )
    if match.duplicated:
        details = "; ".join(
            f"{name} ({', '.join(_label(r, _record_macs(r)) for r in match.records[name])})"
            for name in match.duplicated
        )
        raise HostMatchError(
            "agent_hosts entries matched by more than one host registered with "
            f"Assisted Service: {details}."
        )


def timeout_message(
    match: HostMatch, expected: dict[str, frozenset[str]], timeout: int
) -> str:
    pending = ", ".join(
        f"{name} ({match.records[name][0].get('status')})"
        if match.records.get(name)
        else f"{name} (not matched)"
        for name in match.pending(expected)
    )
    message = (
        f"Timed out after {timeout} seconds waiting for agent_hosts to be matched to "
        f"a host registered with Assisted Service and reach status known: {pending}."
    )
    if match.no_inventory:
        message += (
            " Hosts registered without an inventory yet, so they cannot be matched by "
            f"MAC address: {'; '.join(match.no_inventory)}."
        )
    return message + " Set bootDiscoveryTimeout in config/global.yaml to wait longer."


def assisted_hosts_fetcher(
    url: str, cluster_id: str, token: str
) -> Callable[[], list[dict[str, Any]] | None]:
    """Return a function listing the cluster hosts, or None if the call fails."""

    def fetch() -> list[dict[str, Any]] | None:
        try:
            response = requests.get(
                f"{url.rstrip('/')}/clusters/{cluster_id}/hosts",
                params={"with-inventory": "true"},
                headers={"Authorization": token},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            hosts = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Cannot list Assisted Service hosts: %s", exc)
            return None
        if not isinstance(hosts, list):
            logger.warning("Unexpected Assisted Service hosts response: %r", hosts)
            return None
        return [h for h in hosts if isinstance(h, dict)]

    return fetch


def wait_for_hosts(
    fetch: Callable[[], list[dict[str, Any]] | None],
    expected: dict[str, frozenset[str]],
    timeout: int,
    interval: float = 5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Poll until every expected host is matched and known; return the records."""
    deadline = clock() + timeout
    match = HostMatch()
    while True:
        records = fetch()
        if records is not None:
            match = match_hosts(records, expected)
            check_match(match)
            pending = match.pending(expected)
            if not pending:
                logger.info(
                    "All %d agent hosts are ready (status: known)", len(expected)
                )
                return records
            logger.debug("Waiting for agent hosts: %s", ", ".join(pending))
        if clock() >= deadline:
            raise HostMatchError(timeout_message(match, expected, timeout))
        sleep(interval)

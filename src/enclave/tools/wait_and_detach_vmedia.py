"""Poll Assisted Service and detach Ironic virtual media when a host's disk write completes."""

import logging
import time
from typing import Any, Literal

import requests

logger = logging.getLogger(__name__)

POLL_INTERVAL_DEFAULT = 1
MAX_SECONDS_DEFAULT = 7200
MAX_CONSECUTIVE_UNREACHABLE = 3

DetachDecision = Literal["yes", "no", "error", "unavailable"]


def identify_host(
    assisted_service_url: str,
    cluster_id: str,
    auth_token: str,
    host_name: str,
) -> tuple[str, str] | None:
    """Return (infra_env_id, host_id) for host_name, or None if not yet registered.

    Queries the cluster hosts endpoint to obtain the infra_env_id, then searches
    the infra-env hosts list for the record whose requested_hostname matches
    host_name. Returns None on connection error or if the host is not found yet
    (caller retries on the next poll interval).
    """
    headers = {"Authorization": auth_token}
    cluster_url = f"{assisted_service_url}/clusters/{cluster_id}/hosts"
    try:
        resp = requests.get(cluster_url, headers=headers, timeout=30)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return None
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"Assisted Service returned HTTP {resp.status_code} for cluster hosts"
        ) from exc
    try:
        cluster_hosts: list[dict[str, Any]] = resp.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "Assisted Service cluster hosts response is not valid JSON"
        ) from exc
    if not cluster_hosts:
        return None
    infra_env_id: str = cluster_hosts[0]["infra_env_id"]

    infra_url = f"{assisted_service_url}/infra-envs/{infra_env_id}/hosts"
    try:
        resp = requests.get(infra_url, headers=headers, timeout=30)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return None
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"Assisted Service returned HTTP {resp.status_code} for infra-env hosts"
        ) from exc
    try:
        infra_hosts: list[dict[str, Any]] = resp.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "Assisted Service infra-env hosts response is not valid JSON"
        ) from exc
    for h in infra_hosts:
        if h.get("requested_hostname") == host_name:
            return (infra_env_id, h["id"])
    return None


def can_detach(
    assisted_service_url: str,
    infra_env_id: str,
    host_id: str,
    auth_token: str,
    host_name: str,
) -> DetachDecision:
    """Decide whether vmedia can be safely detached for this host right now.

    Returns:
    - "yes":         disk write is complete, detach immediately
    - "no":          installation still in progress, keep polling
    - "error":       installation failed (status error or cancelled), skip detach
    - "unavailable": Assisted Service unreachable, caller handles streak

    Detach conditions (from observed RHDP bare-metal behaviour):
    - Non-rendezvous host: status == "installed"
    - Rendezvous/bootstrap host: status == "installing-in-progress" and
      status_info == "Rebooting" (disk write complete, bootstrap rebooting)
    """
    url = f"{assisted_service_url}/infra-envs/{infra_env_id}/hosts/{host_id}"
    try:
        resp = requests.get(url, headers={"Authorization": auth_token}, timeout=30)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return "unavailable"
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"Assisted Service returned HTTP {resp.status_code} for host {host_id}"
        ) from exc
    try:
        host: dict[str, Any] = resp.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            f"Assisted Service host response for {host_id} is not valid JSON"
        ) from exc
    status: str = host.get("status", "")
    status_info: str = host.get("status_info", "")
    is_bootstrap: bool = host.get("bootstrap", False)
    logger.debug(
        "%s: bootstrap=%s status=%r status_info=%r",
        host_name,
        is_bootstrap,
        status,
        status_info,
    )
    if status in {"error", "cancelled"}:
        return "error"
    if is_bootstrap:
        if status == "installing-in-progress" and status_info == "Rebooting":
            return "yes"
    elif status == "installed":
        return "yes"
    return "no"


def detach_ironic_vmedia(
    ironic_base_url: str,
    api_version: str,
    ironic_user: str,
    ironic_password: str,
    host_name: str,
) -> None:
    """Fetch the Ironic node UUID for host_name and DELETE its virtual media.

    Accepts HTTP 204 (detached) and 404 (already detached or no vmedia).
    Raises RuntimeError on any other response or transport failure.
    """
    headers = {"X-OpenStack-Ironic-API-Version": api_version}
    auth = (ironic_user, ironic_password)
    try:
        node_resp = requests.get(
            f"{ironic_base_url}/v1/nodes/{host_name}",
            headers=headers,
            auth=auth,
            timeout=30,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        raise RuntimeError(
            f"cannot reach Ironic at {ironic_base_url} to look up {host_name!r}: {exc}"
        ) from exc
    if node_resp.status_code != requests.codes.ok:
        raise RuntimeError(
            f"failed to get Ironic node UUID for {host_name!r}: HTTP {node_resp.status_code}"
        )
    try:
        uuid: str = node_resp.json()["uuid"]
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ironic node response for {host_name!r} is not valid JSON"
        ) from exc
    except KeyError:
        raise RuntimeError(
            f"Ironic node response for {host_name!r} is missing 'uuid' field"
        ) from None
    logger.info("detaching vmedia for %s (uuid=%s)", host_name, uuid)
    try:
        detach_resp = requests.delete(
            f"{ironic_base_url}/v1/nodes/{uuid}/vmedia",
            headers=headers,
            auth=auth,
            timeout=30,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        raise RuntimeError(
            f"cannot reach Ironic at {ironic_base_url} to detach vmedia for {host_name!r}: {exc}"
        ) from exc
    if detach_resp.status_code not in {
        requests.codes.no_content,
        requests.codes.not_found,
    }:
        raise RuntimeError(
            f"failed to detach vmedia for {host_name!r} (uuid={uuid}): HTTP {detach_resp.status_code}"
        )
    if detach_resp.status_code == requests.codes.not_found:
        logger.info("vmedia for %s already absent (404)", host_name)
    else:
        logger.info("vmedia detached for %s", host_name)


def main(
    host_name: str,
    assisted_service_url: str,
    assisted_cluster_id: str,
    assisted_auth_token: str,
    ironic_base_url: str,
    ironic_api_version: str,
    ironic_user: str,
    ironic_password: str,
    *,
    poll_interval: int = POLL_INTERVAL_DEFAULT,
    max_seconds: int = MAX_SECONDS_DEFAULT,
) -> None:
    """Poll Assisted Service until the host's disk write completes, then detach Ironic vmedia.

    Phase 1 — identification: loop until this host's record is found in Assisted Service
    by requested_hostname. No detach in this phase; just wait and retry.

    Phase 2 — polling: call can_detach() each poll interval. Streak of 3 consecutive
    "unavailable" (AS unreachable) triggers detach as a fallback for the rendezvous,
    which may never reach its terminal state before AS shuts down.

    Exits normally (no exception) in all expected cases:
    - can_detach returns "yes": vmedia detached
    - can_detach returns "unavailable" 3+ consecutive times: vmedia detached
    - can_detach returns "error": installation failed, no detach

    Raises RuntimeError on timeout or unexpected Ironic failures.
    """
    start = time.monotonic()
    deadline = start + max_seconds
    logger.info(
        "monitoring %s for disk write completion (interval=%ds, deadline=%ds)",
        host_name,
        poll_interval,
        max_seconds,
    )

    # Phase 1: identify this host in Assisted Service by requested_hostname.
    host_identity: tuple[str, str] | None = None
    while time.monotonic() < deadline:
        host_identity = identify_host(
            assisted_service_url, assisted_cluster_id, assisted_auth_token, host_name
        )
        if host_identity is not None:
            logger.info(
                "%s: identified in Assisted Service (host_id=%s)",
                host_name,
                host_identity[1],
            )
            break
        logger.debug("%s: not yet registered in Assisted Service, retrying", host_name)
        time.sleep(poll_interval)

    if host_identity is None:
        raise RuntimeError(
            f"timeout identifying host {host_name!r} in Assisted Service ({max_seconds}s elapsed)"
        )

    infra_env_id, host_id = host_identity

    # Phase 2: poll per-host status until terminal state or unreachable streak.
    streak = 0
    while time.monotonic() < deadline:
        decision = can_detach(
            assisted_service_url, infra_env_id, host_id, assisted_auth_token, host_name
        )
        if decision == "yes":
            logger.info(
                "%s: disk write complete, detaching vmedia",
                host_name,
            )
            detach_ironic_vmedia(
                ironic_base_url,
                ironic_api_version,
                ironic_user,
                ironic_password,
                host_name,
            )
            return
        if decision == "error":
            logger.warning(
                "%s: installation failed, skipping vmedia detach",
                host_name,
            )
            return
        if decision == "unavailable":
            streak += 1
            logger.debug(
                "%s: Assisted Service unreachable (streak=%d/%d)",
                host_name,
                streak,
                MAX_CONSECUTIVE_UNREACHABLE,
            )
            if streak >= MAX_CONSECUTIVE_UNREACHABLE:
                logger.info(
                    "%s: Assisted Service unreachable for %d consecutive polls — "
                    "rendezvous rebooted, detaching vmedia",
                    host_name,
                    streak,
                )
                detach_ironic_vmedia(
                    ironic_base_url,
                    ironic_api_version,
                    ironic_user,
                    ironic_password,
                    host_name,
                )
                return
        else:
            streak = 0
        time.sleep(poll_interval)
    raise RuntimeError(
        f"timeout waiting for host {host_name!r} to complete disk write "
        f"({max_seconds}s elapsed)"
    )

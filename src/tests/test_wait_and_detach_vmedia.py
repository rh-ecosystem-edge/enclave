"""Tests for wait_and_detach_vmedia: Assisted Service polling and Ironic vmedia detach."""

from typing import Any

import pytest
import requests.exceptions
from pytest_mock import MockerFixture
from requests_mock import Mocker

from enclave.tools import wait_and_detach_vmedia as mod
from tests.fixtures import Fixtures

fxt = Fixtures("wait_and_detach_vmedia")

HOST_NAME = "eci-8dbffc00-master-00"
CLUSTER_ID = "05b406d0-e765-495f-8682-e0a78f226ce1"
AUTH_TOKEN = "Bearer test-token"  # noqa: S105
SERVICE_URL = "http://10.187.89.217:8090/api/assisted-install/v2"
IRONIC_URL = "http://localhost:6385"
IRONIC_VERSION = "1.89"
IRONIC_USER = "ironic"
IRONIC_PASSWORD = "ironic-pass"  # noqa: S105
NODE_UUID = "a2868f98-c696-697a-ca65-ad6b44e7aa3b"
INFRA_ENV_ID = "4c104ffc-8543-4108-9fb7-2e59f86ab126"
HOST_ID = "a2868f98-c696-697a-ca65-ad6b44e7aa3b"

CLUSTER_HOSTS_URL = f"{SERVICE_URL}/clusters/{CLUSTER_ID}/hosts"
INFRA_ENV_HOSTS_URL = f"{SERVICE_URL}/infra-envs/{INFRA_ENV_ID}/hosts"
HOST_URL = f"{SERVICE_URL}/infra-envs/{INFRA_ENV_ID}/hosts/{HOST_ID}"

INFRA_ENV_HOSTS: list[dict[str, Any]] = fxt.get_anymarkup("infra_env_hosts.json")

CLUSTER_HOSTS_WITH_INFRA_ENV: list[dict[str, Any]] = [
    {"id": HOST_ID, "infra_env_id": INFRA_ENV_ID, "bootstrap": False}
]

MAIN_KWARGS: dict[str, Any] = {
    "host_name": HOST_NAME,
    "assisted_service_url": SERVICE_URL,
    "assisted_cluster_id": CLUSTER_ID,
    "assisted_auth_token": AUTH_TOKEN,
    "ironic_base_url": IRONIC_URL,
    "ironic_api_version": IRONIC_VERSION,
    "ironic_user": IRONIC_USER,
    "ironic_password": IRONIC_PASSWORD,
}


def test_identify_host_returns_ids_when_found(requests_mock: Mocker) -> None:
    """Return (infra_env_id, host_id) when requested_hostname matches in the infra-env list."""
    requests_mock.get(CLUSTER_HOSTS_URL, json=CLUSTER_HOSTS_WITH_INFRA_ENV)
    requests_mock.get(INFRA_ENV_HOSTS_URL, json=INFRA_ENV_HOSTS)
    result = mod.identify_host(SERVICE_URL, CLUSTER_ID, AUTH_TOKEN, HOST_NAME)
    assert result == (INFRA_ENV_ID, HOST_ID)


def test_identify_host_returns_none_when_host_list_empty(requests_mock: Mocker) -> None:
    """Return None when the cluster hosts list is empty (registration not started)."""
    requests_mock.get(CLUSTER_HOSTS_URL, json=[])
    assert mod.identify_host(SERVICE_URL, CLUSTER_ID, AUTH_TOKEN, HOST_NAME) is None


def test_identify_host_returns_none_when_hostname_not_yet_set(
    requests_mock: Mocker,
) -> None:
    """Return None when infra-env hosts have null requested_hostname (not yet registered)."""
    requests_mock.get(CLUSTER_HOSTS_URL, json=CLUSTER_HOSTS_WITH_INFRA_ENV)
    requests_mock.get(
        INFRA_ENV_HOSTS_URL,
        json=[
            {"id": HOST_ID, "infra_env_id": INFRA_ENV_ID, "requested_hostname": None}
        ],
    )
    assert mod.identify_host(SERVICE_URL, CLUSTER_ID, AUTH_TOKEN, HOST_NAME) is None


def test_identify_host_returns_none_on_connection_error(requests_mock: Mocker) -> None:
    """Return None when the cluster endpoint is unreachable (caller retries)."""
    requests_mock.get(CLUSTER_HOSTS_URL, exc=requests.exceptions.ConnectionError)
    assert mod.identify_host(SERVICE_URL, CLUSTER_ID, AUTH_TOKEN, HOST_NAME) is None


def test_identify_host_raises_on_http_error(requests_mock: Mocker) -> None:
    """Raise RuntimeError when the cluster endpoint returns a non-2xx response."""
    requests_mock.get(CLUSTER_HOSTS_URL, status_code=401)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        mod.identify_host(SERVICE_URL, CLUSTER_ID, AUTH_TOKEN, HOST_NAME)


def test_can_detach_returns_yes_for_non_bootstrap_installed(
    requests_mock: Mocker,
) -> None:
    """Return 'yes' when a non-bootstrap host status is 'installed'."""
    requests_mock.get(HOST_URL, json=fxt.get_anymarkup("host_infra_env_installed.json"))
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "yes"
    )


def test_can_detach_returns_yes_for_bootstrap_rebooting(requests_mock: Mocker) -> None:
    """Return 'yes' for rendezvous when status_info is 'Rebooting' (disk write complete)."""
    requests_mock.get(
        HOST_URL, json=fxt.get_anymarkup("host_infra_env_bootstrap_rebooting.json")
    )
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "yes"
    )


def test_can_detach_returns_no_when_installing(requests_mock: Mocker) -> None:
    """Return 'no' when host is installing-in-progress (disk write underway)."""
    requests_mock.get(
        HOST_URL, json=fxt.get_anymarkup("host_infra_env_installing.json")
    )
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "no"
    )


def test_can_detach_returns_no_for_bootstrap_not_yet_rebooting(
    requests_mock: Mocker,
) -> None:
    """Return 'no' for rendezvous still writing image (not yet rebooting)."""
    requests_mock.get(
        HOST_URL,
        json={
            "bootstrap": True,
            "id": HOST_ID,
            "infra_env_id": INFRA_ENV_ID,
            "status": "installing-in-progress",
            "status_info": "Writing image to disk",
        },
    )
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "no"
    )


def test_can_detach_returns_error_when_status_error(requests_mock: Mocker) -> None:
    """Return 'error' when host status is 'error' (installation failed)."""
    requests_mock.get(HOST_URL, json=fxt.get_anymarkup("host_infra_env_error.json"))
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "error"
    )


def test_can_detach_returns_error_when_status_cancelled(requests_mock: Mocker) -> None:
    """Return 'error' when host status is 'cancelled' (installation aborted)."""
    requests_mock.get(
        HOST_URL,
        json={"id": HOST_ID, "infra_env_id": INFRA_ENV_ID, "status": "cancelled"},
    )
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "error"
    )


def test_can_detach_returns_unavailable_on_connection_error(
    requests_mock: Mocker,
) -> None:
    """Return 'unavailable' when Assisted Service is unreachable."""
    requests_mock.get(HOST_URL, exc=requests.exceptions.ConnectionError)
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "unavailable"
    )


def test_can_detach_returns_unavailable_on_timeout(requests_mock: Mocker) -> None:
    """Return 'unavailable' when the Assisted Service request times out."""
    requests_mock.get(HOST_URL, exc=requests.exceptions.Timeout)
    assert (
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)
        == "unavailable"
    )


def test_can_detach_raises_on_http_error(requests_mock: Mocker) -> None:
    """Raise RuntimeError with HTTP status when the service returns a non-2xx response."""
    requests_mock.get(HOST_URL, status_code=401)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)


def test_can_detach_raises_on_invalid_json(requests_mock: Mocker) -> None:
    """Raise RuntimeError when the Assisted Service response body is not valid JSON."""
    requests_mock.get(HOST_URL, text="not-valid-json", status_code=200)
    with pytest.raises(RuntimeError, match="not valid JSON"):
        mod.can_detach(SERVICE_URL, INFRA_ENV_ID, HOST_ID, AUTH_TOKEN, HOST_NAME)


def test_detach_ironic_vmedia_success_204(requests_mock: Mocker) -> None:
    """Complete without error when Ironic returns 204 (vmedia detached)."""
    requests_mock.get(
        f"{IRONIC_URL}/v1/nodes/{HOST_NAME}",
        json={"uuid": NODE_UUID, "name": HOST_NAME},
    )
    requests_mock.delete(f"{IRONIC_URL}/v1/nodes/{NODE_UUID}/vmedia", status_code=204)
    mod.detach_ironic_vmedia(
        IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
    )


def test_detach_ironic_vmedia_already_absent_404(requests_mock: Mocker) -> None:
    """Complete without error when Ironic returns 404 (vmedia already absent)."""
    requests_mock.get(
        f"{IRONIC_URL}/v1/nodes/{HOST_NAME}",
        json={"uuid": NODE_UUID, "name": HOST_NAME},
    )
    requests_mock.delete(f"{IRONIC_URL}/v1/nodes/{NODE_UUID}/vmedia", status_code=404)
    mod.detach_ironic_vmedia(
        IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
    )


def test_detach_ironic_vmedia_node_get_connection_error(requests_mock: Mocker) -> None:
    """Raise RuntimeError when Ironic is unreachable during the node UUID lookup."""
    requests_mock.get(
        f"{IRONIC_URL}/v1/nodes/{HOST_NAME}", exc=requests.exceptions.ConnectionError
    )
    with pytest.raises(RuntimeError, match="cannot reach Ironic"):
        mod.detach_ironic_vmedia(
            IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
        )


def test_detach_ironic_vmedia_node_get_timeout(requests_mock: Mocker) -> None:
    """Raise RuntimeError when the node UUID lookup request times out."""
    requests_mock.get(
        f"{IRONIC_URL}/v1/nodes/{HOST_NAME}", exc=requests.exceptions.Timeout
    )
    with pytest.raises(RuntimeError, match="cannot reach Ironic"):
        mod.detach_ironic_vmedia(
            IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
        )


def test_detach_ironic_vmedia_node_get_non_200(requests_mock: Mocker) -> None:
    """Raise RuntimeError when the node UUID lookup returns a non-200 status."""
    requests_mock.get(f"{IRONIC_URL}/v1/nodes/{HOST_NAME}", status_code=404)
    with pytest.raises(RuntimeError, match="failed to get Ironic node UUID"):
        mod.detach_ironic_vmedia(
            IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
        )


def test_detach_ironic_vmedia_node_get_invalid_json(requests_mock: Mocker) -> None:
    """Raise RuntimeError when the node response body is not valid JSON."""
    requests_mock.get(
        f"{IRONIC_URL}/v1/nodes/{HOST_NAME}", text="not-valid-json", status_code=200
    )
    with pytest.raises(RuntimeError, match="not valid JSON"):
        mod.detach_ironic_vmedia(
            IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
        )


def test_detach_ironic_vmedia_node_get_missing_uuid(requests_mock: Mocker) -> None:
    """Raise RuntimeError when the node JSON response lacks the 'uuid' field."""
    requests_mock.get(f"{IRONIC_URL}/v1/nodes/{HOST_NAME}", json={"name": HOST_NAME})
    with pytest.raises(RuntimeError, match="missing 'uuid'"):
        mod.detach_ironic_vmedia(
            IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
        )


def test_detach_ironic_vmedia_delete_connection_error(requests_mock: Mocker) -> None:
    """Raise RuntimeError when Ironic is unreachable during the vmedia DELETE."""
    requests_mock.get(
        f"{IRONIC_URL}/v1/nodes/{HOST_NAME}",
        json={"uuid": NODE_UUID, "name": HOST_NAME},
    )
    requests_mock.delete(
        f"{IRONIC_URL}/v1/nodes/{NODE_UUID}/vmedia",
        exc=requests.exceptions.ConnectionError,
    )
    with pytest.raises(RuntimeError, match="cannot reach Ironic"):
        mod.detach_ironic_vmedia(
            IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
        )


def test_detach_ironic_vmedia_delete_unexpected_status(requests_mock: Mocker) -> None:
    """Raise RuntimeError when the vmedia DELETE returns an unexpected status code."""
    requests_mock.get(
        f"{IRONIC_URL}/v1/nodes/{HOST_NAME}",
        json={"uuid": NODE_UUID, "name": HOST_NAME},
    )
    requests_mock.delete(f"{IRONIC_URL}/v1/nodes/{NODE_UUID}/vmedia", status_code=500)
    with pytest.raises(RuntimeError, match="failed to detach vmedia"):
        mod.detach_ironic_vmedia(
            IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
        )


def test_main_detaches_on_yes(mocker: MockerFixture) -> None:
    """Detach vmedia immediately when can_detach returns 'yes'."""
    mocker.patch("enclave.tools.wait_and_detach_vmedia.time.sleep")
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.identify_host",
        return_value=(INFRA_ENV_ID, HOST_ID),
    )
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.can_detach",
        return_value="yes",
    )
    mock_detach = mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.detach_ironic_vmedia"
    )
    mod.main(**MAIN_KWARGS)
    mock_detach.assert_called_once_with(
        IRONIC_URL, IRONIC_VERSION, IRONIC_USER, IRONIC_PASSWORD, HOST_NAME
    )


def test_main_polls_through_no_then_detaches(mocker: MockerFixture) -> None:
    """Poll through 'no' decisions before detaching when 'yes' is returned."""
    mocker.patch("enclave.tools.wait_and_detach_vmedia.time.sleep")
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.identify_host",
        return_value=(INFRA_ENV_ID, HOST_ID),
    )
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.can_detach",
        side_effect=["no", "no", "yes"],
    )
    mock_detach = mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.detach_ironic_vmedia"
    )
    mod.main(**MAIN_KWARGS)
    mock_detach.assert_called_once()


def test_main_skips_detach_on_error(mocker: MockerFixture) -> None:
    """Exit without detaching when can_detach returns 'error' (installation failed)."""
    mocker.patch("enclave.tools.wait_and_detach_vmedia.time.sleep")
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.identify_host",
        return_value=(INFRA_ENV_ID, HOST_ID),
    )
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.can_detach",
        return_value="error",
    )
    mock_detach = mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.detach_ironic_vmedia"
    )
    mod.main(**MAIN_KWARGS)
    mock_detach.assert_not_called()


def test_main_detaches_after_unavailable_streak(mocker: MockerFixture) -> None:
    """3 consecutive 'unavailable' returns trigger detach (rendezvous-rebooted heuristic)."""
    mocker.patch("enclave.tools.wait_and_detach_vmedia.time.sleep")
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.identify_host",
        return_value=(INFRA_ENV_ID, HOST_ID),
    )
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.can_detach",
        return_value="unavailable",
    )
    mock_detach = mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.detach_ironic_vmedia"
    )
    mod.main(**MAIN_KWARGS)
    mock_detach.assert_called_once()


def test_main_resets_streak_on_recovery(mocker: MockerFixture) -> None:
    """Fewer than 3 consecutive 'unavailable' followed by 'no' resets the streak."""
    mocker.patch("enclave.tools.wait_and_detach_vmedia.time.sleep")
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.identify_host",
        return_value=(INFRA_ENV_ID, HOST_ID),
    )
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.can_detach",
        side_effect=["unavailable", "unavailable", "no", "yes"],
    )
    mock_detach = mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.detach_ironic_vmedia"
    )
    mod.main(**MAIN_KWARGS)
    mock_detach.assert_called_once()


def test_main_raises_on_timeout(mocker: MockerFixture) -> None:
    """Raise RuntimeError when the deadline elapses before a terminal decision is reached."""
    mocker.patch("enclave.tools.wait_and_detach_vmedia.time.sleep")
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.identify_host",
        return_value=(INFRA_ENV_ID, HOST_ID),
    )
    mocker.patch(
        "enclave.tools.wait_and_detach_vmedia.can_detach",
        return_value="no",
    )
    mocker.patch("enclave.tools.wait_and_detach_vmedia.detach_ironic_vmedia")
    with pytest.raises(RuntimeError, match="timeout"):
        mod.main(**MAIN_KWARGS, max_seconds=0)

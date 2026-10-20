import json

import pytest
from pytest_mock import MockerFixture

from enclave.reconcile.operator_versions import (
    _update_subscription_channel,
    approve_install_plans,
    reconcile,
)
from tests.fixtures import Fixtures, OcResultFactory

fxt = Fixtures("operator_versions")


def _plan(name: str, phase: str, csv_names: list[str]) -> dict:
    """Build a minimal InstallPlan dict with the fields approve_install_plans reads."""
    return {
        "metadata": {"name": name},
        "status": {"phase": phase},
        "spec": {"clusterServiceVersionNames": csv_names},
    }


# ---------------------------------------------------------------------------
# approve_install_plans
# ---------------------------------------------------------------------------


def test_approve_get_targets_correct_namespace(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """The oc get installplan command is scoped to the requested namespace."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout='{"items": []}'),
    )
    dry_run = False
    approve_install_plans(dry_run, "quay-enterprise", {})
    assert mock_run.call_count == 1
    get_args = mock_run.call_args.args[0]
    assert "get" in get_args
    assert "-n" in get_args
    assert "quay-enterprise" in get_args


def test_approve_skips_non_requiring_approval(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Plans in phases other than RequiresApproval are logged and skipped."""
    plan = _plan("ip-abc", "Complete", ["quay-operator.v3.15.3"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=json.dumps({"items": [plan]})),
    )
    dry_run = False
    approve_install_plans(dry_run, "quay-enterprise", {"quay-operator": "3.15.3"})
    assert mock_run.call_count == 1


def test_approve_skips_unmanaged_csv(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Plans whose CSV operator name is not in the approved map are skipped."""
    plan = _plan("ip-abc", "RequiresApproval", ["some-other-operator.v1.0.0"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=json.dumps({"items": [plan]})),
    )
    dry_run = False
    approve_install_plans(dry_run, "ns", {"quay-operator": "3.15.3"})
    assert mock_run.call_count == 1


def test_approve_skips_newer_version(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Plans whose CSV version exceeds the desired version are not approved."""
    plan = _plan("ip-abc", "RequiresApproval", ["quay-operator.v3.15.4"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=json.dumps({"items": [plan]})),
    )
    dry_run = False
    approve_install_plans(dry_run, "ns", {"quay-operator": "3.15.3"})
    assert mock_run.call_count == 1


def test_approve_approves_matching_version(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A plan at exactly the desired version triggers an oc patch to approve it."""
    plan = _plan("ip-abc", "RequiresApproval", ["quay-operator.v3.15.3"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps({"items": [plan]})),
            oc_result(stdout="patched"),
        ],
    )
    dry_run = False
    approve_install_plans(dry_run, "quay-enterprise", {"quay-operator": "3.15.3"})
    assert mock_run.call_count == 2
    patch_call = mock_run.call_args_list[1]
    assert "patch" in patch_call.args[0]
    assert "ip-abc" in patch_call.args[0]


def test_approve_dry_run_skips_patch(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """In dry-run mode the oc patch is never issued, only the oc get."""
    plan = _plan("ip-abc", "RequiresApproval", ["quay-operator.v3.15.3"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=json.dumps({"items": [plan]})),
    )
    dry_run = True
    approve_install_plans(dry_run, "quay-enterprise", {"quay-operator": "3.15.3"})
    assert mock_run.call_count == 1
    assert "get" in mock_run.call_args.args[0]
    assert "patch" not in mock_run.call_args.args[0]


def test_approve_older_version_is_approved(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A plan below the desired version is still approved (catch-up install)."""
    plan = _plan("ip-abc", "RequiresApproval", ["quay-operator.v3.15.2"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps({"items": [plan]})),
            oc_result(stdout="patched"),
        ],
    )
    dry_run = False
    approve_install_plans(dry_run, "quay-enterprise", {"quay-operator": "3.15.3"})
    assert mock_run.call_count == 2


def test_approve_get_failure_raises(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A non-zero exit code from oc get installplan raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout="", returncode=1, stderr="connection refused"),
    )
    dry_run = False
    with pytest.raises(RuntimeError, match="oc get installplan"):
        approve_install_plans(dry_run, "ns", {"quay-operator": "3.15.3"})


def test_approve_patch_failure_raises(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A non-zero exit code from oc patch installplan raises RuntimeError."""
    plan = _plan("ip-abc", "RequiresApproval", ["quay-operator.v3.15.3"])
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps({"items": [plan]})),
            oc_result(stdout="", returncode=1, stderr="forbidden"),
        ],
    )
    dry_run = False
    with pytest.raises(RuntimeError, match="oc patch installplan"):
        approve_install_plans(dry_run, "quay-enterprise", {"quay-operator": "3.15.3"})


def test_approve_wait_retries_retrieval_failures_and_timeouts(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Retry failed and timed-out InstallPlan queries until the target appears."""
    target_csv = "quay-operator.v3.15.3"
    plan = _plan("ip-abc", "Complete", [target_csv])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            TimeoutError("oc timed out"),
            oc_result(stdout="", returncode=1, stderr="connection refused"),
            oc_result(stdout=json.dumps({"items": []})),
            oc_result(stdout=json.dumps({"items": [plan]})),
        ],
    )
    mock_sleep = mocker.patch("enclave.reconcile.operator_versions.time.sleep")
    mocker.patch(
        "enclave.reconcile.operator_versions.time.monotonic",
        side_effect=[0.0, 1.0, 2.0, 3.0],
    )

    approve_install_plans(
        dry_run=False,
        namespace="quay-enterprise",
        approved_op_version_map={"quay-operator": "3.15.3"},
        wait_for_csvs={target_csv},
    )

    assert mock_run.call_count == 4
    assert mock_sleep.call_count == 3


def test_approve_wait_times_out_without_target_plan(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Raise after the InstallPlan deadline if the target CSV never appears."""
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout='{"items": []}'),
    )
    mocker.patch(
        "enclave.reconcile.operator_versions.time.monotonic",
        side_effect=[0.0, 1800.0],
    )

    with pytest.raises(TimeoutError, match="No InstallPlan containing target CSVs"):
        approve_install_plans(
            dry_run=False,
            namespace="quay-enterprise",
            approved_op_version_map={"quay-operator": "3.15.3"},
            wait_for_csvs={"quay-operator.v3.15.3"},
        )


def test_approve_wait_times_out_after_retrieval_timeout(
    mocker: MockerFixture,
) -> None:
    """Stop retrying InstallPlan retrieval after the shared deadline."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=TimeoutError("oc timed out"),
    )
    mocker.patch(
        "enclave.reconcile.operator_versions.time.monotonic",
        side_effect=[0.0, 1800.0],
    )
    mocker.patch("enclave.reconcile.operator_versions.time.sleep")

    with pytest.raises(TimeoutError, match=r"oc get installplan.*timed out"):
        approve_install_plans(
            dry_run=False,
            namespace="quay-enterprise",
            approved_op_version_map={"quay-operator": "3.15.3"},
            wait_for_csvs={"quay-operator.v3.15.3"},
        )

    mock_run.assert_called_once()


def test_update_subscription_channel_skips_matching_channel(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Do not patch a Subscription that already uses the requested channel."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(
            stdout=json.dumps({
                "spec": {"channel": "stable-3.15"},
                "status": {"currentCSV": "quay-operator.v3.15.3"},
            })
        ),
    )

    assert _update_subscription_channel(
        dry_run=False,
        namespace="quay-enterprise",
        subscription_name="quay-operator",
        channel="stable-3.15",
    ) == (False, "quay-operator.v3.15.3", False)
    assert mock_run.call_count == 1


def test_update_subscription_channel_patches_changed_channel(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Patch a Subscription channel and preserve its currently installed CSV."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(
                stdout=json.dumps({
                    "spec": {"channel": "stable-3.14"},
                    "status": {"currentCSV": "quay-operator.v3.14.2"},
                })
            ),
            oc_result(stdout="patched"),
        ],
    )

    assert _update_subscription_channel(
        dry_run=False,
        namespace="quay-enterprise",
        subscription_name="quay-operator",
        channel="stable-3.15",
    ) == (True, "quay-operator.v3.14.2", False)
    patch_args = mock_run.call_args_list[1].args[0]
    assert patch_args[:4] == [
        "oc",
        "patch",
        "subscription.operators.coreos.com",
        "quay-operator",
    ]
    assert json.loads(patch_args[patch_args.index("-p") + 1]) == {
        "spec": {"channel": "stable-3.15"}
    }


def test_update_subscription_channel_dry_run_skips_patch(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Report a needed channel update without issuing the patch in dry-run mode."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout='{"spec": {"channel": "stable-3.14"}}'),
    )

    assert _update_subscription_channel(
        dry_run=True,
        namespace="quay-enterprise",
        subscription_name="quay-operator",
        channel="stable-3.15",
    ) == (True, None, False)
    assert mock_run.call_count == 1


def test_update_subscription_channel_channel_only_skips_automatic_approval(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Do not patch Automatic Subscriptions during channel-only reconciliation."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(
            stdout=json.dumps({
                "spec": {
                    "channel": "stable-3.14",
                    "installPlanApproval": "Automatic",
                },
                "status": {"currentCSV": "quay-operator.v3.14.2"},
            })
        ),
    )

    assert _update_subscription_channel(
        dry_run=False,
        namespace="quay-enterprise",
        subscription_name="quay-operator",
        channel="stable-3.15",
        channel_only=True,
    ) == (False, "quay-operator.v3.14.2", True)
    assert mock_run.call_count == 1


def test_update_subscription_channel_rejects_invalid_channel(
    mocker: MockerFixture,
) -> None:
    """Reject shell metacharacters before running any oc command."""
    mock_run = mocker.patch("enclave.reconcile.operator_versions.run_oc_command")

    with pytest.raises(ValueError, match="Invalid operator channel"):
        _update_subscription_channel(
            dry_run=False,
            namespace="quay-enterprise",
            subscription_name="quay-operator",
            channel="stable-3.15;id",
        )
    mock_run.assert_not_called()


def test_update_subscription_channel_get_failure_raises(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Raise when the Subscription cannot be fetched."""
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout="", returncode=1, stderr="not found"),
    )

    with pytest.raises(RuntimeError, match="oc get subscription"):
        _update_subscription_channel(
            dry_run=False,
            namespace="quay-enterprise",
            subscription_name="quay-operator",
            channel="stable-3.15",
        )


def test_update_subscription_channel_patch_failure_raises(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Raise when patching the Subscription channel fails."""
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout='{"spec": {"channel": "stable-3.14"}}'),
            oc_result(stdout="", returncode=1, stderr="forbidden"),
        ],
    )

    with pytest.raises(RuntimeError, match="oc patch subscription"):
        _update_subscription_channel(
            dry_run=False,
            namespace="quay-enterprise",
            subscription_name="quay-operator",
            channel="stable-3.15",
        )


# ---------------------------------------------------------------------------
# approve_install_plans — fixture-backed (real cluster data)
# ---------------------------------------------------------------------------


def test_approve_fixture_skips_newer_than_desired(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Real cluster data: install-72xtr (Complete) and install-9h5cl (v3.15.4) are both skipped when desired is 3.15.3."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=fxt.get_json("install_plans.yaml")),
    )
    dry_run = False
    approve_install_plans(dry_run, "quay-enterprise", {"quay-operator": "3.15.3"})
    assert mock_run.call_count == 1


def test_approve_fixture_approves_when_at_desired(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Real cluster data: install-9h5cl (v3.15.4, RequiresApproval) is approved when desired is 3.15.4."""
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=fxt.get_json("install_plans.yaml")),
            oc_result(stdout="patched"),
        ],
    )
    dry_run = False
    approve_install_plans(dry_run, "quay-enterprise", {"quay-operator": "3.15.4"})
    assert mock_run.call_count == 2
    patch_call = mock_run.call_args_list[1]
    assert "install-9h5cl" in patch_call.args[0]


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def test_reconcile_calls_approve_and_wait(mocker: MockerFixture) -> None:
    """reconcile calls approve_install_plans then waits for each CSV to reach Succeeded."""
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )
    dry_run = False
    reconcile("3.15.3", "quay-enterprise", ["quay-operator"], dry_run=dry_run)
    mock_approve.assert_called_once_with(
        dry_run,
        "quay-enterprise",
        {"quay-operator": "3.15.3"},
    )
    mock_wait.assert_called_once_with(
        "clusterserviceversion.operators.coreos.com",
        "quay-operator.v3.15.3",
        "phase",
        "Succeeded",
        namespace="quay-enterprise",
        timeout_minutes=30,
        sleep_interval=10,
    )


def test_reconcile_waits_for_install_plan_after_channel_change(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Wait for a target InstallPlan when a channel change needs an upgrade."""
    subscription = {
        "spec": {"channel": "stable-3.14"},
        "status": {"currentCSV": "quay-operator.v3.14.2"},
    }
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps(subscription)),
            oc_result(stdout="patched"),
        ],
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mocker.patch("enclave.reconcile.operator_versions.wait_for_resource_status")
    dry_run = False

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run,
        channel="stable-3.15",
        subscription_name="quay-operator",
    )

    mock_approve.assert_called_once_with(
        dry_run,
        "quay-enterprise",
        {"quay-operator": "3.15.3"},
        wait_for_csvs={"quay-operator.v3.15.3"},
    )


def test_reconcile_skips_plan_wait_when_target_csv_is_installed(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Skip the plan wait when Subscription status already reports the target CSV."""
    subscription = {
        "spec": {"channel": "stable-3.14"},
        "status": {"currentCSV": "quay-operator.v3.15.3"},
    }
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps(subscription)),
            oc_result(stdout="patched"),
        ],
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mocker.patch("enclave.reconcile.operator_versions.wait_for_resource_status")
    dry_run = False

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run,
        channel="stable-3.15",
        subscription_name="quay-operator",
    )

    mock_approve.assert_called_once_with(
        dry_run, "quay-enterprise", {"quay-operator": "3.15.3"}
    )


def test_reconcile_dry_run_does_not_wait_for_plan_after_channel_change(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Do not patch or poll for a plan after a channel change in dry-run mode."""
    subscription = {
        "spec": {"channel": "stable-3.14"},
        "status": {"currentCSV": "quay-operator.v3.14.2"},
    }
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=json.dumps(subscription)),
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )
    dry_run = True

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run,
        channel="stable-3.15",
        subscription_name="quay-operator",
    )

    assert mock_run.call_count == 1
    mock_approve.assert_called_once_with(
        dry_run, "quay-enterprise", {"quay-operator": "3.15.3"}
    )
    mock_wait.assert_not_called()


def test_reconcile_channel_only_patches_and_waits_without_approving(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Stage a channel change and target InstallPlan without approving it."""
    subscription = {
        "spec": {
            "channel": "stable-3.14",
            "installPlanApproval": "Manual",
        },
        "status": {"currentCSV": "quay-operator.v3.14.2"},
    }
    plan = _plan("ip-abc", "RequiresApproval", ["quay-operator.v3.15.3"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps(subscription)),
            oc_result(stdout="patched"),
            oc_result(stdout=json.dumps({"items": [plan]})),
        ],
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run=False,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=True,
    )

    assert mock_run.call_count == 3
    patch_args = mock_run.call_args_list[1].args[0]
    assert json.loads(patch_args[patch_args.index("-p") + 1]) == {
        "spec": {"channel": "stable-3.15"}
    }
    mock_approve.assert_not_called()
    mock_wait.assert_not_called()


def test_reconcile_channel_only_requires_channel(mocker: MockerFixture) -> None:
    """Reject direct channel-only calls that do not provide a channel."""
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )

    with pytest.raises(ValueError, match="channel is required"):
        reconcile(
            "3.15.3",
            "quay-enterprise",
            ["quay-operator"],
            dry_run=False,
            channel_only=True,
        )

    mock_approve.assert_not_called()


def test_reconcile_channel_only_waits_for_remaining_multi_csv_targets(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Wait for target CSVs that are not already reported as current."""
    subscription = {
        "spec": {
            "channel": "stable-3.15",
            "installPlanApproval": "Manual",
        },
        "status": {"currentCSV": "quay-operator.v3.15.3"},
    }
    plan = _plan(
        "ip-abc",
        "RequiresApproval",
        ["quay-dependency.v3.15.3"],
    )
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps(subscription)),
            oc_result(stdout=json.dumps({"items": [plan]})),
        ],
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator", "quay-dependency"],
        dry_run=False,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=True,
    )

    assert mock_run.call_count == 2
    mock_approve.assert_not_called()
    mock_wait.assert_not_called()


def test_reconcile_channel_only_waits_on_matching_channel(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Wait for a z-stream InstallPlan when the channel is already correct."""
    subscription = {
        "spec": {
            "channel": "stable-3.15",
            "installPlanApproval": "Manual",
        },
        "status": {"currentCSV": "quay-operator.v3.15.2"},
    }
    plan = _plan("ip-abc", "RequiresApproval", ["quay-operator.v3.15.3"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps(subscription)),
            oc_result(stdout=json.dumps({"items": [plan]})),
        ],
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run=False,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=True,
    )

    assert mock_run.call_count == 2
    mock_approve.assert_not_called()
    mock_wait.assert_not_called()


def test_reconcile_channel_only_dry_run_skips_patch_and_wait(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Validate a channel-only change without patching or polling in dry-run."""
    subscription = {
        "spec": {
            "channel": "stable-3.14",
            "installPlanApproval": "Manual",
        },
        "status": {"currentCSV": "quay-operator.v3.14.2"},
    }
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=json.dumps(subscription)),
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run=True,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=True,
    )

    assert mock_run.call_count == 1
    mock_approve.assert_not_called()
    mock_wait.assert_not_called()


def test_reconcile_channel_only_skips_automatic_approval(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Do not patch or poll Automatic Subscriptions during channel-only mode."""
    subscription = {
        "spec": {
            "channel": "stable-3.14",
            "installPlanApproval": "Automatic",
        },
        "status": {"currentCSV": "quay-operator.v3.14.2"},
    }
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        return_value=oc_result(stdout=json.dumps(subscription)),
    )
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )

    reconcile(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run=False,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=True,
    )

    assert mock_run.call_count == 1
    mock_approve.assert_not_called()
    mock_wait.assert_not_called()


def test_reconcile_dry_run_skips_wait(mocker: MockerFixture) -> None:
    """In dry-run mode reconcile calls approve but never waits for CSV status."""
    mocker.patch("enclave.reconcile.operator_versions.approve_install_plans")
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )
    reconcile("3.15.3", "quay-enterprise", ["quay-operator"], dry_run=True)
    mock_wait.assert_not_called()


def test_reconcile_replaces_plus_in_version(mocker: MockerFixture) -> None:
    """'+' in the version string is normalised to '-' before building CSV names."""
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mocker.patch("enclave.reconcile.operator_versions.wait_for_resource_status")
    dry_run = False
    reconcile(
        "4.20.0+202602261925", "metallb-system", ["metallb-operator"], dry_run=dry_run
    )
    mock_approve.assert_called_once_with(
        dry_run,
        "metallb-system",
        {"metallb-operator": "4.20.0-202602261925"},
    )


def test_reconcile_multiple_csv_names(mocker: MockerFixture) -> None:
    """When multiple CSV names are given, approve is called once and wait is called per CSV."""
    mock_approve = mocker.patch(
        "enclave.reconcile.operator_versions.approve_install_plans"
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.operator_versions.wait_for_resource_status"
    )
    dry_run = False
    reconcile(
        "5.0.3",
        "openshift-update-service",
        ["update-service-operator", "another-csv"],
        dry_run=dry_run,
    )
    mock_approve.assert_called_once_with(
        dry_run,
        "openshift-update-service",
        {"update-service-operator": "5.0.3", "another-csv": "5.0.3"},
    )
    assert mock_wait.call_count == 2
    called_names = [c.args[1] for c in mock_wait.call_args_list]
    assert "update-service-operator.v5.0.3" in called_names
    assert "another-csv.v5.0.3" in called_names

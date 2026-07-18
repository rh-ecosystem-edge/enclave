import json

import pytest
from pytest_mock import MockerFixture

from enclave.reconcile.operator_versions import (
    approve_and_wait_for_csv,
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


def test_reconcile_delegates_to_approve_and_wait_per_csv(mocker: MockerFixture) -> None:
    """reconcile calls approve_and_wait_for_csv once per CSV with the right arguments."""
    mock_aw = mocker.patch("enclave.reconcile.operator_versions.approve_and_wait_for_csv")
    dry_run = False
    reconcile("3.15.3", "quay-enterprise", ["quay-operator"], dry_run=dry_run)
    mock_aw.assert_called_once_with(
        dry_run,
        "quay-enterprise",
        {"quay-operator": "3.15.3"},
        "quay-operator",
        "3.15.3",
    )


def test_reconcile_dry_run_skips_wait(mocker: MockerFixture) -> None:
    """In dry-run mode reconcile calls approve_install_plans once and skips the wait."""
    mock_approve = mocker.patch("enclave.reconcile.operator_versions.approve_install_plans")
    mock_aw = mocker.patch("enclave.reconcile.operator_versions.approve_and_wait_for_csv")
    reconcile("3.15.3", "quay-enterprise", ["quay-operator"], dry_run=True)
    mock_approve.assert_called_once()
    mock_aw.assert_not_called()


def test_reconcile_replaces_plus_in_version(mocker: MockerFixture) -> None:
    """'+' in the version string is normalised to '-' before building CSV names."""
    mock_aw = mocker.patch("enclave.reconcile.operator_versions.approve_and_wait_for_csv")
    dry_run = False
    reconcile(
        "4.20.0+202602261925", "metallb-system", ["metallb-operator"], dry_run=dry_run
    )
    mock_aw.assert_called_once_with(
        dry_run,
        "metallb-system",
        {"metallb-operator": "4.20.0-202602261925"},
        "metallb-operator",
        "4.20.0-202602261925",
    )


def test_approve_and_wait_approves_intermediate_then_target(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """When OLM requires stepping through 2.10.2 before 2.10.3, approve_and_wait_for_csv must approve both.

    The 2.10.3 plan is created by OLM only after 2.10.2 installs, so approve_install_plans
    is called on every poll iteration.
    """
    intermediate_plan = _plan(
        "ip-intermediate", "RequiresApproval", ["my-operator.v2.10.2"]
    )
    target_plan = _plan("ip-target", "RequiresApproval", ["my-operator.v2.10.3"])
    mock_run = mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(
                stdout=json.dumps({"items": [intermediate_plan]})
            ),  # get plans (iter 1)
            oc_result(stdout="patched"),  # patch ip-intermediate
            oc_result(stdout="Installing"),  # get CSV phase → not done
            oc_result(
                stdout=json.dumps({"items": [target_plan]})
            ),  # get plans (iter 2)
            oc_result(stdout="patched"),  # patch ip-target
            oc_result(stdout="Succeeded"),  # get CSV phase → done
        ],
    )
    mocker.patch("enclave.reconcile.operator_versions.time.sleep")

    dry_run = False
    approve_and_wait_for_csv(
        dry_run, "test-ns", {"my-operator": "2.10.3"}, "my-operator", "2.10.3"
    )

    def contains(cmd: list[str], *substrings: str) -> bool:
        return all(any(s in part for part in cmd) for s in substrings)

    cmds = [call.args[0] for call in mock_run.call_args_list]
    assert contains(cmds[0], "get", "installplan")
    assert contains(cmds[1], "patch", "ip-intermediate")
    assert contains(cmds[2], "get", "clusterserviceversion")
    assert contains(cmds[3], "get", "installplan")
    assert contains(cmds[4], "patch", "ip-target")
    assert contains(cmds[5], "get", "clusterserviceversion")


def test_approve_and_wait_raises_on_timeout(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """approve_and_wait_for_csv raises TimeoutError when CSV never reaches Succeeded."""
    plan = _plan("ip-abc", "RequiresApproval", ["my-operator.v1.0.0"])
    mocker.patch(
        "enclave.reconcile.operator_versions.run_oc_command",
        side_effect=[
            oc_result(stdout=json.dumps({"items": [plan]})),
            oc_result(stdout="patched"),
            oc_result(stdout="Installing"),
        ],
    )
    mocker.patch("enclave.reconcile.operator_versions.time.sleep")
    mocker.patch("enclave.reconcile.operator_versions.time.time", side_effect=[0.0, 9999.0])

    dry_run = False
    with pytest.raises(TimeoutError, match=r"my-operator\.v1\.0\.0"):
        approve_and_wait_for_csv(
            dry_run, "test-ns", {"my-operator": "1.0.0"}, "my-operator", "1.0.0"
        )


def test_reconcile_multiple_csv_names(mocker: MockerFixture) -> None:
    """When multiple CSV names are given, approve_and_wait_for_csv is called once per CSV."""
    mock_aw = mocker.patch("enclave.reconcile.operator_versions.approve_and_wait_for_csv")
    op_version_map = {"update-service-operator": "5.0.3", "another-csv": "5.0.3"}
    reconcile(
        "5.0.3",
        "openshift-update-service",
        ["update-service-operator", "another-csv"],
        dry_run=False,
    )
    assert mock_aw.call_count == 2
    called_csv_names = [c.args[3] for c in mock_aw.call_args_list]
    assert "update-service-operator" in called_csv_names
    assert "another-csv" in called_csv_names
    for call in mock_aw.call_args_list:
        assert call.args[2] == op_version_map

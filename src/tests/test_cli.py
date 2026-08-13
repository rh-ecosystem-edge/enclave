import yaml
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.reconcile.cli import cli, defaults_path

_KC = {"KUBECONFIG": "/fake/kubeconfig"}


def test_cli_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Reconcile CLI" in result.output
    assert "resolve-quay-registry-ca" not in result.output
    assert "collect-node-image-digests" not in result.output


def test_operator_versions_help() -> None:
    result = CliRunner().invoke(cli, ["operator-versions", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--version" in result.output
    assert "--namespace" in result.output
    assert "--csv-name" in result.output
    assert "--dry-run" in result.output
    assert "--use-defaults" in result.output
    assert "--plugin" in result.output
    assert "--operators" not in result.output


def test_mgmt_cluster_version_help() -> None:
    result = CliRunner().invoke(cli, ["mgmt-cluster-version", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--version" in result.output
    assert "--use-defaults" in result.output
    assert "--timeout-minutes" in result.output
    assert "--sleep-interval" in result.output


def test_log_level_option() -> None:
    result = CliRunner().invoke(cli, ["--log-level", "DEBUG", "--help"])
    assert result.exit_code == 0


def test_invalid_log_level() -> None:
    result = CliRunner().invoke(cli, ["--log-level", "INVALID"])
    assert result.exit_code != 0


def test_operator_versions_csv_name_defaults_to_name(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "quay-operator",
            "--version",
            "3.15.3",
            "--namespace",
            "quay-enterprise",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "3.15.3", "quay-enterprise", ["quay-operator"], dry_run
    )


def test_operator_versions_multiple_csv_names(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "metallb-operator",
            "--version",
            "4.20.0",
            "--namespace",
            "metallb-system",
            "--csv-name",
            "metallb-operator",
            "--csv-name",
            "metallb-operator-bundle",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "4.20.0",
        "metallb-system",
        ["metallb-operator", "metallb-operator-bundle"],
        dry_run,
    )


def test_use_defaults_calls_reconcile_per_operator(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    defaults_file = defaults_path("operators.yaml")
    with defaults_file.open(encoding="utf-8") as fh:
        operators = yaml.safe_load(fh)["operators"]
    dry_run = True
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count == len(operators)
    quay_op = next(op for op in operators if op["name"] == "quay-operator")
    mock_reconcile.assert_any_call(
        quay_op["version"],
        quay_op["namespace"],
        quay_op.get("csvNames") or [quay_op["name"]],
        dry_run,
    )


def test_use_defaults_mutual_exclusive_name() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--name", "foo"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_version() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--version", "1.0.0"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_csv_name() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--csv-name", "foo"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_operator_versions_missing_required_without_defaults() -> None:
    result = CliRunner().invoke(cli, ["operator-versions", "--name", "foo"], env=_KC)
    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_operator_versions_plugin_single_operator(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "lvms", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.20.0", "openshift-storage", ["lvms-operator"], dry_run
    )


def test_operator_versions_plugin_multiple_operators(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    # Mock a plugin with two separate operator entries
    plugin_yaml_content = """
name: test-plugin
type: foundation
order: 10
installOperators: true
operators:
  - name: operator-a
    version: 1.0.0
    namespace: ns-a
    csvNames:
      - operator-a
      - operator-a-bundle
  - name: operator-b
    version: 2.0.0
    namespace: ns-b
"""
    mocker.patch("pathlib.Path.open", mocker.mock_open(read_data=plugin_yaml_content))
    dry_run = True
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "test-plugin", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count == 2

    # Verify both operators were reconciled with correct arguments
    mock_reconcile.assert_any_call(
        "1.0.0", "ns-a", ["operator-a", "operator-a-bundle"], dry_run
    )
    mock_reconcile.assert_any_call("2.0.0", "ns-b", ["operator-b"], dry_run)


def test_operator_versions_plugin_real_odf(mocker: MockerFixture) -> None:
    """Test with real ODF plugin that has one operator with many CSV names."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "odf", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count == 1
    mock_reconcile.assert_any_call(
        "4.20.7-rhodf",
        "openshift-storage",
        [
            "odf-operator",
            "odf-dependencies",
            "odf-csi-addons-operator",
            "rook-ceph-operator",
            "ocs-operator",
            "recipe",
            "mcg-operator",
            "odf-prometheus-operator",
            "ocs-client-operator",
            "cephcsi-operator",
            "odf-external-snapshotter-operator",
        ],
        dry_run,
    )


def test_operator_versions_plugin_invalid_operator_not_dict(
    mocker: MockerFixture,
) -> None:
    """Test that non-dict operator entries are rejected."""
    mocker.patch(
        "pathlib.Path.open",
        mocker.mock_open(
            read_data=(
                "name: test\ntype: addon\norder: 1\noperators:\n  - invalid-string\n"
            )
        ),
    )
    result = CliRunner().invoke(cli, ["operator-versions", "--plugin", "test"], env=_KC)
    assert result.exit_code != 0
    assert "not a mapping" in result.output


def test_operator_versions_plugin_invalid_operator_missing_name(
    mocker: MockerFixture,
) -> None:
    """Test that operator entries missing 'name' are rejected."""
    mocker.patch(
        "pathlib.Path.open",
        mocker.mock_open(
            read_data=(
                "name: test\ntype: addon\norder: 1\n"
                "operators:\n  - version: 1.0.0\n    namespace: ns\n"
            )
        ),
    )
    result = CliRunner().invoke(cli, ["operator-versions", "--plugin", "test"], env=_KC)
    assert result.exit_code != 0
    assert "invalid or missing 'name'" in result.output


def test_operator_versions_plugin_invalid_csvnames_not_list(
    mocker: MockerFixture,
) -> None:
    """Test that csvNames must be a list of strings."""
    mocker.patch(
        "pathlib.Path.open",
        mocker.mock_open(
            read_data=(
                "name: test\ntype: addon\norder: 1\n"
                "operators:\n  - name: op\n    version: 1.0.0\n"
                "    namespace: ns\n    csvNames: not-a-list\n"
            )
        ),
    )
    result = CliRunner().invoke(cli, ["operator-versions", "--plugin", "test"], env=_KC)
    assert result.exit_code != 0
    assert "invalid 'csvNames'" in result.output


def test_operator_versions_plugin_invalid_csvnames_blank(
    mocker: MockerFixture,
) -> None:
    """Test that blank/whitespace-only CSV names are rejected."""
    mocker.patch(
        "pathlib.Path.open",
        mocker.mock_open(
            read_data=(
                "name: test\ntype: addon\norder: 1\n"
                "operators:\n  - name: op\n    version: 1.0.0\n"
                "    namespace: ns\n    csvNames: ['valid', '  ', 'also-valid']\n"
            )
        ),
    )
    result = CliRunner().invoke(cli, ["operator-versions", "--plugin", "test"], env=_KC)
    assert result.exit_code != 0
    assert "invalid 'csvNames'" in result.output


def test_operator_versions_plugin_partial_validation_no_reconcile(
    mocker: MockerFixture,
) -> None:
    """Test that if one operator is invalid, no operators are reconciled."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    mocker.patch(
        "pathlib.Path.open",
        mocker.mock_open(
            read_data=(
                "name: test\ntype: addon\norder: 1\noperators:\n"
                "  - name: valid-op\n    version: 1.0.0\n    namespace: ns\n"
                "  - name: invalid-op\n    namespace: ns\n"  # missing version
            )
        ),
    )
    result = CliRunner().invoke(cli, ["operator-versions", "--plugin", "test"], env=_KC)
    assert result.exit_code != 0
    assert "invalid or missing 'version'" in result.output
    # Critical: no reconciliation should have occurred
    mock_reconcile.assert_not_called()


def test_operator_versions_plugin_mutual_exclusive_name() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "lvms", "--name", "foo"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_operator_versions_plugin_mutual_exclusive_use_defaults() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "lvms", "--use-defaults"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_operator_versions_plugin_not_found() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "nonexistent"], env=_KC
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_operator_versions_plugin_no_operators(mocker: MockerFixture) -> None:
    mocker.patch(
        "pathlib.Path.open",
        mocker.mock_open(read_data="name: test-plugin\ntype: addon\norder: 1\n"),
    )
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "test-plugin"], env=_KC
    )
    assert result.exit_code != 0
    assert "no operators defined" in result.output


def test_operator_versions_plugin_install_operators_false(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "pathlib.Path.open",
        mocker.mock_open(
            read_data="name: test-plugin\ntype: addon\norder: 1\ninstallOperators: false\n"
        ),
    )
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "test-plugin"], env=_KC
    )
    assert result.exit_code != 0
    assert "installOperators set to false" in result.output


def test_operator_versions_plugin_path_traversal_double_dot() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "../../../etc"], env=_KC
    )
    assert result.exit_code != 0
    assert "Invalid plugin name" in result.output


def test_operator_versions_plugin_path_traversal_slash() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "foo/bar"], env=_KC
    )
    assert result.exit_code != 0
    assert "Invalid plugin name" in result.output


def test_operator_versions_plugin_path_traversal_backslash() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--plugin", "foo\\bar"], env=_KC
    )
    assert result.exit_code != 0
    assert "Invalid plugin name" in result.output


def test_mgmt_cluster_version_with_version(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.20.21", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with("4.20.21", dry_run, 180, 60)


def test_mgmt_cluster_version_use_defaults(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with("4.20.29", dry_run, 180, 60)


def test_mgmt_cluster_version_use_defaults_mutual_exclusive_version() -> None:
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--version", "4.20.8"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_no_args_shows_help() -> None:
    result = CliRunner().invoke(cli, ["mgmt-cluster-version"])
    assert result.exit_code == 2
    assert "--version" in result.output
    assert "--use-defaults" in result.output


def test_reconcile_no_args_shows_help() -> None:
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2
    assert "Reconcile CLI" in result.output


def test_kubeconfig_missing_fails(mocker: MockerFixture) -> None:
    mocker.patch("enclave.utils.Path.exists", return_value=False)
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.14"], env={"KUBECONFIG": ""}
    )
    assert result.exit_code != 0
    assert "KUBECONFIG" in result.output

"""Unit tests for enclave.reconcile.release_signatures."""

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from enclave.reconcile.release_signatures import (
    SIGSTORE_GATE,
    MissingReleaseSignaturesError,
    MissingSignature,
    ReleaseSignaturesQueryError,
    ack_sigstore_gate,
    find_missing_signatures,
    get_release_mirrors,
    list_tags,
    manifest_digest,
    pull_secret_authfile,
    reconcile,
    signature_tag,
    signed_release_tags,
)
from tests.fixtures import OcResultFactory

MODULE = "enclave.reconcile.release_signatures"
LZ_MIRROR = "mirror.example:8443/openshift/release-images"
QUAY_MIRROR = "quay.apps.example/openshift/release-images"
DIGEST_420 = "sha256:" + "a" * 64
DIGEST_421 = "sha256:" + "b" * 64


def _idms(*entries: tuple[str, list[str]]) -> str:
    """Build an ImageDigestMirrorSet list with one item per (source, mirrors) entry."""
    items = [
        {"spec": {"imageDigestMirrors": [{"source": source, "mirrors": mirrors}]}}
        for source, mirrors in entries
    ]
    return json.dumps({"items": items})


def _configmap(data: dict[str, str] | None) -> str:
    """Build a ConfigMap JSON document."""
    return json.dumps({"data": data} if data is not None else {})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_signature_tag() -> None:
    """A manifest digest maps to the cosign signature tag."""
    assert signature_tag("sha256:abc") == "sha256-abc.sig"


def test_signed_release_tags_filters_versions_and_odd_tags() -> None:
    """Only release tags at 4.20 or later are selected."""
    tags = [
        "4.19.20-x86_64",
        "4.20.32-x86_64",
        "4.21.0-rc.1-x86_64",
        "4.21.32-aarch64",
        "5.0.1-multi",
        "sha256-" + "a" * 64 + ".sig",
        "latest",
        "4.21.32",
    ]
    assert signed_release_tags(tags) == [
        "4.20.32-x86_64",
        "4.21.0-rc.1-x86_64",
        "4.21.32-aarch64",
        "5.0.1-multi",
    ]


# ---------------------------------------------------------------------------
# get_release_mirrors
# ---------------------------------------------------------------------------


def test_get_release_mirrors_dedupes_release_source(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Only ocp-release mirrors are returned, without duplicates and in order."""
    payload = _idms(
        ("quay.io/openshift-release-dev/ocp-v4.0-art-dev", ["mirror.example/release"]),
        ("quay.io/openshift-release-dev/ocp-release", [LZ_MIRROR]),
        ("quay.io/openshift-release-dev/ocp-release", [QUAY_MIRROR, LZ_MIRROR]),
    )
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout=payload))
    assert get_release_mirrors() == [LZ_MIRROR, QUAY_MIRROR]


def test_get_release_mirrors_none(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """No ImageDigestMirrorSets gives no mirrors."""
    mocker.patch(
        f"{MODULE}.run_oc_command", return_value=oc_result(stdout='{"items": []}')
    )
    assert get_release_mirrors() == []


def test_get_release_mirrors_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A failing oc call raises ReleaseSignaturesQueryError."""
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(stderr="forbidden", returncode=1),
    )
    with pytest.raises(ReleaseSignaturesQueryError):
        get_release_mirrors()


def test_get_release_mirrors_invalid_json(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Invalid JSON from oc raises ReleaseSignaturesQueryError."""
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="nope"))
    with pytest.raises(ReleaseSignaturesQueryError):
        get_release_mirrors()


# ---------------------------------------------------------------------------
# pull_secret_authfile
# ---------------------------------------------------------------------------


def test_pull_secret_authfile_writes_private_file_and_removes_it(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """The decoded pull secret is written to a 0600 file that is removed afterwards."""
    auths = b'{"auths": {}}'
    secret = json.dumps({
        "data": {".dockerconfigjson": base64.b64encode(auths).decode()}
    })
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout=secret))
    with pull_secret_authfile() as path:
        authfile = Path(path)
        assert authfile.read_bytes() == auths
        assert authfile.stat().st_mode & 0o777 == 0o600
    assert not authfile.exists()


def test_pull_secret_authfile_missing_data(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A pull secret without .dockerconfigjson raises ReleaseSignaturesQueryError."""
    mocker.patch(
        f"{MODULE}.run_oc_command", return_value=oc_result(stdout='{"data": {}}')
    )
    with pytest.raises(ReleaseSignaturesQueryError), pull_secret_authfile():
        pass


# ---------------------------------------------------------------------------
# skopeo wrappers
# ---------------------------------------------------------------------------


def _skopeo(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["skopeo"], returncode, stdout, stderr)


def test_list_tags(mocker: MockerFixture) -> None:
    """list_tags returns the Tags array from skopeo list-tags."""
    mock_run = mocker.patch(
        f"{MODULE}.subprocess.run",
        return_value=_skopeo(json.dumps({"Tags": ["4.21.32-x86_64"]})),
    )
    assert list_tags(LZ_MIRROR, "/auth.json") == ["4.21.32-x86_64"]
    args = mock_run.call_args.args[0]
    assert args[:2] == ["skopeo", "list-tags"]
    assert args[-1] == f"docker://{LZ_MIRROR}"


def test_list_tags_invalid_json(mocker: MockerFixture) -> None:
    """Invalid skopeo output raises ReleaseSignaturesQueryError."""
    mocker.patch(f"{MODULE}.subprocess.run", return_value=_skopeo("not-json"))
    with pytest.raises(ReleaseSignaturesQueryError):
        list_tags(LZ_MIRROR, "/auth.json")


def test_skopeo_failure_raises(mocker: MockerFixture) -> None:
    """A failing skopeo call raises ReleaseSignaturesQueryError."""
    mocker.patch(
        f"{MODULE}.subprocess.run",
        return_value=_skopeo(returncode=1, stderr="unauthorized"),
    )
    with pytest.raises(ReleaseSignaturesQueryError):
        list_tags(LZ_MIRROR, "/auth.json")


def test_skopeo_timeout_raises(mocker: MockerFixture) -> None:
    """A skopeo timeout raises ReleaseSignaturesQueryError."""
    mocker.patch(
        f"{MODULE}.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["skopeo"], 120),
    )
    with pytest.raises(ReleaseSignaturesQueryError):
        list_tags(LZ_MIRROR, "/auth.json")


def test_manifest_digest(mocker: MockerFixture) -> None:
    """manifest_digest returns the stripped digest of mirror:tag."""
    mock_run = mocker.patch(
        f"{MODULE}.subprocess.run", return_value=_skopeo(f"{DIGEST_421}\n")
    )
    assert manifest_digest(LZ_MIRROR, "4.21.32-x86_64", "/auth.json") == DIGEST_421
    assert mock_run.call_args.args[0][-1] == f"docker://{LZ_MIRROR}:4.21.32-x86_64"


def test_manifest_digest_unexpected_output(mocker: MockerFixture) -> None:
    """Output that isn't a sha256 digest raises ReleaseSignaturesQueryError."""
    mocker.patch(f"{MODULE}.subprocess.run", return_value=_skopeo("garbage"))
    with pytest.raises(ReleaseSignaturesQueryError):
        manifest_digest(LZ_MIRROR, "4.21.32-x86_64", "/auth.json")


# ---------------------------------------------------------------------------
# find_missing_signatures
# ---------------------------------------------------------------------------


def _patch_registry(
    mocker: MockerFixture,
    tags: dict[str, list[str]],
    digests: dict[str, str],
) -> MagicMock:
    """Stub list_tags per mirror and manifest_digest per tag; return the digest mock."""
    mocker.patch(f"{MODULE}.list_tags", side_effect=lambda mirror, _auth: tags[mirror])
    return mocker.patch(
        f"{MODULE}.manifest_digest",
        side_effect=lambda _mirror, tag, _auth: digests[tag],
    )


def test_find_missing_signatures_all_present(mocker: MockerFixture) -> None:
    """Every release tag >= 4.20 with its signature tag gives no missing entries."""
    tags = ["4.19.1-x86_64", "4.20.32-x86_64", "4.21.32-x86_64"]
    tags += [signature_tag(DIGEST_420), signature_tag(DIGEST_421)]
    mock_digest = _patch_registry(
        mocker,
        {LZ_MIRROR: tags, QUAY_MIRROR: tags},
        {"4.20.32-x86_64": DIGEST_420, "4.21.32-x86_64": DIGEST_421},
    )
    assert find_missing_signatures([LZ_MIRROR, QUAY_MIRROR], "/auth.json") == (4, [])
    inspected = {call.args[1] for call in mock_digest.call_args_list}
    assert "4.19.1-x86_64" not in inspected


def test_find_missing_signatures_reports_per_mirror(mocker: MockerFixture) -> None:
    """A signature present in one mirror only is reported for the other mirror."""
    _patch_registry(
        mocker,
        {
            LZ_MIRROR: ["4.21.32-x86_64", signature_tag(DIGEST_421)],
            QUAY_MIRROR: ["4.21.32-x86_64"],
        },
        {"4.21.32-x86_64": DIGEST_421},
    )
    assert find_missing_signatures([LZ_MIRROR, QUAY_MIRROR], "/auth.json") == (
        2,
        [MissingSignature(QUAY_MIRROR, "4.21.32-x86_64", DIGEST_421)],
    )


# ---------------------------------------------------------------------------
# ack_sigstore_gate
# ---------------------------------------------------------------------------


def _patch_gate(
    mocker: MockerFixture,
    oc_result: OcResultFactory,
    gates: dict[str, str] | None,
    acks: dict[str, str] | None,
    patch_rc: int = 0,
) -> MagicMock:
    """Stub oc for admin-gates, admin-acks and the admin-acks patch."""

    def fake_oc(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "admin-gates" in args:
            return oc_result(stdout=_configmap(gates))
        if "patch" in args:
            return oc_result(stdout="patched", returncode=patch_rc)
        return oc_result(stdout=_configmap(acks))

    return mocker.patch(f"{MODULE}.run_oc_command", side_effect=fake_oc)


def _patched(mock_oc: MagicMock) -> bool:
    return any("patch" in call.args[0] for call in mock_oc.call_args_list)


def test_ack_sigstore_gate_acks_when_defined(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A defined, unacknowledged gate is acknowledged with a merge patch."""
    mock_oc = _patch_gate(mocker, oc_result, {SIGSTORE_GATE: "msg"}, None)
    ack_sigstore_gate(dry_run=False)
    patch_call = next(c for c in mock_oc.call_args_list if "patch" in c.args[0])
    assert json.loads(patch_call.args[0][-1]) == {"data": {SIGSTORE_GATE: "true"}}


def test_ack_sigstore_gate_not_defined(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Without the gate in admin-gates, nothing is patched."""
    mock_oc = _patch_gate(mocker, oc_result, {"ack-other": "msg"}, None)
    ack_sigstore_gate(dry_run=False)
    assert not _patched(mock_oc)


def test_ack_sigstore_gate_already_acked(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """An already acknowledged gate is not patched again."""
    mock_oc = _patch_gate(
        mocker, oc_result, {SIGSTORE_GATE: "msg"}, {SIGSTORE_GATE: "true"}
    )
    ack_sigstore_gate(dry_run=False)
    assert not _patched(mock_oc)


def test_ack_sigstore_gate_dry_run(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """In dry-run, the gate is not patched."""
    mock_oc = _patch_gate(mocker, oc_result, {SIGSTORE_GATE: "msg"}, {})
    ack_sigstore_gate(dry_run=True)
    assert not _patched(mock_oc)


def test_ack_sigstore_gate_patch_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A failing patch raises ReleaseSignaturesQueryError."""
    _patch_gate(mocker, oc_result, {SIGSTORE_GATE: "msg"}, None, patch_rc=1)
    with pytest.raises(ReleaseSignaturesQueryError):
        ack_sigstore_gate(dry_run=False)


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def _patch_reconcile(
    mocker: MockerFixture,
    mirrors: list[str],
    missing: list[MissingSignature],
    checked: int = 1,
) -> MagicMock:
    mocker.patch(f"{MODULE}.get_release_mirrors", return_value=mirrors)
    mocker.patch(
        f"{MODULE}.pull_secret_authfile"
    ).return_value.__enter__.return_value = "/auth.json"
    mocker.patch(f"{MODULE}.find_missing_signatures", return_value=(checked, missing))
    return mocker.patch(f"{MODULE}.ack_sigstore_gate")


def test_reconcile_all_present_acks_gate(mocker: MockerFixture) -> None:
    """With every signature present, the gate step runs."""
    mock_ack = _patch_reconcile(mocker, [LZ_MIRROR], [])
    reconcile(dry_run=False)
    dry_run = False
    mock_ack.assert_called_once_with(dry_run)


def test_reconcile_missing_raises_without_ack(mocker: MockerFixture) -> None:
    """Missing signatures raise and the gate is not acknowledged."""
    missing = [MissingSignature(LZ_MIRROR, "4.21.32-x86_64", DIGEST_421)]
    mock_ack = _patch_reconcile(mocker, [LZ_MIRROR], missing)
    with pytest.raises(MissingReleaseSignaturesError) as exc_info:
        reconcile(dry_run=False)
    assert f"{LZ_MIRROR}:4.21.32-x86_64" in str(exc_info.value)
    assert signature_tag(DIGEST_421) in str(exc_info.value)
    mock_ack.assert_not_called()


def test_reconcile_no_mirrors_skips(mocker: MockerFixture) -> None:
    """Without release mirrors, nothing is checked or acknowledged."""
    mock_ack = _patch_reconcile(mocker, [], [])
    reconcile(dry_run=False)
    mock_ack.assert_not_called()


def test_reconcile_no_signed_release_tags_skips_ack(mocker: MockerFixture) -> None:
    """If no release tag at 4.20 or later was checked, the gate is not acknowledged."""
    mock_ack = _patch_reconcile(mocker, [LZ_MIRROR], [], checked=0)
    reconcile(dry_run=False)
    mock_ack.assert_not_called()

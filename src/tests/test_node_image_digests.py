import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from enclave.tools.node_image_digests import (
    DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS,
    OC_DEBUG_CRICTL_TIMEOUT_SECONDS,
    collect_node_image_digests,
    extract_digest_refs_from_crictl_output,
    main,
    normalize_digest_ref,
    parse_exclude_contains,
    run_oc_debug_crictl_images,
)

_REGISTRY = "registry.example.com:5000"
_IMAGE = "org/app"
_DIGEST = "sha256:" + "a" * 64
_REF = f"{_REGISTRY}/{_IMAGE}@{_DIGEST}"


def test_normalize_digest_ref_lowercases_sha() -> None:
    upper = f"{_REGISTRY}/{_IMAGE}@sha256:{'A' * 64}"
    assert normalize_digest_ref(upper) == _REF


def test_normalize_digest_ref_rejects_invalid() -> None:
    assert normalize_digest_ref("") is None
    assert normalize_digest_ref("<none>") is None
    assert normalize_digest_ref("registry.example.com/app:latest") is None


def test_normalize_digest_ref_allows_multi_segment_path() -> None:
    deep_ref = f"{_REGISTRY}/org/sub-org/image@{_DIGEST}"
    assert normalize_digest_ref(deep_ref) == deep_ref


def test_normalize_digest_ref_logs_unsupported_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    invalid_name_ref = f"{_REGISTRY}/org//image@{_DIGEST}"
    assert normalize_digest_ref(invalid_name_ref) is None
    assert "unsupported image name" in caplog.text


def test_parse_exclude_contains_defaults() -> None:
    assert parse_exclude_contains(None) == list(DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS)
    assert parse_exclude_contains("") == list(DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS)


def test_parse_exclude_contains_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid --exclude-contains JSON"):
        parse_exclude_contains("not-json")


def test_parse_exclude_contains_rejects_non_array() -> None:
    with pytest.raises(TypeError, match="must be a JSON array"):
        parse_exclude_contains('{"foo": "bar"}')


def test_parse_exclude_contains_custom() -> None:
    assert parse_exclude_contains('["foo/"]') == ["foo/"]


def test_extract_digest_refs_from_crictl_output() -> None:
    payload = [
        {
            "repoDigests": [
                _REF,
                f"{_REGISTRY}/{_IMAGE}@sha256:{'b' * 64}",
            ],
        }
    ]
    text = f"noise before\n{json.dumps(payload)}\n"
    refs = extract_digest_refs_from_crictl_output(text, exclude_contains=[])
    assert refs == [
        f"{_REGISTRY}/{_IMAGE}@sha256:{'b' * 64}",
    ]


def test_extract_digest_refs_applies_exclude_contains() -> None:
    payload = [{"repoDigests": [_REF]}]
    refs = extract_digest_refs_from_crictl_output(
        json.dumps(payload),
        exclude_contains=["openshift-pipelines/"],
    )
    assert refs == [_REF]

    excluded = extract_digest_refs_from_crictl_output(
        json.dumps([
            {
                "repoDigests": [
                    "registry.example.com:5000/openshift-pipelines/foo@" + _DIGEST
                ]
            }
        ]),
        exclude_contains=["openshift-pipelines/"],
    )
    assert excluded == []

    lvms_catalog = (
        "mirror.enclave-test.lab:8443/redhat/redhat-operator-index-lvms@" + _DIGEST
    )
    assert (
        extract_digest_refs_from_crictl_output(
            json.dumps([{"repoDigests": [lvms_catalog]}]),
            exclude_contains=list(DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS),
        )
        == []
    )


def test_extract_digest_refs_dict_wrapper() -> None:
    payload = {"images": [{"repoDigests": [_REF]}]}
    refs = extract_digest_refs_from_crictl_output(
        json.dumps(payload), exclude_contains=[]
    )
    assert refs == [_REF]


def test_run_oc_debug_crictl_images_timeout(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.tools.node_image_digests.subprocess.run",
        side_effect=subprocess.TimeoutExpired(
            cmd="oc", timeout=OC_DEBUG_CRICTL_TIMEOUT_SECONDS
        ),
    )
    with pytest.raises(TimeoutError, match="timed out after"):
        run_oc_debug_crictl_images(node="node-0", oc="oc")


def test_collect_node_image_digests_calls_oc(mocker: MockerFixture) -> None:
    mock_run = mocker.patch(
        "enclave.tools.node_image_digests.run_oc_debug_crictl_images",
        return_value=json.dumps([{"repoDigests": [_REF]}]),
    )
    result = collect_node_image_digests("node-0", oc="/bin/oc")
    mock_run.assert_called_once_with(node="node-0", oc="/bin/oc")
    assert result.refs == [_REF]


def test_reconcile_emits_refs(
    mocker: MockerFixture, capsys: pytest.CaptureFixture
) -> None:
    mocker.patch(
        "enclave.tools.node_image_digests.collect_node_image_digests",
        return_value=MagicMock(refs=[_REF], raw_output="raw"),
    )
    main("node-0", oc="oc", exclude_contains_raw="[]")
    assert capsys.readouterr().out.strip() == _REF


def test_reconcile_emits_raw_on_empty_refs(
    mocker: MockerFixture, capsys: pytest.CaptureFixture
) -> None:
    mocker.patch(
        "enclave.tools.node_image_digests.collect_node_image_digests",
        return_value=MagicMock(refs=[], raw_output="crictl failed"),
    )
    main("node-0")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "crictl failed"


def test_reconcile_writes_raw_output_file_on_empty_refs(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    mocker.patch(
        "enclave.tools.node_image_digests.collect_node_image_digests",
        return_value=MagicMock(refs=[], raw_output="crictl failed"),
    )
    raw_output_file = tmp_path / "collect" / "node-0.log"

    with caplog.at_level("INFO"):
        main("node-0", raw_output_file=str(raw_output_file))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert "No digest refs were collected; raw output saved to" in caplog.text
    assert str(raw_output_file) in caplog.text
    assert raw_output_file.read_text(encoding="utf-8") == "crictl failed"

"""Fixture tests for the CI artifact scrubber (enclave-ci scrub-artifacts)."""

import base64
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from enclave_ci.scrub_artifacts.main import (
    REPORT_NAME,
    ScrubError,
    cli,
    scrub_directory,
    scrub_text,
)

AUTH = base64.b64encode(b"user:s3cr3t-registry-password").decode()
PULL_SECRET = json.dumps({
    "auths": {
        "cloud.openshift.com": {"auth": AUTH, "email": "dev@example.com"},
        "registry.redhat.io": {"auth": AUTH},
    }
})
JWT = (
    "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpbmZyYV9lbnZfaWQiOiJiYzdhMDJiNS0zMzVhIn0."
    "Wu9M-VbjaI60GiIsdcK6094Nj8H7nVXz08MskSiAc7Z"
)
ISO_URL = (
    "https://assisted-image-service.apps.hub.lab/byapikey/"
    f"{JWT}/4.20.32/x86_64/minimal.iso"
)
OCP_TOKEN = "sha256~AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # ruff: ignore[hardcoded-password-string] - fake fixture
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAsecretkeymaterial\n"
    "-----END RSA PRIVATE KEY-----"
)
KUBECONFIG_USER = (
    "users:\n- name: admin\n  user:\n"
    "    client-certificate-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t\n"
    "    client-key-data: LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0t\n"
)

SECRETS = [
    AUTH,
    JWT,
    OCP_TOKEN,
    "s3cr3t-pw",
    "secretkeymaterial",
    "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t",
    "LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0t",
    "abcdef0123456789token",
]


def assert_no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"secret survived scrubbing: {secret[:12]}..."


def all_text(root: Path) -> str:
    return "\n".join(
        p.read_text(errors="replace") for p in root.rglob("*") if p.is_file()
    )


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        (PULL_SECRET, "auth-field"),
        (json.dumps({"log": PULL_SECRET}), "auth-field"),  # escaped JSON in a log
        ('{"auths":{"x":{"password":"s3cr3t-pw"}}}', "auth-field"),
        (f'.dockerconfigjson: "{AUTH}{AUTH}"', "dockerconfigjson"),
        (KUBECONFIG_USER, "kubeconfig-credential"),
        (f"    token: {OCP_TOKEN}\n", "kubeconfig-credential"),
        (f"Image provisioning started for {ISO_URL}", "iso-download-token"),
        (f"isoDownloadURL: {ISO_URL}", "iso-download-token"),
        (f"token={JWT} other", "jwt"),
        (f"oc login --token {OCP_TOKEN}", "openshift-token"),
        ("Authorization: Bearer abcdef0123456789token", "bearer-token"),
        ("GET /api?api_key=abcdef0123456789token&x=1", "token-query-param"),
        ("pulling https://admin:s3cr3t-pw@quay.lab/v2/", "url-credentials"),
        (PRIVATE_KEY, "private-key"),
    ],
)
def test_scrub_text_redacts_each_pattern(text: str, pattern: str) -> None:
    scrubbed, counts = scrub_text(text)
    assert counts[pattern] >= 1
    assert_no_secrets(scrubbed)
    assert "REDACTED" in scrubbed


def test_scrub_text_keeps_ordinary_diagnostics() -> None:
    text = (
        "ClusterVersion Available=True\n"
        "api.ecd-ab12cd34-sno.ecd-ab12cd34.lab -> 192.168.5.30\n"
        "image: quay.io/openshift-release-dev/ocp-release@sha256:" + "a" * 64 + "\n"
        "upstream: https://service-route.apps.hub.lab/api/upgrades_info/v1/graph\n"
    )
    scrubbed, counts = scrub_text(text)
    assert scrubbed == text
    assert not counts


def _tar_gz(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    (root / "managed_sno" / "failure" / "hub").mkdir(parents=True)
    (root / "cluster").mkdir()
    (root / "managed_sno" / "failure" / "hub" / "events-infraenv.txt").write_text(
        f"Normal ProvisioningStarted Image provisioning started for {ISO_URL}\n"
    )
    (root / "cluster" / "pods.txt").write_text("pod-a Running\npod-b Running\n")
    (root / "managed_sno" / "failure" / "hub" / "install-logs.tar.gz").write_bytes(
        _tar_gz({
            "host/journal.log": f"pull secret: {PULL_SECRET}\n",
            "host/installer.log": "Bearer abcdef0123456789token\n",
        })
    )
    (root / "must-gather.zip").write_bytes(_zip({"gather/kc.yaml": KUBECONFIG_USER}))
    (root / "cluster" / "kubeconfig").write_text(KUBECONFIG_USER)
    (root / "core.bin").write_bytes(b"\x00\x01binary" + AUTH.encode())
    (root / "broken.tar.gz").write_bytes(b"not really an archive " + AUTH.encode())
    return root


def test_scrub_directory_removes_every_secret(bundle: Path) -> None:
    report = scrub_directory(bundle)

    assert_no_secrets(all_text(bundle))
    # Archives were opened and scrubbed, not just dropped.
    extracted = bundle / "managed_sno" / "failure" / "hub" / "install-logs.extracted"
    assert (extracted / "host" / "journal.log").read_text().count("REDACTED") == 2
    assert (bundle / "must-gather.extracted" / "gather" / "kc.yaml").exists()
    assert not (bundle / "must-gather.zip").exists()
    # Unscannable content and kubeconfigs are removed.
    assert not (bundle / "cluster" / "kubeconfig").exists()
    assert not (bundle / "core.bin").exists()
    assert not (bundle / "broken.tar.gz").exists()
    assert report.removed["cluster/kubeconfig"] == "kubeconfig"
    assert "binary" in report.removed["core.bin"]
    assert "could not be extracted" in report.removed["broken.tar.gz"]
    # Untouched files stay as they were.
    assert (bundle / "cluster" / "pods.txt").read_text() == (
        "pod-a Running\npod-b Running\n"
    )


def test_report_has_counts_but_no_values(bundle: Path) -> None:
    scrub_directory(bundle)
    report = (bundle / REPORT_NAME).read_text()
    assert "iso-download-token=1" in report
    assert "managed_sno/failure/hub/install-logs.tar.gz" in report
    assert_no_secrets(report)


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text()
        for p in root.rglob("*")
        if p.is_file() and p.name != REPORT_NAME
    }


def test_scrub_is_idempotent(bundle: Path) -> None:
    scrub_directory(bundle)
    first = _snapshot(bundle)
    second_report = scrub_directory(bundle)
    assert not second_report.removed
    assert not second_report.extracted
    assert not second_report.redactions
    assert _snapshot(bundle) == first


def test_unsafe_archive_member_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "evil.tar").write_bytes(
        _tar_gz({"../outside.txt": "escape"})  # gzip data with .tar name still opens
    )
    report = scrub_directory(root)
    assert not (tmp_path / "outside.txt").exists()
    assert "unsafe path" in report.removed["evil.tar"]


def test_unsafe_zip_member_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "evil.zip").write_bytes(_zip({"../outside.txt": "escape"}))
    report = scrub_directory(root)
    assert not (tmp_path / "outside.txt").exists()
    assert "unsafe path" in report.removed["evil.zip"]


def test_symlinks_are_removed(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text(AUTH)
    (root / "link.txt").symlink_to(secret)
    report = scrub_directory(root)
    assert not (root / "link.txt").exists()
    assert report.removed["link.txt"] == "symlink"
    assert secret.read_text() == AUTH  # the target outside the bundle is untouched


def test_scrub_directory_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(ScrubError):
        scrub_directory(tmp_path / "nope")


def test_cli_scrubs_and_skips_missing(bundle: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, [str(bundle), str(tmp_path / "missing")])
    assert result.exit_code == 0, result.output
    assert "redaction(s)" in result.output
    assert "does not exist, skipping" in result.output
    assert_no_secrets(all_text(bundle))


def test_cli_rejects_file_argument(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    result = CliRunner().invoke(cli, [str(f)])
    assert result.exit_code != 0

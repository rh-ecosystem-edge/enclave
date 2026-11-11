"""Exercise the default DNS shell validation without accessing host resolver state."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml


def _run_dns_validation(
    tmp_path: Path,
    resolv_conf: str,
    resolver_status: str,
    default_dns: str,
    *,
    active: bool = True,
    through_tee: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the real DNS block and emitter with fixture inputs and optional tee."""
    script = (Path(__file__).resolve().parents[2] / "validations.sh").read_text(
        encoding="utf-8"
    )
    validation_function = script.split("validation(){", 1)[1].split("checkDNS(){", 1)[0]
    dns_validation = script.split("# Make sure that LZ DNS is defaultDNS", 1)[1].split(
        "# Validate that defaultDNS is reachable", 1
    )[0]
    resolver_file = tmp_path / "resolv.conf"
    resolver_file.write_text(resolv_conf, encoding="utf-8")
    harness = r"""
grep() {
    if [[ "${!#}" == /etc/resolv.conf ]]; then
        command grep "${@:1:$#-1}" "$RESOLVER_FILE"
    else
        command grep "$@"
    fi
}
awk() {
    if [[ "${!#}" == /etc/resolv.conf ]]; then
        command awk "${@:1:$#-1}" "$RESOLVER_FILE"
    else
        command awk "$@"
    fi
}
systemctl() { return "$RESOLVED_EXIT"; }
resolvectl() { printf '%s\n' "$RESOLVER_STATUS"; }
printf 'validations:\n    dns:\n'
"""
    command = [
        "bash",
        "-c",
        harness + "validation(){" + validation_function + dns_validation,
    ]
    if through_tee:
        command = [
            "bash",
            "-o",
            "pipefail",
            "-c",
            'bash -c "$1" 2>&1 | tee -a "$2"',
            "dns-validation",
            command[-1],
            str(tmp_path / "validation.log"),
        ]
    return subprocess.run(
        command,
        env=os.environ
        | {
            "RESOLVER_FILE": str(resolver_file),
            "RESOLVER_STATUS": resolver_status,
            "RESOLVED_EXIT": "0" if active else "1",
            "default_dns": default_dns,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("resolved", [False, True], ids=["resolv-conf", "resolved"])
@pytest.mark.parametrize("default_dns", ["192.0.2.53", "198.51.100.53", "203.0.113.53"])
def test_default_dns_match_and_mismatch(
    tmp_path: Path, resolved: bool, default_dns: str
) -> None:
    """Match either detected address, not other configured resolvectl servers."""
    addresses = ("192.0.2.53", "198.51.100.53")
    result = _run_dns_validation(
        tmp_path,
        "nameserver 127.0.0.53\n"
        if resolved
        else "".join(f"nameserver {address}\n" for address in addresses),
        "Global\n Current DNS Server: 192.0.2.53\n"
        " DNS Servers: 192.0.2.53 203.0.113.53\n"
        "Link 2 (eth0)\n Current DNS Server: 198.51.100.53\n",
        default_dns,
    )
    matches = default_dns in addresses
    assert result.returncode == (0 if matches else 1), result.stderr
    record = yaml.safe_load(result.stdout)["validations"]["dns"][0]
    assert record["name"] == "default_dns"
    assert record["status"] == ("PASS" if matches else "FAIL")
    assert isinstance(record["description"], str)
    message = record["message"]
    assert f"config/global.yaml defaultDNS: {default_dns}" in message
    source = (
        "resolvectl status Current DNS Server entries"
        if resolved
        else "/etc/resolv.conf nameserver entries"
    )
    assert f"Detected Landing Zone DNS addresses from {source}:\n" in message
    assert "192.0.2.53\n198.51.100.53\n" in message
    assert "Authentication hostname suffixes are omitted" not in message
    if not matches:
        assert (
            "must match one of the detected Landing Zone DNS addresses"
            in record["description"]
        )
        assert "If the detected Landing Zone DNS is intended" in message
        assert "update defaultDNS in config/global.yaml" in message
        assert "If defaultDNS is the intended deployment DNS" in message
        assert "Landing Zone host's DNS configuration" in message
        assert "rerun ./sync.sh" in message
        assert "must match one of" in result.stdout


@pytest.mark.parametrize("resolved", [False, True], ids=["resolv-conf", "resolved"])
def test_default_dns_no_detected_addresses(tmp_path: Path, resolved: bool) -> None:
    """An empty resolver list remains a failure with explicit diagnostic output."""
    result = _run_dns_validation(
        tmp_path,
        "nameserver 127.0.0.53\n" if resolved else "",
        "Global\n DNS Servers: 203.0.113.53\n",
        "203.0.113.53",
    )
    assert result.returncode == 1
    record = yaml.safe_load(result.stdout)["validations"]["dns"][0]
    assert record["status"] == "FAIL"
    assert "<none detected>" in record["message"]
    assert "rerun ./sync.sh" in record["message"]


def test_default_dns_inactive_stub(tmp_path: Path) -> None:
    """An inactive stub must not be described as a detected upstream resolver."""
    result = _run_dns_validation(
        tmp_path,
        "nameserver 127.0.0.53\n",
        "Current DNS Server: 192.0.2.53\n",
        "192.0.2.53",
        active=False,
    )
    assert result.returncode == 1
    record = yaml.safe_load(result.stdout)["validations"]["dns"][0]
    assert record["status"] == "FAIL"
    assert record["description"] == "Landing Zone DNS could not be determined"
    message = record["message"]
    assert "config/global.yaml defaultDNS: 192.0.2.53" in message
    assert "/etc/resolv.conf contains 127.0.0.53 (local stub)" in message
    assert "systemd-resolved is inactive" in message
    assert "upstream Landing Zone DNS addresses could not be determined" in message
    assert "must match one of the detected Landing Zone DNS addresses" in message
    assert "Restore the intended Landing Zone DNS configuration" in message
    assert "rerun ./sync.sh" in message
    assert "Detected Landing Zone DNS addresses" not in message


@pytest.mark.parametrize("matches", [False, True], ids=["mismatch", "match"])
def test_default_dns_sync_console_and_log(tmp_path: Path, matches: bool) -> None:
    """Preserve validation output and failure through the sync.sh tee consumer."""
    result = _run_dns_validation(
        tmp_path,
        "nameserver 192.0.2.53\n",
        "",
        "192.0.2.53" if matches else "203.0.113.53",
        through_tee=True,
    )
    assert result.returncode == (0 if matches else 1), result.stderr
    assert result.stdout == (tmp_path / "validation.log").read_text(encoding="utf-8")
    record = yaml.safe_load(result.stdout)["validations"]["dns"][0]
    assert record["status"] == ("PASS" if matches else "FAIL")
    assert "config/global.yaml defaultDNS:" in record["message"]
    assert "/etc/resolv.conf nameserver entries" in record["message"]
    if not matches:
        assert "rerun ./sync.sh" in record["message"]


@pytest.mark.parametrize("resolved", [False, True], ids=["resolv-conf", "resolved"])
@pytest.mark.parametrize("default_dns", ["198.51.100.53", "203.0.113.53", "192.0.2.53"])
def test_default_dns_redacts_authentication_hostname(
    tmp_path: Path, resolved: bool, default_dns: str
) -> None:
    """Redact display suffixes without making a suffixed raw entry match."""
    result = _run_dns_validation(
        tmp_path,
        "nameserver 127.0.0.53\n"
        if resolved
        else "nameserver 192.0.2.53#resolver.example\nnameserver 198.51.100.53\n",
        "Current DNS Server: 192.0.2.53#resolver.example\n"
        "Current DNS Server: 198.51.100.53\n",
        default_dns,
        through_tee=True,
    )
    matches = default_dns == "198.51.100.53"
    assert result.returncode == (0 if matches else 1), result.stderr
    log = (tmp_path / "validation.log").read_text(encoding="utf-8")
    assert result.stdout == log
    assert "resolver.example" not in result.stdout
    assert "#" not in result.stdout
    record = yaml.safe_load(result.stdout)["validations"]["dns"][0]
    assert record["status"] == ("PASS" if matches else "FAIL")
    message = record["message"]
    assert "resolver.example" not in message
    assert f"config/global.yaml defaultDNS: {default_dns}" in message
    source = (
        "resolvectl status Current DNS Server entries"
        if resolved
        else "/etc/resolv.conf nameserver entries"
    )
    assert f"Detected Landing Zone DNS addresses from {source}:" in message
    assert "\n192.0.2.53\n198.51.100.53\n" in message
    assert "Authentication hostname suffixes are omitted from this display" in message
    assert "Validation compares the original resolver entries, including suffixes" in (
        message
    )
    assert "a displayed address alone may not match defaultDNS" in message
    if not matches:
        assert "rerun ./sync.sh" in message

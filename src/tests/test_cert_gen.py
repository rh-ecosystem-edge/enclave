"""Unit tests for enclave.cert_gen.main."""

import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from enclave.cert_gen.main import (
    CertIssuanceError,
    RootCaExtractionError,
    _fetch_cert,
    _load_credentials,
    cli,
    extract_root_ca,
    issue_cert,
    render_ingress_api_yaml,
    render_ironic_yaml,
    write_hetzner_ini,
)
from tests.cert_helpers import (
    generate_ca,
    generate_intermediate_ca,
    generate_signed_leaf,
)


def _generate_intermediate_with_aia(
    tmp_path: Path, parent_cert_path: Path, parent_key_path: Path, aia_url: str
) -> str:
    key = tmp_path / "inter_aia.key"
    csr = tmp_path / "inter_aia.csr"
    cert = tmp_path / "inter_aia.crt"
    ext = tmp_path / "inter_aia.ext"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(key),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(key),
            "-out",
            str(csr),
            "-subj",
            "/CN=Inter with AIA",
        ],
        check=True,
        capture_output=True,
    )
    ext.write_text(
        "[ext]\nbasicConstraints=CA:TRUE,pathlen:0\nkeyUsage=keyCertSign,cRLSign\n"
        f"authorityInfoAccess=caIssuers;URI:{aia_url}\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(parent_cert_path),
            "-CAkey",
            str(parent_key_path),
            "-out",
            str(cert),
            "-days",
            "1",
            "-set_serial",
            "20",
            "-extfile",
            str(ext),
            "-extensions",
            "ext",
        ],
        check=True,
        capture_output=True,
    )
    return cert.read_text(encoding="utf-8").strip()


def _make_result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> CompletedProcess[str]:
    r: CompletedProcess[str] = CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestWriteHetznerIni:
    def test_writes_token(self, tmp_path: Path) -> None:
        ini = write_hetzner_ini(tmp_path, "mytoken")
        assert ini.read_text(encoding="utf-8") == "dns_hetzner_api_token = mytoken\n"

    def test_permissions(self, tmp_path: Path) -> None:
        ini = write_hetzner_ini(tmp_path, "tok")
        assert oct(ini.stat().st_mode)[-3:] == "600"


class TestExtractRootCa:
    def test_finds_root_in_chain(self, tmp_path: Path) -> None:
        root_pem, root_cert, root_key = generate_ca(tmp_path, "Root CA")
        inter_pem, inter_cert, inter_key = generate_intermediate_ca(
            tmp_path, "Intermediate CA", root_cert, root_key
        )
        leaf_pem = generate_signed_leaf(tmp_path, inter_cert, inter_key, "Leaf")

        chain_path = tmp_path / "chain.pem"
        chain_path.write_text(
            f"{leaf_pem}\n{inter_pem}\n{root_pem}\n", encoding="utf-8"
        )
        result = extract_root_ca(chain_path)
        assert "BEGIN CERTIFICATE" in result
        cert = x509.load_pem_x509_certificate(result.encode())
        assert cert.subject == cert.issuer

    def test_root_last_or_middle(self, tmp_path: Path) -> None:
        root_pem, root_cert, root_key = generate_ca(tmp_path, "Root CA")
        inter_pem, _, _ = generate_intermediate_ca(
            tmp_path, "Intermediate CA", root_cert, root_key
        )
        chain_path = tmp_path / "chain.pem"
        chain_path.write_text(
            f"{inter_pem}\n{root_pem}\n{inter_pem}\n", encoding="utf-8"
        )
        result = extract_root_ca(chain_path)
        cert = x509.load_pem_x509_certificate(result.encode())
        assert cert.subject == cert.issuer

    def test_raises_when_no_root_and_no_aia(self, tmp_path: Path) -> None:
        _, root_cert, root_key = generate_ca(tmp_path, "Root CA")
        inter_pem, inter_cert, inter_key = generate_intermediate_ca(
            tmp_path, "Intermediate CA", root_cert, root_key
        )
        leaf_pem = generate_signed_leaf(tmp_path, inter_cert, inter_key, "Leaf")

        chain_path = tmp_path / "chain.pem"
        chain_path.write_text(f"{leaf_pem}\n{inter_pem}\n", encoding="utf-8")
        with pytest.raises(RootCaExtractionError, match="no AIA extension"):
            extract_root_ca(chain_path)

    def test_aia_walk_finds_root(self, tmp_path: Path) -> None:
        root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")

        aia_url = "http://test.example.com/root.crt"
        inter_pem = _generate_intermediate_with_aia(
            tmp_path, root_cert_path, root_key_path, aia_url
        )

        chain_path = tmp_path / "chain.pem"
        chain_path.write_text(inter_pem + "\n", encoding="utf-8")

        root_cert_obj = x509.load_pem_x509_certificate(root_pem.encode())
        root_der = root_cert_obj.public_bytes(Encoding.DER)
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.raw.read.return_value = root_der

        with patch("enclave.cert_gen.main.requests.get", return_value=mock_resp):
            result = extract_root_ca(chain_path)

        cert = x509.load_pem_x509_certificate(result.encode())
        assert cert.subject == cert.issuer

    def test_aia_walk_exhausted_after_five_hops(self, tmp_path: Path) -> None:
        _, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
        aia_url = "http://test.example.com/inter.crt"
        inter_pem = _generate_intermediate_with_aia(
            tmp_path, root_cert_path, root_key_path, aia_url
        )
        chain_path = tmp_path / "chain.pem"
        chain_path.write_text(inter_pem + "\n", encoding="utf-8")

        # Every fetch returns the same non-self-signed intermediate, so the loop
        # never converges and must raise after exactly 5 hops.
        inter_der = x509.load_pem_x509_certificate(inter_pem.encode()).public_bytes(
            Encoding.DER
        )
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.raw.read.return_value = inter_der

        with (
            patch("enclave.cert_gen.main.requests.get", return_value=mock_resp),
            pytest.raises(RootCaExtractionError, match="after 5 AIA hops"),
        ):
            extract_root_ca(chain_path)


class TestFetchCert:
    def test_invalid_scheme_raises(self) -> None:
        with pytest.raises(
            RootCaExtractionError, match="Unsupported AIA issuer URL scheme"
        ):
            _fetch_cert("ftp://example.com/cert.crt")

    def test_request_exception_raises(self) -> None:
        with (
            patch(
                "enclave.cert_gen.main.requests.get",
                side_effect=requests.RequestException("timeout"),
            ),
            pytest.raises(RootCaExtractionError, match="Failed to fetch AIA issuer"),
        ):
            _fetch_cert("http://example.com/cert.crt")

    def test_unparsable_body_raises(self) -> None:
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.raw.read.return_value = b"not a cert"
        with (
            patch("enclave.cert_gen.main.requests.get", return_value=mock_resp),
            pytest.raises(RootCaExtractionError, match="Cannot parse cert"),
        ):
            _fetch_cert("http://example.com/cert.crt")


class TestLoadCredentials:
    def test_missing_token_raises(self) -> None:
        with (
            patch.dict(
                "os.environ", {"HETZNER_API_TOKEN": "", "ACME_EMAIL": "a@b.com"}
            ),
            pytest.raises(click.UsageError, match="HETZNER_API_TOKEN"),
        ):
            _load_credentials("le-rsa")

    def test_zerossl_missing_eab_raises(self) -> None:
        env = {
            "HETZNER_API_TOKEN": "tok",
            "ACME_EMAIL": "a@b.com",
            "ZEROSSL_EAB_KID": "",
            "ZEROSSL_EAB_HMAC_KEY": "",
        }
        with (
            patch.dict("os.environ", env),
            pytest.raises(click.UsageError, match="ZEROSSL_EAB_KID"),
        ):
            _load_credentials("zerossl-rsa")

    def test_le_returns_empty_eab(self) -> None:
        env = {"HETZNER_API_TOKEN": "tok", "ACME_EMAIL": "a@b.com"}
        with patch.dict("os.environ", env, clear=True):
            result = _load_credentials("le-rsa")
        assert result == ("tok", "a@b.com", "", "")


PEM_CHAIN = "CHAIN\n"
PEM_KEY = "KEY\n"
PEM_ROOT = "ROOT\n"


class TestRenderIngressApiYaml:
    def test_all_four_ssl_keys_present(self) -> None:
        out = render_ingress_api_yaml(PEM_CHAIN, PEM_KEY)
        assert "sslAPICertificateKey: |" in out
        assert "sslAPICertificateFullChain: |" in out
        assert "sslIngressCertificateKey: |" in out
        assert "sslIngressCertificateFullChain: |" in out
        assert "sslCACertificate" not in out

    def test_with_root_ca(self) -> None:
        out = render_ingress_api_yaml(PEM_CHAIN, PEM_KEY, root_ca=PEM_ROOT)
        assert "sslCACertificate: |" in out
        assert "  ROOT" in out

    def test_values_indented(self) -> None:
        out = render_ingress_api_yaml("line1\nline2\n", PEM_KEY)
        assert "  line1\n  line2" in out

    def test_api_and_ingress_share_cert(self) -> None:
        out = render_ingress_api_yaml(PEM_CHAIN, PEM_KEY)
        assert out.count("CHAIN") == 2
        assert out.count("KEY") == 2


class TestRenderIronicYaml:
    def test_ironic_keys_present(self) -> None:
        out = render_ironic_yaml(PEM_CHAIN, PEM_KEY)
        assert "ironicHTTPSCertificate: |" in out
        assert "ironicHTTPSKey: |" in out

    def test_values_indented(self) -> None:
        out = render_ironic_yaml("line1\nline2\n", PEM_KEY)
        assert "  line1\n  line2" in out


class TestIssueCert:
    def test_success(self, tmp_path: Path) -> None:
        ini = tmp_path / "h.ini"
        ini.write_text("", encoding="utf-8")
        with patch("subprocess.run", return_value=_make_result(0)):
            issue_cert(
                tmp_path,
                "https://acme.example.com",
                "rsa",
                "2048",
                "",
                ["example.com"],
                email="test@example.com",
                hetzner_ini=ini,
                eab_kid="",
                eab_hmac_key="",
            )

    def test_ecdsa_uses_elliptic_curve_flag(self, tmp_path: Path) -> None:
        ini = tmp_path / "h.ini"
        ini.write_text("", encoding="utf-8")
        with patch("subprocess.run", return_value=_make_result(0)) as mock_run:
            issue_cert(
                tmp_path,
                "https://acme.example.com",
                "ecdsa",
                "secp384r1",
                "",
                ["example.com"],
                email="test@example.com",
                hetzner_ini=ini,
                eab_kid="",
                eab_hmac_key="",
            )
        args = mock_run.call_args[0][0]
        assert "--elliptic-curve" in args
        assert "secp384r1" in args
        assert "--rsa-key-size" not in args

    def test_preferred_chain_included(self, tmp_path: Path) -> None:
        ini = tmp_path / "h.ini"
        ini.write_text("", encoding="utf-8")
        with patch("subprocess.run", return_value=_make_result(0)) as mock_run:
            issue_cert(
                tmp_path,
                "https://acme.example.com",
                "ecdsa",
                "secp384r1",
                "ISRG Root X2",
                ["example.com"],
                email="test@example.com",
                hetzner_ini=ini,
                eab_kid="",
                eab_hmac_key="",
            )
        args = mock_run.call_args[0][0]
        assert "--preferred-chain" in args
        assert "ISRG Root X2" in args

    def test_eab_credentials_included(self, tmp_path: Path) -> None:
        ini = tmp_path / "h.ini"
        ini.write_text("", encoding="utf-8")
        with patch("subprocess.run", return_value=_make_result(0)) as mock_run:
            issue_cert(
                tmp_path,
                "https://acme.zerossl.com/v2/DV90",
                "rsa",
                "2048",
                "",
                ["example.com"],
                email="test@example.com",
                hetzner_ini=ini,
                eab_kid="kid123",
                eab_hmac_key="hmac456",
            )
        args = mock_run.call_args[0][0]
        # EAB creds must not appear in argv (readable via /proc/<pid>/cmdline).
        assert "--eab-kid" not in args
        assert "kid123" not in args
        assert "--eab-hmac-key" not in args
        assert "hmac456" not in args
        # Instead they are written to a 0600 config file passed via --config.
        eab_ini = tmp_path / "eab.ini"
        assert eab_ini.exists()
        content = eab_ini.read_text(encoding="utf-8")
        assert "eab-kid = kid123" in content
        assert "eab-hmac-key = hmac456" in content
        assert "--config" in args
        assert oct(eab_ini.stat().st_mode)[-3:] == "600"

    def test_rate_limit_error(self, tmp_path: Path) -> None:
        ini = tmp_path / "h.ini"
        ini.write_text("", encoding="utf-8")
        with (
            patch(
                "subprocess.run",
                return_value=_make_result(
                    1, stderr="Error: retry after 2026-08-01T06:00:00Z"
                ),
            ),
            pytest.raises(CertIssuanceError, match="CA rate limit reached"),
        ):
            issue_cert(
                tmp_path,
                "https://acme-v02.api.letsencrypt.org/directory",
                "rsa",
                "2048",
                "",
                ["example.com"],
                email="test@example.com",
                hetzner_ini=ini,
                eab_kid="",
                eab_hmac_key="",
            )

    def test_generic_error(self, tmp_path: Path) -> None:
        ini = tmp_path / "h.ini"
        ini.write_text("", encoding="utf-8")
        with (
            patch(
                "subprocess.run",
                return_value=_make_result(1, stderr="ACME server rejected the request"),
            ),
            pytest.raises(CertIssuanceError, match="Certificate issuance failed"),
        ):
            issue_cert(
                tmp_path,
                "https://acme-v02.api.letsencrypt.org/directory",
                "rsa",
                "2048",
                "",
                ["example.com"],
                email="test@example.com",
                hetzner_ini=ini,
                eab_kid="",
                eab_hmac_key="",
            )


DUMMY_PEM = "-----BEGIN CERTIFICATE-----\nDUMMY\n-----END CERTIFICATE-----\n"


def _make_issue_cert_side_effect(san: str) -> object:
    """Return an issue_cert side_effect that creates a fake certbot live dir."""

    def side_effect(config_dir: Path, *_args: object, **_kwargs: object) -> None:
        live_dir = config_dir / "live" / san
        live_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("fullchain.pem", "privkey.pem", "chain.pem"):
            (live_dir / fname).write_text(DUMMY_PEM, encoding="utf-8")

    return side_effect


class TestCliIngressApi:
    def _env(self) -> dict[str, str]:
        return {
            "HETZNER_API_TOKEN": "tok",
            "ACME_EMAIL": "a@b.com",
        }

    def test_missing_hetzner_token(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ingress-api", "--san", "api.x.nodns.in", "--type", "le-rsa"],
            env={"ACME_EMAIL": "a@b.com", "HETZNER_API_TOKEN": None},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "HETZNER_API_TOKEN" in result.output

    def test_missing_acme_email(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ingress-api", "--san", "api.x.nodns.in", "--type", "le-rsa"],
            env={"HETZNER_API_TOKEN": "tok", "ACME_EMAIL": None},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "ACME_EMAIL" in result.output

    def test_zerossl_missing_eab(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ingress-api", "--san", "api.x.nodns.in", "--type", "zerossl-rsa"],
            env={**self._env(), "ZEROSSL_EAB_KID": None, "ZEROSSL_EAB_HMAC_KEY": None},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "ZEROSSL_EAB_KID" in result.output

    def test_success_le_rsa(self) -> None:
        runner = CliRunner()
        san = "api.smoke.nodns.in"
        with patch(
            "enclave.cert_gen.main.issue_cert",
            side_effect=_make_issue_cert_side_effect(san),
        ):
            result = runner.invoke(
                cli,
                ["ingress-api", "--san", san, "--type", "le-rsa"],
                env=self._env(),
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "sslAPICertificateKey: |" in result.output
        assert "sslIngressCertificateFullChain: |" in result.output

    def test_issue_cert_failure(self) -> None:
        runner = CliRunner()
        with patch(
            "enclave.cert_gen.main.issue_cert",
            side_effect=CertIssuanceError("cert failed"),
        ):
            result = runner.invoke(
                cli,
                ["ingress-api", "--san", "api.x.nodns.in", "--type", "le-rsa"],
                env=self._env(),
                catch_exceptions=False,
            )
        assert result.exit_code != 0
        assert "cert failed" in result.output


class TestCliIronic:
    def _env(self) -> dict[str, str]:
        return {
            "HETZNER_API_TOKEN": "tok",
            "ACME_EMAIL": "a@b.com",
        }

    def test_missing_hetzner_token(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ironic", "--san", "ironic.x.nodns.in", "--type", "le-rsa"],
            env={"ACME_EMAIL": "a@b.com", "HETZNER_API_TOKEN": None},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "HETZNER_API_TOKEN" in result.output

    def test_zerossl_missing_eab(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ironic", "--san", "ironic.x.nodns.in", "--type", "zerossl-rsa"],
            env={**self._env(), "ZEROSSL_EAB_KID": None, "ZEROSSL_EAB_HMAC_KEY": None},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "ZEROSSL_EAB_KID" in result.output

    def test_success_le_rsa(self) -> None:
        runner = CliRunner()
        san = "ironic.smoke.nodns.in"
        with patch(
            "enclave.cert_gen.main.issue_cert",
            side_effect=_make_issue_cert_side_effect(san),
        ):
            result = runner.invoke(
                cli,
                ["ironic", "--san", san, "--type", "le-rsa"],
                env=self._env(),
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "ironicHTTPSCertificate: |" in result.output
        assert "ironicHTTPSKey: |" in result.output

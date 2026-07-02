from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tests.cert_helpers import generate_ca, generate_signed_leaf
from tests.fixtures import OcResultFactory


@pytest.fixture
def leaf_pem() -> str:
    """Syntactic fake PEM string representing a leaf certificate."""
    return """-----BEGIN CERTIFICATE-----
leaf
-----END CERTIFICATE-----"""


@pytest.fixture
def intermediate_pem() -> str:
    """Syntactic fake PEM string representing an intermediate CA certificate."""
    return """-----BEGIN CERTIFICATE-----
intermediate
-----END CERTIFICATE-----"""


@pytest.fixture
def root_pem() -> str:
    """Syntactic fake PEM string representing a root CA certificate."""
    return """-----BEGIN CERTIFICATE-----
root
-----END CERTIFICATE-----"""


@pytest.fixture
def router_ca_pem() -> str:
    """Syntactic fake PEM string representing an OpenShift router CA certificate."""
    return """-----BEGIN CERTIFICATE-----
router-ca
-----END CERTIFICATE-----"""


@pytest.fixture
def ca_pem() -> str:
    """Syntactic fake PEM string representing a generic CA certificate."""
    return """-----BEGIN CERTIFICATE-----
ca
-----END CERTIFICATE-----"""


@pytest.fixture
def chain_pem() -> str:
    """Syntactic fake PEM string representing a certificate chain entry."""
    return """-----BEGIN CERTIFICATE-----
chain
-----END CERTIFICATE-----"""


@pytest.fixture(scope="module")
def module_ca(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path, Path]:
    """Self-signed CA generated once per module: (cert_pem, cert_path, key_path)."""
    return generate_ca(tmp_path_factory.mktemp("ca"), "Module CA")


@pytest.fixture(scope="module")
def module_ca_pem(module_ca: tuple[str, Path, Path]) -> str:
    """PEM text of the module-scoped self-signed CA certificate."""
    return module_ca[0]


@pytest.fixture(scope="module")
def module_leaf_pem(
    tmp_path_factory: pytest.TempPathFactory, module_ca: tuple[str, Path, Path]
) -> str:
    """PEM text of a leaf certificate signed by module_ca (issuer ≠ subject)."""
    _, ca_cert_path, ca_key_path = module_ca
    return generate_signed_leaf(
        tmp_path_factory.mktemp("leaf"), ca_cert_path, ca_key_path, "Leaf"
    )


@pytest.fixture
def oc_result() -> OcResultFactory:
    def _make(
        stdout: str = "", returncode: int = 0, stderr: str = ""
    ) -> CompletedProcess[str]:
        result: CompletedProcess[str] = CompletedProcess(args=[], returncode=returncode)
        result.stdout = stdout
        result.stderr = stderr
        return result

    return _make

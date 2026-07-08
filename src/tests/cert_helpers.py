"""Shared openssl helper functions for certificate generation in tests."""

import subprocess
from pathlib import Path


def generate_ca(tmp_path: Path, cn: str) -> tuple[str, Path, Path]:
    """Generate a self-signed CA. Returns (cert_pem, cert_path, key_path)."""
    key_path = tmp_path / f"{cn}.key"
    cert_path = tmp_path / f"{cn}.crt"
    # Generate a P-256 EC private key and a self-signed X.509 certificate in one
    # step. -x509 produces a cert instead of a CSR; -nodes skips key encryption.
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            "1",
            "-nodes",
            "-subj",
            f"/CN={cn}",
        ],
        check=True,
        capture_output=True,
    )
    return cert_path.read_text(encoding="utf-8").strip(), cert_path, key_path


def generate_signed_leaf(
    tmp_path: Path, ca_cert_path: Path, ca_key_path: Path, cn: str
) -> str:
    """Generate a cert signed by the given CA. Returns cert_pem (stripped)."""
    leaf_key = tmp_path / "leaf.key"
    # Generate a P-256 EC private key for the leaf certificate.
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(leaf_key),
        ],
        check=True,
        capture_output=True,
    )
    leaf_csr = tmp_path / "leaf.csr"
    # Create a Certificate Signing Request (CSR) using the leaf key.
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(leaf_key),
            "-out",
            str(leaf_csr),
            "-subj",
            f"/CN={cn}",
        ],
        check=True,
        capture_output=True,
    )
    leaf_cert = tmp_path / "leaf.crt"
    # Sign the CSR with the CA's certificate and key, producing the leaf cert.
    # -set_serial supplies a fixed serial number (required when not using a CA database).
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_cert_path),
            "-CAkey",
            str(ca_key_path),
            "-out",
            str(leaf_cert),
            "-days",
            "1",
            "-set_serial",
            "01",
        ],
        check=True,
        capture_output=True,
    )
    return leaf_cert.read_text(encoding="utf-8").strip()


def generate_self_issued_not_self_signed_cert(tmp_path: Path) -> str:
    """Generate a cert that is self-issued (issuer==subject) but NOT self-signed.

    The cert has subject=CN=Cross CA and issuer=CN=Cross CA because the signing CA
    was created with the same subject DN. However, the public key embedded in the cert
    belongs to a different keypair, so the self-signature check fails.
    """
    signing_key = tmp_path / "signing.key"
    signing_cert = tmp_path / "signing.crt"
    # Create the signing CA with CN=Cross CA. Its key will be used to sign the
    # subject cert, making that cert's issuer field read "CN=Cross CA".
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-keyout",
            str(signing_key),
            "-out",
            str(signing_cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=Cross CA",
        ],
        check=True,
        capture_output=True,
    )
    subject_key = tmp_path / "subject.key"
    # Generate a separate, independent key for the subject cert. This is the
    # key pair that will be embedded in the cross cert's SubjectPublicKeyInfo —
    # distinct from the signing CA's key despite the identical subject DN.
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(subject_key),
        ],
        check=True,
        capture_output=True,
    )
    subject_csr = tmp_path / "subject.csr"
    # Create a CSR for the subject cert. Using the same DN (/CN=Cross CA) means
    # issuer and subject will match in the final cert, satisfying the self-issued
    # condition while the public key belongs to a different keypair.
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(subject_key),
            "-out",
            str(subject_csr),
            "-subj",
            "/CN=Cross CA",
        ],
        check=True,
        capture_output=True,
    )
    cross_cert = tmp_path / "cross.crt"
    # Sign the CSR with the signing CA's key. The result is a cert whose
    # issuer == subject == CN=Cross CA but whose signature was made by a
    # different private key, so -check_ss_sig verification will fail.
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(subject_csr),
            "-CA",
            str(signing_cert),
            "-CAkey",
            str(signing_key),
            "-out",
            str(cross_cert),
            "-days",
            "1",
            "-set_serial",
            "01",
        ],
        check=True,
        capture_output=True,
    )
    return cross_cert.read_text(encoding="utf-8").strip()

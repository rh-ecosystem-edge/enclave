"""Release image Sigstore signatures in the mirrors and the 4.21 Sigstore admin gate."""

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from enclave.utils import log_subprocess_output, run_oc_command

RELEASE_SOURCE = "quay.io/openshift-release-dev/ocp-release"
MIN_SIGNED_MINOR = (4, 20)
SIGSTORE_GATE = "ack-4.20-sigstore-in-4.21"
SKOPEO_TIMEOUT_SECONDS = 120
RELEASE_TAG_PATTERN = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.\d+(?:-[0-9A-Za-z.]+)?"
    r"-(?:x86_64|aarch64|ppc64le|s390x|multi)$"
)

logger = logging.getLogger(__name__)


class ReleaseSignaturesError(Exception):
    """Base exception for release signature operations."""


class ReleaseSignaturesQueryError(ReleaseSignaturesError):
    """Raised when a cluster or registry query fails."""


@dataclass(frozen=True)
class MissingSignature:
    """A release image tag in a mirror without its Sigstore signature tag."""

    mirror: str
    tag: str
    digest: str


class MissingReleaseSignaturesError(ReleaseSignaturesError):
    """Raised when release images in a mirror lack their Sigstore signature tags."""

    def __init__(self, missing: list[MissingSignature]) -> None:
        """Record every mirror/tag/digest without a signature tag."""
        self.missing = missing
        lines = "\n".join(
            f"  - {m.mirror}:{m.tag} ({m.digest}): "
            f"missing tag {signature_tag(m.digest)}"
            for m in missing
        )
        super().__init__(
            "Release images are missing their Sigstore signatures in the mirror "
            f"registries; OpenShift 4.21 and later can't verify them:\n{lines}"
        )


def signature_tag(digest: str) -> str:
    """Return the cosign signature tag for a manifest digest (sha256:x -> sha256-x.sig)."""
    return digest.replace(":", "-", 1) + ".sig"


def _oc_json(args: list[str], what: str) -> dict[str, Any]:
    """Run an oc command with JSON output and return the parsed object."""
    result = run_oc_command(args)
    if result.returncode != 0:
        header = f"oc get {what} failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        raise ReleaseSignaturesQueryError(header)
    try:
        parsed: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseSignaturesQueryError(
            f"oc get {what} returned invalid JSON"
        ) from exc
    return parsed


def get_release_mirrors() -> list[str]:
    """Return the mirrors configured for release images in ImageDigestMirrorSets."""
    idms = _oc_json(
        ["oc", "get", "imagedigestmirrorset.config.openshift.io", "-o", "json"],
        "imagedigestmirrorset",
    )
    mirrors: list[str] = []
    for item in idms.get("items") or []:
        for entry in item.get("spec", {}).get("imageDigestMirrors") or []:
            if entry.get("source") != RELEASE_SOURCE:
                continue
            for mirror in entry.get("mirrors") or []:
                if mirror not in mirrors:
                    mirrors.append(mirror)
    logger.debug("Release image mirrors: %s", mirrors)
    return mirrors


@contextmanager
def pull_secret_authfile() -> Generator[str]:
    """Yield the path of a private temp file holding the cluster pull secret."""
    secret = _oc_json(
        ["oc", "-n", "openshift-config", "get", "secret", "pull-secret", "-o", "json"],
        "secret openshift-config/pull-secret",
    )
    encoded = secret.get("data", {}).get(".dockerconfigjson")
    if not encoded:
        raise ReleaseSignaturesQueryError(
            "secret openshift-config/pull-secret has no .dockerconfigjson"
        )
    fd, path = tempfile.mkstemp(prefix="enclave-pull-secret-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64decode(encoded))
        yield path
    finally:
        Path(path).unlink()


def _run_skopeo(args: list[str]) -> str:
    """Run skopeo and return its stdout, raising on failure or timeout."""
    command = ["skopeo", *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SKOPEO_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReleaseSignaturesQueryError(
            f"skopeo timed out after {SKOPEO_TIMEOUT_SECONDS}s: {' '.join(command)}"
        ) from exc
    if result.returncode != 0:
        header = f"skopeo {args[0]} failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        raise ReleaseSignaturesQueryError(f"{header}: {args[-1]}")
    return result.stdout


def list_tags(mirror: str, authfile: str) -> list[str]:
    """Return all tags of a mirror repository."""
    stdout = _run_skopeo([
        "list-tags",
        "--authfile",
        authfile,
        "--tls-verify=false",
        f"docker://{mirror}",
    ])
    try:
        tags = json.loads(stdout).get("Tags") or []
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ReleaseSignaturesQueryError(
            f"skopeo list-tags returned invalid JSON for {mirror}"
        ) from exc
    return [str(tag) for tag in tags]


def manifest_digest(mirror: str, tag: str, authfile: str) -> str:
    """Return the manifest digest of mirror:tag."""
    digest = _run_skopeo([
        "inspect",
        "--authfile",
        authfile,
        "--tls-verify=false",
        "--format",
        "{{.Digest}}",
        f"docker://{mirror}:{tag}",
    ]).strip()
    if not digest.startswith("sha256:"):
        raise ReleaseSignaturesQueryError(
            f"Unexpected digest '{digest}' for {mirror}:{tag}"
        )
    return digest


def signed_release_tags(tags: list[str]) -> list[str]:
    """Return the release image tags whose version requires a Sigstore signature."""
    selected = []
    for tag in tags:
        match = RELEASE_TAG_PATTERN.match(tag)
        if match is None:
            continue
        if (int(match.group("major")), int(match.group("minor"))) >= MIN_SIGNED_MINOR:
            selected.append(tag)
    return selected


def find_missing_signatures(
    mirrors: list[str], authfile: str
) -> tuple[int, list[MissingSignature]]:
    """Return how many release tags were checked and those lacking a signature tag."""
    checked = 0
    missing = []
    for mirror in mirrors:
        tags = list_tags(mirror, authfile)
        tag_set = set(tags)
        for tag in signed_release_tags(tags):
            checked += 1
            digest = manifest_digest(mirror, tag, authfile)
            if signature_tag(digest) in tag_set:
                logger.info("Signature present: %s:%s (%s)", mirror, tag, digest)
            else:
                missing.append(MissingSignature(mirror, tag, digest))
    return checked, missing


def ack_sigstore_gate(dry_run: bool) -> None:
    """Acknowledge the 4.20 -> 4.21 Sigstore admin gate if the cluster defines it."""
    gates = _oc_json(
        [
            "oc",
            "-n",
            "openshift-config-managed",
            "get",
            "configmap",
            "admin-gates",
            "-o",
            "json",
        ],
        "configmap openshift-config-managed/admin-gates",
    )
    if SIGSTORE_GATE not in (gates.get("data") or {}):
        logger.info(
            "Admin gate %s is not defined; nothing to acknowledge", SIGSTORE_GATE
        )
        return

    acks = _oc_json(
        [
            "oc",
            "-n",
            "openshift-config",
            "get",
            "configmap",
            "admin-acks",
            "-o",
            "json",
        ],
        "configmap openshift-config/admin-acks",
    )
    if (acks.get("data") or {}).get(SIGSTORE_GATE) == "true":
        logger.info("Admin gate %s is already acknowledged", SIGSTORE_GATE)
        return

    if dry_run:
        logger.info("DRY-RUN: would acknowledge admin gate %s", SIGSTORE_GATE)
        return

    patch = json.dumps({"data": {SIGSTORE_GATE: "true"}})
    result = run_oc_command([
        "oc",
        "-n",
        "openshift-config",
        "patch",
        "configmap",
        "admin-acks",
        "--type=merge",
        "-p",
        patch,
    ])
    if result.returncode != 0:
        header = f"oc patch configmap admin-acks failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        raise ReleaseSignaturesQueryError(header)
    logger.info("Acknowledged admin gate %s", SIGSTORE_GATE)


def reconcile(dry_run: bool) -> None:
    """Check release signatures in every release mirror, then ack the Sigstore gate.

    Every release image tag at 4.20 or later in each mirror must have its
    sha256-<digest>.sig tag. If any is missing, raises MissingReleaseSignaturesError and
    does not acknowledge the gate. If no such tag exists in any mirror, the gate is not
    acknowledged either. In dry-run mode, nothing on the cluster is changed.
    """
    mirrors = get_release_mirrors()
    if not mirrors:
        logger.warning(
            "No ImageDigestMirrorSet mirrors for %s; nothing to check", RELEASE_SOURCE
        )
        return

    with pull_secret_authfile() as authfile:
        checked, missing = find_missing_signatures(mirrors, authfile)
    if missing:
        raise MissingReleaseSignaturesError(missing)
    if checked == 0:
        logger.warning(
            "No release image tags at %d.%d or later in %s; not acknowledging %s",
            *MIN_SIGNED_MINOR,
            ", ".join(mirrors),
            SIGSTORE_GATE,
        )
        return

    ack_sigstore_gate(dry_run)

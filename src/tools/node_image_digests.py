"""Collect PinnedImageSet-format digest refs from crictl images JSON on a node."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

OC_DEBUG_CRICTL_TIMEOUT_SECONDS = 600

# FQDN-style registry host (PinnedImageSet API).
_OCI_NAME = re.compile(
    r"^([a-zA-Z0-9-]+\.)+[a-zA-Z0-9-]+(:[0-9]{2,5})?/[a-zA-Z0-9-_.]+(/[a-zA-Z0-9-_.]+)*$"
)
_DIGEST_REF = re.compile(r"^(.+)@(sha256:[a-fA-F0-9]{64})$")

# Substring filters for digest refs to skip when building a PinnedImageSet list.
# When crictl lists multiple repoDigests per image id, MCO prefetch runs
# `podman manifest inspect`; these defaults avoid images that break or are not
# worth pinning: openshift-pipelines/*, Cincinnati graph-data (/openshift/graph-image),
# upstream OLM catalog indexes under /redhat/*-operator-index, disconnected mirrors of
# those catalogs (mirror-redhat-operators, mirror-redhat-operators-<plugin>), and
# /rhel9/support-tools (single-manifest refs that can cause prefetch exit 125).
DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS: tuple[str, ...] = (
    "openshift-pipelines/",
    "/openshift/graph-image",
    "/redhat/community-operator-index",
    "/redhat/certified-operator-index",
    "/redhat/redhat-marketplace-index",
    "/redhat/redhat-operator-index",
    "mirror-redhat-operators",
    "/rhel9/support-tools",
)

_REPO_DIGEST_KEYS = ("repoDigests", "repo_digests")
_REPO_TAG_KEYS = ("repoTags", "repo_tags")


@dataclass(frozen=True)
class CollectResult:
    """Digest refs extracted from a node plus the raw oc debug/crictl output."""

    refs: list[str]
    raw_output: str


def parse_exclude_contains(raw: str | None) -> list[str]:
    """Parse ``--exclude-contains`` JSON or return default substring filters."""
    if raw is None or not str(raw).strip():
        return list(DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"invalid --exclude-contains JSON: {raw!r}"
        raise ValueError(msg) from exc
    if not isinstance(parsed, list):
        msg = (
            f"--exclude-contains must be a JSON array, got {type(parsed).__name__}: "
            f"{raw!r}"
        )
        raise TypeError(msg)
    return [str(s) for s in parsed]


def normalize_digest_ref(ref: str | None) -> str | None:
    """Return a normalized digest pull spec, or None if the ref is invalid."""
    ref = (ref or "").strip()
    if not ref or ref == "<none>":
        return None
    match = _DIGEST_REF.match(ref)
    if not match:
        return None
    name, digest = match.group(1), match.group(2)
    digest = "sha256:" + digest.split(":", 1)[1].lower()
    candidate = f"{name}@{digest}"
    if _OCI_NAME.match(name):
        return candidate
    logger.debug("Skipping digest ref with unsupported image name: %s", name)
    return None


def is_excluded(ref: str, exclude_contains: list[str]) -> bool:
    """Return True when ``ref`` contains any of the configured exclude substrings."""
    return any(s and str(s) in ref for s in exclude_contains)


def _parse_crictl_images_list(text: str) -> list[dict]:
    match = re.search(r"\[\s*\{", text)
    if not match:
        return []
    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(text[match.start() :])
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("images") or data.get("Images") or []
    if not isinstance(data, list):
        return []
    return [img for img in data if isinstance(img, dict)]


def _refs_from_field(img: dict, keys: tuple[str, ...]) -> list[str]:
    refs: list[str] = []
    for key in keys:
        for repo_ref in img.get(key) or []:
            out = normalize_digest_ref(repo_ref)
            if out:
                refs.append(out)
    return refs


def _dedupe_ordered(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _select_repo_digests(rd_vals: list[str]) -> list[str]:
    if not rd_vals:
        return []
    # crictl can report multiple repoDigests for one image ID (same image mirrored under
    # different names). Keep only the last digest to avoid pinning redundant aliases.
    return rd_vals if len(rd_vals) == 1 else [rd_vals[-1]]


def _digest_refs_from_image(
    img: dict,
    excludes: list[str],
    seen: set[str],
) -> list[str]:
    refs: list[str] = []

    def emit(out: str) -> None:
        if is_excluded(out, excludes) or out in seen:
            return
        seen.add(out)
        refs.append(out)

    raw_rd = _refs_from_field(img, _REPO_DIGEST_KEYS)
    rd_vals = _dedupe_ordered(raw_rd)
    had_digest_fields = bool(img.get("repoDigests") or img.get("repo_digests"))
    for out in _select_repo_digests(rd_vals):
        emit(out)
    if had_digest_fields and rd_vals:
        return refs
    for out in _refs_from_field(img, _REPO_TAG_KEYS):
        emit(out)
    return refs


def extract_digest_refs_from_crictl_output(
    text: str,
    exclude_contains: list[str] | None = None,
) -> list[str]:
    """Extract unique digest pull specs from ``crictl images -o json`` output.

    Tolerates leading noise from ``oc debug``. Uses ``exclude_contains`` when
    provided, otherwise ``DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS``.
    """
    excludes = (
        list(DEFAULT_PINNED_IMAGE_EXCLUDE_CONTAINS)
        if exclude_contains is None
        else exclude_contains
    )
    seen: set[str] = set()
    refs: list[str] = []
    for img in _parse_crictl_images_list(text):
        refs.extend(_digest_refs_from_image(img, excludes, seen))
    return refs


def run_oc_debug_crictl_images(*, node: str, oc: str) -> str:
    """Run ``oc debug`` on a node and return combined stdout/stderr from ``crictl images``.

    Raises ``TimeoutError`` when the command exceeds ``OC_DEBUG_CRICTL_TIMEOUT_SECONDS``.
    """
    try:
        result = subprocess.run(
            [
                oc,
                "debug",
                f"node/{node}",
                "-n",
                "default",
                "--quiet",
                "--",
                "chroot",
                "/host",
                "/usr/bin/crictl",
                "-r",
                "unix:///var/run/crio/crio.sock",
                "images",
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=OC_DEBUG_CRICTL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        logger.exception(
            "oc debug on node/%s timed out after %d seconds",
            node,
            OC_DEBUG_CRICTL_TIMEOUT_SECONDS,
        )
        msg = (
            f"oc debug on node/{node} timed out after "
            f"{OC_DEBUG_CRICTL_TIMEOUT_SECONDS} seconds"
        )
        raise TimeoutError(msg) from exc
    return (result.stdout or "") + (result.stderr or "")


def collect_node_image_digests(
    node: str,
    *,
    oc: str = "oc",
    exclude_contains: list[str] | None = None,
) -> CollectResult:
    """Collect PinnedImageSet-format digest refs from container images on a node."""
    raw_output = run_oc_debug_crictl_images(node=node, oc=oc)
    refs = extract_digest_refs_from_crictl_output(raw_output, exclude_contains)
    logger.debug(
        "node=%s collected %d digest ref(s) from %d byte(s) of crictl output",
        node,
        len(refs),
        len(raw_output),
    )
    return CollectResult(refs=refs, raw_output=raw_output)


def emit_collection_output(
    result: CollectResult, *, raw_output_file: str | None = None
) -> None:
    """Write digest refs to stdout; on empty collection, persist or stream raw output.

    When ``raw_output_file`` is set and no refs were collected, writes full crictl
    output to that path and prints a short message to stderr instead of dumping it.
    """
    if not result.refs:
        if raw_output_file:
            output_path = Path(raw_output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(result.raw_output, encoding="utf-8")
            sys.stderr.write(
                f"No digest refs were collected; raw output saved to {output_path}\n"
            )
        else:
            sys.stderr.write(result.raw_output)
    for ref in result.refs:
        sys.stdout.write(f"{ref}\n")


def main(
    node: str,
    *,
    oc: str = "oc",
    exclude_contains_raw: str | None = None,
    raw_output_file: str | None = None,
) -> None:
    """CLI entry point: collect node image digests and emit stdout/stderr output."""
    excludes = parse_exclude_contains(exclude_contains_raw)
    result = collect_node_image_digests(node, oc=oc, exclude_contains=excludes)
    emit_collection_output(result, raw_output_file=raw_output_file)

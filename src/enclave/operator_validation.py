"""Validate operator settings using the reconciliation consumer contracts."""

import re

from enclave.utils import semver_key


def validate_operator_settings(
    version: str | None = None, channel: str | None = None
) -> None:
    """Validate supplied fields, retaining optional channels and partial overrides."""
    if version is not None:
        try:
            semver_key(version.replace("+", "-"))
        except ValueError as exc:
            raise ValueError(f"Invalid operator version {version!r}: {exc}") from exc
    if (
        channel is not None
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", channel) is None
    ):
        raise ValueError(f"Invalid operator channel {channel!r}")

"""Add a plugin to the ``enabled_plugins`` list in the enclave global config.

The edit is done with ruamel.yaml in round-trip mode so comments, quoting and the
rest of the file shape are preserved. A plugin that is already present is a no-op.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError


class AddEnabledPluginError(RuntimeError):
    """Raised when the config file cannot be read, parsed or updated."""


def _build_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def add_enabled_plugin(config_path: Path, plugin: str) -> bool:
    """Add ``plugin`` to ``enabled_plugins`` in ``config_path``.

    The key is created when it is absent (or only present as a comment). Returns
    ``True`` when the file was modified, ``False`` when the plugin was already
    listed. Raises :class:`AddEnabledPluginError` on any read/parse/write failure.
    """
    yaml = _build_yaml()
    # Resolve symlinks so we edit the real target in place rather than replacing
    # the link with a regular file.
    target = config_path.resolve()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise AddEnabledPluginError(f"cannot read {config_path}: {exc}") from exc

    try:
        data: Any = yaml.load(text)
    except YAMLError as exc:
        # Do not interpolate the exception: it embeds a snippet of the file, which
        # may contain sensitive data. Report only the location.
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        location = (
            f" at line {mark.line + 1}, column {mark.column + 1}"
            if mark is not None
            else ""
        )
        raise AddEnabledPluginError(
            f"cannot parse {config_path}: invalid YAML{location}"
        ) from exc

    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise AddEnabledPluginError(
            f"{config_path}: expected a YAML mapping, got {type(data).__name__}"
        )

    enabled = data.get("enabled_plugins")
    if enabled is None:
        enabled = CommentedSeq()
        data["enabled_plugins"] = enabled
    if not isinstance(enabled, list):
        raise AddEnabledPluginError(
            f"{config_path}: enabled_plugins must be a list, "
            f"got {type(enabled).__name__}"
        )

    if plugin in enabled:
        return False

    enabled.append(plugin)

    # Write atomically: dump to a temporary file in the same directory and
    # replace the original only once the dump succeeds, so a mid-write failure
    # cannot truncate the existing config.
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            yaml.dump(data, handle)
        shutil.copymode(target, tmp_path)
        tmp_path.replace(target)
    except OSError as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise AddEnabledPluginError(f"cannot write {config_path}: {exc}") from exc
    return True

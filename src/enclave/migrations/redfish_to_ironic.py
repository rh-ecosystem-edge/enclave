"""Move the Redfish settings of agent_hosts to the ironic boot plugin configuration.

The redfish, redfishUser, redfishPassword and bmcSystemId fields of each
agent_hosts entry in config/global.yaml move to hosts.<name> of
config/plugins/boot/ironic.yaml as address, username, password and systemId.

Edits keep comments and formatting, trying in order:
1. deleting the lines of the fields from global.yaml, located with ruamel.yaml,
   so that nothing else in the file changes
2. a ruamel.yaml round-trip edit, which keeps comments but may normalize some
   formatting (e.g. spacing in flow mappings)
3. rewriting the file from its data, which loses comments
Each edit is accepted only if reloading it with PyYAML gives the expected data.
Each file is written atomically after a backup of the original is kept, and
ironic.yaml is written before global.yaml so that an interrupted run can be
retried.
"""

import copy
import io
import itertools
import logging
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark, YAMLError as RuamelYAMLError
from ruamel.yaml.tokens import CommentToken
from ruamel.yaml.util import load_yaml_guess_indent

logger = logging.getLogger(__name__)

BMC_FIELDS = {
    "redfish": "address",
    "redfishUser": "username",
    "redfishPassword": "password",
    "bmcSystemId": "systemId",
}

IRONIC_HEADER = (
    "---\n"
    "# ironic boot plugin configuration (plugins/boot/ironic).\n"
    "# BMC settings moved from agent_hosts in config/global.yaml.\n"
)

# Index of the comment token in ruamel's comment attribute (ca.items):
# mapping keys keep their trailing comments at 2, sequence items at 0.
_MAP_COMMENT_SLOT = 2
_SEQ_COMMENT_SLOT = 0
_MAPPING_PARENT_LINE = re.compile(r"^(\s*)[^\s#\-][^:]*:\s*(#.*)?$")
_MAPPING_KEY_LINE = re.compile(r"^(\s*)[^\s#\-][^:]*:(\s|$)")
_NEW_FILE_MODE = 0o600
_NEW_DIR_MODE = 0o750


class MigrationError(Exception):
    pass


class RoundTripError(Exception):
    pass


@dataclass
class MigrationResult:
    hosts: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)
    rewritten: list[Path] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.written)


def _load_data(path: Path, text: str) -> object:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MigrationError(f"{path}: cannot be parsed as YAML: {exc}") from exc


def _bmc_entries(global_path: Path, global_data: dict[str, Any]) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for host in global_data.get("agent_hosts") or []:
        if not isinstance(host, dict) or not any(f in host for f in BMC_FIELDS):
            continue
        name = host.get("name")
        if not isinstance(name, str) or not name:
            raise MigrationError(
                f"{global_path}: an agent_hosts entry with Redfish settings has no name"
            )
        entries[name] = {BMC_FIELDS[f]: host[f] for f in BMC_FIELDS if f in host}
    return entries


def _without_bmc_fields(global_data: dict[str, Any]) -> dict[str, Any]:
    expected = copy.deepcopy(global_data)
    for host in expected.get("agent_hosts") or []:
        if isinstance(host, dict):
            for bmc_field in BMC_FIELDS:
                host.pop(bmc_field, None)
    return expected


def _guess_mapping_indent(text: str) -> int:
    """Return the indentation of the first block mapping nested in a mapping."""
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "---"))
    ]
    for line, following in itertools.pairwise(lines):
        parent = _MAPPING_PARENT_LINE.match(line)
        child = _MAPPING_KEY_LINE.match(following)
        if parent and child and len(child.group(1)) > len(parent.group(1)):
            return len(child.group(1)) - len(parent.group(1))
    return 2


def _round_trip_yaml(text: str) -> YAML:
    # ruamel guesses the sequence style (e.g. "  - name:" is indent 4, offset 2);
    # block mappings may use another indentation, guessed separately.
    _, sequence_indent, block_seq_indent = load_yaml_guess_indent(text)
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    rt.indent(
        mapping=_guess_mapping_indent(text),
        sequence=sequence_indent or 2,
        offset=block_seq_indent or 0,
    )
    rt.explicit_start = bool(re.search(r"^---\s*$", text, re.MULTILINE))
    return rt


def _dump(rt: YAML, data: object) -> str:
    stream = io.StringIO()
    rt.dump(data, stream)
    return stream.getvalue()


def _last_comment_holder(node: CommentedMap, key: Any) -> tuple[Any, Any, int]:  # ruff: ignore[any-type]
    """Return the container, key and slot holding the comments after node[key]."""
    container: Any = node
    slot = _MAP_COMMENT_SLOT
    while True:
        value = container[key]
        if isinstance(value, CommentedMap) and len(value) > 0:
            container, key, slot = value, list(value.keys())[-1], _MAP_COMMENT_SLOT
        elif isinstance(value, CommentedSeq) and len(value) > 0:
            container, key, slot = value, len(value) - 1, _SEQ_COMMENT_SLOT
        else:
            return container, key, slot


def _append_comment(node: CommentedMap, key: Any, text: str) -> None:  # ruff: ignore[any-type]
    container, holder_key, slot = _last_comment_holder(node, key)
    entry = container.ca.items.setdefault(holder_key, [None, None, None, None])
    token = entry[slot]
    if token is None:
        entry[slot] = CommentToken("\n" + text, CommentMark(0), None)
    else:
        token.value += text


def _delete_keeping_comments(node: CommentedMap, key: str) -> None:
    """Delete node[key], keeping the comment lines that follow it."""
    keys = list(node.keys())
    index = keys.index(key)
    entry = node.ca.items.pop(key, None)
    del node[key]
    token = entry[_MAP_COMMENT_SLOT] if entry else None
    if token is None:
        return
    # The token holds the key's end-of-line comment (or nothing) up to the first
    # newline, then the full-line comments and blank lines until the next key.
    following = token.value.partition("\n")[2]
    if not following:
        return
    if index == 0:
        raise RoundTripError(
            f"cannot keep the comments after {key}, the first key of its entry"
        )
    _append_comment(node, keys[index - 1], following)


def _value_line_count(lines: list[str], line: int, col: int) -> int:
    """Return the number of lines of the key at (line, col) and its value."""
    value_start = lines[line][col:].partition(":")[2].strip()
    block_scalar = value_start[:1] in {"|", ">"}
    count, pending_blanks = 1, 0
    for following in lines[line + 1 :]:
        stripped = following.strip()
        if not stripped:
            if not block_scalar:
                break
            pending_blanks += 1
            continue
        indent = len(following) - len(following.lstrip())
        if indent <= col or (stripped.startswith("#") and not block_scalar):
            break
        count += pending_blanks + 1
        pending_blanks = 0
    return count


def _remove_bmc_fields_by_lines(text: str) -> str:
    data = YAML().load(text)
    lines = text.splitlines(keepends=True)
    removed: set[int] = set()
    for host in data.get("agent_hosts") or []:
        if not isinstance(host, CommentedMap):
            continue
        keys = list(host.keys())
        for bmc_field in BMC_FIELDS:
            if bmc_field in host:
                line, col = host.lc.key(bmc_field)
                removed.update(range(line, line + _value_line_count(lines, line, col)))
        if keys[0] in BMC_FIELDS:
            _move_item_indicator(host, keys, lines)
    return "".join(text for i, text in enumerate(lines) if i not in removed)


def _move_item_indicator(host: CommentedMap, keys: list[str], lines: list[str]) -> None:
    """Move the "- " that starts a sequence item to its first remaining key."""
    first_line, first_col = host.lc.key(keys[0])
    remaining = [key for key in keys if key not in BMC_FIELDS]
    if not remaining:
        raise RoundTripError("an agent_hosts entry has only Redfish settings")
    line, col = host.lc.key(remaining[0])
    if col != first_col or lines[line][:col].strip() or line == first_line:
        raise RoundTripError(f"cannot move the start of the entry to {remaining[0]}")
    lines[line] = lines[first_line][:first_col] + lines[line][col:]


def _remove_bmc_fields_round_trip(text: str) -> str:
    rt = _round_trip_yaml(text)
    data = rt.load(text)
    for host in data.get("agent_hosts") or []:
        if isinstance(host, CommentedMap):
            for bmc_field in BMC_FIELDS:
                if bmc_field in host:
                    _delete_keeping_comments(host, bmc_field)
    return _dump(rt, data)


def _add_hosts_round_trip(text: str, entries: dict[str, Any]) -> str:
    rt = _round_trip_yaml(text)
    data = rt.load(text)
    if data is None:
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise RoundTripError("not a YAML mapping")
    hosts = data.get("hosts")
    if hosts is None:
        hosts = data["hosts"] = CommentedMap()
    if not isinstance(hosts, CommentedMap):
        raise RoundTripError("hosts is not a YAML mapping")
    for name, entry in entries.items():
        if name not in hosts:
            hosts[name] = CommentedMap(entry)
    return _dump(rt, data)


def _plain_dump(data: object, header: str = "---\n") -> str:
    return header + yaml.safe_dump(
        data, default_flow_style=False, sort_keys=False, width=4096
    )


def _render(
    path: Path,
    expected: object,
    edits: list[Callable[[], str]],
    result: MigrationResult,
) -> tuple[str, bool]:
    """Return the first edit that yields the expected data, else a rewrite.

    The returned flag is True for the first edit, which changes nothing else.
    """
    for index, edit in enumerate(edits):
        try:
            text = edit()
            loaded = yaml.safe_load(text)
        except (RoundTripError, RuamelYAMLError, yaml.YAMLError) as exc:
            logger.debug("%s: edit %d not used: %s", path, index + 1, exc)
            continue
        if loaded != expected:
            logger.debug("%s: edit %d does not hold the expected data", path, index + 1)
            continue
        return text, index == 0
    logger.warning(
        "%s: cannot be edited keeping comments; rewriting it from its data. "
        "Comments and formatting are kept only in the backup",
        path,
    )
    result.rewritten.append(path)
    return _plain_dump(expected), False


def _write_fd(fd: int, text: str) -> None:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def _write(path: Path, text: str, result: MigrationResult) -> None:
    """Write text to path atomically, keeping a backup of the original."""
    if path.exists():
        backup = path.with_name(
            f"{path.name}.{os.getpid()}.{time.strftime('%Y-%m-%d@%H:%M:%S')}~"
        )
        shutil.copy2(path, backup)
        result.backups.append(backup)
        mode = stat.S_IMODE(path.stat().st_mode)
    else:
        path.parent.mkdir(mode=_NEW_DIR_MODE, parents=True, exist_ok=True)
        mode = _NEW_FILE_MODE
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp = Path(tmp_name)
    try:
        _write_fd(fd, text)
        tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    result.written.append(path)


def _validate_ironic(schema_path: Path, data: dict[str, Any]) -> None:
    try:
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MigrationError(f"cannot read {schema_path}: {exc}") from exc
    errors = sorted(Draft7Validator(schema).iter_errors(data), key=str)
    if errors:
        details = "; ".join(
            "/".join(str(p) for p in e.absolute_path) + f": {e.message}"
            if e.absolute_path
            else e.message
            for e in errors
        )
        raise MigrationError(
            f"the migrated ironic boot plugin configuration is not valid: {details}"
        )


def _read_ironic(ironic_path: Path) -> tuple[str | None, dict[str, Any]]:
    text = ironic_path.read_text(encoding="utf-8") if ironic_path.exists() else None
    data = (_load_data(ironic_path, text) if text is not None else None) or {}
    if not isinstance(data, dict):
        raise MigrationError(f"{ironic_path}: expected a YAML mapping")
    if not isinstance(data.get("hosts") or {}, dict):
        raise MigrationError(f"{ironic_path}: hosts must be a YAML mapping")
    return text, data


def _update_ironic(
    ironic_path: Path,
    ironic_text: str | None,
    new_data: dict[str, Any],
    added: dict[str, Any],
    result: MigrationResult,
) -> None:
    if ironic_text is None:
        new_text = _plain_dump(new_data, IRONIC_HEADER)
    else:
        text = ironic_text
        new_text, _ = _render(
            ironic_path, new_data, [lambda: _add_hosts_round_trip(text, added)], result
        )
    _write(ironic_path, new_text, result)
    logger.info("Added the BMC settings of %s to %s", ", ".join(added), ironic_path)


def _update_global(
    global_path: Path,
    global_text: str,
    expected: dict[str, Any],
    removed: dict[str, Any],
    result: MigrationResult,
) -> None:
    new_text, exact = _render(
        global_path,
        expected,
        [
            lambda: _remove_bmc_fields_by_lines(global_text),
            lambda: _remove_bmc_fields_round_trip(global_text),
        ],
        result,
    )
    _write(global_path, new_text, result)
    # Field values are never logged: they include passwords, and other edits
    # may also change lines holding unrelated secrets (e.g. pullSecret).
    if exact:
        for name, entry in removed.items():
            logger.info(
                "%s: removed the lines of %s from agent_hosts entry %s",
                global_path,
                ", ".join(f for f, key in BMC_FIELDS.items() if key in entry),
                name,
            )
    elif global_path not in result.rewritten:
        logger.warning(
            "%s: formatting changed beyond the migrated settings; compare it with the "
            "backup %s",
            global_path,
            result.backups[-1],
        )


def migrate(global_path: Path, ironic_path: Path, schema_path: Path) -> MigrationResult:
    """Move the Redfish settings of agent_hosts from global_path to ironic_path."""
    result = MigrationResult()
    try:
        global_text = global_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MigrationError(f"cannot read {global_path}: {exc}") from exc
    global_data = _load_data(global_path, global_text)
    if not isinstance(global_data, dict):
        raise MigrationError(f"{global_path}: expected a YAML mapping")

    entries = _bmc_entries(global_path, global_data)
    if not entries:
        logger.info("No Redfish settings in agent_hosts of %s", global_path)
        return result
    result.hosts = list(entries)

    ironic_text, ironic_data = _read_ironic(ironic_path)
    existing_hosts = ironic_data.get("hosts") or {}
    conflicts = [
        name
        for name, entry in entries.items()
        if name in existing_hosts and existing_hosts[name] != entry
    ]
    if conflicts:
        raise MigrationError(
            f"{ironic_path} already has BMC settings for {', '.join(conflicts)} "
            f"that differ from the Redfish settings in {global_path}. Remove one of "
            "them and run the upgrade again; nothing was changed."
        )

    new_ironic_data = dict(ironic_data)
    new_ironic_data["hosts"] = {**existing_hosts, **entries}
    _validate_ironic(schema_path, new_ironic_data)

    added = {name: e for name, e in entries.items() if name not in existing_hosts}
    if new_ironic_data != ironic_data:
        _update_ironic(ironic_path, ironic_text, new_ironic_data, added, result)
    else:
        logger.info("%s already has the BMC settings of all hosts", ironic_path)
    _update_global(
        global_path, global_text, _without_bmc_fields(global_data), entries, result
    )

    logger.info(
        "Moved the Redfish settings of %s from %s to %s",
        ", ".join(result.hosts),
        global_path,
        ironic_path,
    )
    for backup in result.backups:
        logger.info("Backup: %s", backup)
    return result

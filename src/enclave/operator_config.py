"""Load and validate core operator overrides without affecting plugin descriptors."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

from enclave.operator_validation import validate_operator_settings


class OperatorConfigError(ValueError):
    """Invalid or unreadable core operator configuration."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently keeping the last value."""

    def get_single_data(self) -> object:
        """Dispose parser resources even when validation of YAML keys fails."""
        try:
            return super().get_single_data()
        finally:
            self.dispose()


def _unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
) -> dict[str, object]:
    loader.flatten_mapping(node)
    result: dict[str, object] = {}
    for key, value in loader.construct_pairs(node, deep=True):
        if not isinstance(key, str):
            raise OperatorConfigError("YAML mapping keys must be strings")
        if key in result:
            raise OperatorConfigError(f"Duplicate YAML key: {key!r}")
        result[key] = value
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_mapping,
)


def _read_yaml(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as stream:
            return _UniqueKeyLoader(stream).get_single_data()
    except (OSError, UnicodeError, yaml.YAMLError, OperatorConfigError) as exc:
        raise OperatorConfigError(f"Cannot load {path}: {exc}") from exc


def _validate(data: object, schema: dict[str, Any], source: Path) -> None:
    try:
        Draft7Validator(schema).validate(data)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        raise OperatorConfigError(
            f"Invalid operators in {source} at {location or '<root>'}: {exc.message}",
        ) from exc
    for entry in cast("dict[str, Any]", data)["operators"]:
        try:
            validate_operator_settings(entry.get("version"), entry.get("channel"))
        except ValueError as exc:
            raise OperatorConfigError(
                f"{source}: operator {entry['name']!r}: {exc}",
            ) from exc


def _index_operators(
    data: dict[str, Any],
    source: Path,
) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for entry in data["operators"]:
        name = entry["name"]
        if name in entries:
            raise OperatorConfigError(f"Duplicate operator {name!r} in {source}")
        entries[name] = entry
    return entries


def load_operators(
    defaults_file: Path,
    config_file: Path,
    schema_file: Path,
) -> dict[str, Any]:
    """Merge supplied fields by name, validate raw and effective config, retain order.

    Only a missing config file or ``operators: []`` is a no-op. Null/empty documents,
    unknown names/fields, duplicate names/keys and malformed values are errors.
    Lists supplied as fields replace that field, not the operator list itself.
    """
    schema = cast("dict[str, Any]", _read_yaml(schema_file))
    raw_defaults = _read_yaml(defaults_file)
    _validate(raw_defaults, schema, defaults_file)
    defaults = cast("dict[str, Any]", raw_defaults)
    default_entries = _index_operators(defaults, defaults_file)
    try:
        config_file.lstat()
    except FileNotFoundError:
        return deepcopy(defaults)
    except OSError as exc:
        raise OperatorConfigError(f"Cannot access {config_file}: {exc}") from exc
    raw_overrides = _read_yaml(config_file)
    override_schema = {
        "$ref": "#/definitions/overrides",
        "definitions": schema["definitions"],
    }
    _validate(raw_overrides, override_schema, config_file)
    overrides = cast("dict[str, Any]", raw_overrides)
    override_entries = _index_operators(overrides, config_file)
    unknown = override_entries.keys() - default_entries.keys()
    if unknown:
        raise OperatorConfigError(
            f"Unknown core operator names in {config_file}: {', '.join(sorted(unknown))}",
        )
    effective = {
        "operators": [
            deepcopy(entry | override_entries.get(name, {}))
            for name, entry in default_entries.items()
        ]
    }
    _validate(effective, schema, config_file)
    return effective

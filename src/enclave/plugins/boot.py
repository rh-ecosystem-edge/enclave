"""Boot plugin contract validation."""

from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator, SchemaError, ValidationError
from jsonschema.validators import validator_for
from referencing.exceptions import Unresolvable

HOOKS = frozenset({
    "validate.yaml",
    "setup.yaml",
    "boot.yaml",
    "post-install.yaml",
    "cleanup.yaml",
})
# Plugin-internal task files, included by the hooks and never by the runner.
LIB_DIR = "lib"
SCHEMA_FILES = frozenset({"config.yaml", "defaults.yaml"})
TOP_LEVEL_FILES = frozenset({"plugin.yaml", "defaults.yaml"})
TOP_LEVEL_DIRS = frozenset({"schemas", "tasks"})


def descriptor_schema_path() -> Path:
    # Installed: site-packages/enclave/plugins/boot.py → site-packages/enclave/schemas/
    # Editable:  src/enclave/plugins/boot.py → src/enclave/ (no schemas/) → repo_root/schemas/
    enclave_pkg = Path(__file__).resolve().parent.parent
    path = enclave_pkg / "schemas" / "boot-plugin.yaml"
    if not path.exists():
        path = enclave_pkg.parent.parent / "schemas" / "boot-plugin.yaml"
    return path


def _load_yaml(path: Path, errors: list[str]) -> tuple[bool, Any]:
    try:
        return True, yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        errors.append(f"{path.name}: cannot be read as YAML: {exc}")
        return False, None


def _check_layout(plugin_dir: Path, errors: list[str]) -> None:
    if plugin_dir.parent.name != "boot" or plugin_dir.parent.parent.name != "plugins":
        errors.append("the plugin directory must be under plugins/boot/")
    for entry in sorted(plugin_dir.iterdir()):
        if entry.is_dir() and entry.name in TOP_LEVEL_DIRS:
            continue
        if entry.is_file() and entry.name in TOP_LEVEL_FILES:
            continue
        errors.append(f"unexpected entry: {entry.name}")


def _check_descriptor(plugin_dir: Path, errors: list[str]) -> None:
    descriptor_path = plugin_dir / "plugin.yaml"
    if not descriptor_path.is_file():
        errors.append("missing plugin.yaml")
        return
    ok, descriptor = _load_yaml(descriptor_path, errors)
    if not ok:
        return
    schema = yaml.safe_load(descriptor_schema_path().read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    schema_errors = sorted(validator.iter_errors(descriptor), key=str)
    errors.extend(
        f"plugin.yaml: {_error_location(e)}{e.message}" for e in schema_errors
    )
    if isinstance(descriptor, dict) and descriptor.get("name") != plugin_dir.name:
        errors.append(
            f"plugin.yaml: name {descriptor.get('name')!r} must match the "
            f"directory name {plugin_dir.name!r}"
        )


def _check_task_list(path: Path, display: str, errors: list[str]) -> None:
    ok, tasks = _load_yaml(path, errors)
    if ok and (
        not isinstance(tasks, list) or not all(isinstance(t, dict) for t in tasks)
    ):
        errors.append(f"{display}: must be a YAML list of tasks")


def _check_lib(lib_dir: Path, errors: list[str]) -> None:
    for entry in sorted(lib_dir.iterdir()):
        display = f"tasks/{LIB_DIR}/{entry.name}"
        if not entry.is_file() or entry.suffix != ".yaml":
            errors.append(f"{display}: only YAML task files are allowed in tasks/lib/")
            continue
        _check_task_list(entry, display, errors)


def _check_tasks(plugin_dir: Path, errors: list[str]) -> None:
    tasks_dir = plugin_dir / "tasks"
    if not (tasks_dir / "boot.yaml").is_file():
        errors.append("missing tasks/boot.yaml")
    if not tasks_dir.is_dir():
        return
    for entry in sorted(tasks_dir.iterdir()):
        if entry.is_dir() and entry.name == LIB_DIR:
            _check_lib(entry, errors)
            continue
        if not entry.is_file() or entry.name not in HOOKS:
            errors.append(
                f"tasks/{entry.name}: not a boot plugin hook "
                f"(allowed: {', '.join(sorted(HOOKS))}, and {LIB_DIR}/)"
            )
            continue
        _check_task_list(entry, f"tasks/{entry.name}", errors)


def _load_schema(path: Path, errors: list[str]) -> dict[str, Any] | None:
    ok, schema = _load_yaml(path, errors)
    if not ok:
        return None
    if not isinstance(schema, dict):
        errors.append(f"schemas/{path.name}: must be a YAML mapping")
        return None
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        errors.append(f"schemas/{path.name}: not a valid JSON Schema: {exc.message}")
        return None
    return schema


def _check_schemas(plugin_dir: Path, errors: list[str]) -> None:
    schemas_dir = plugin_dir / "schemas"
    schemas: dict[str, dict[str, Any] | None] = {}
    if schemas_dir.is_dir():
        for entry in sorted(schemas_dir.iterdir()):
            if not entry.is_file() or entry.name not in SCHEMA_FILES:
                errors.append(
                    f"schemas/{entry.name}: unexpected entry "
                    f"(allowed: {', '.join(sorted(SCHEMA_FILES))})"
                )
                continue
            schemas[entry.name] = _load_schema(entry, errors)

    defaults_path = plugin_dir / "defaults.yaml"
    has_defaults = defaults_path.is_file()
    has_defaults_schema = "defaults.yaml" in schemas
    if has_defaults != has_defaults_schema:
        errors.append(
            "defaults.yaml and schemas/defaults.yaml must both exist or both be absent"
        )
        return
    defaults_schema = schemas.get("defaults.yaml")
    if not has_defaults or defaults_schema is None:
        return
    ok, defaults = _load_yaml(defaults_path, errors)
    if not ok:
        return
    validator_cls = validator_for(defaults_schema)
    validator = validator_cls(defaults_schema)
    try:
        defaults_errors = sorted(validator.iter_errors(defaults), key=str)
    except Unresolvable as exc:
        errors.append(f"schemas/defaults.yaml: unresolvable reference: {exc}")
        return
    errors.extend(
        f"defaults.yaml: {_error_location(e)}{e.message}" for e in defaults_errors
    )


def _error_location(error: ValidationError) -> str:
    if not error.absolute_path:
        return ""
    return "/".join(str(p) for p in error.absolute_path) + ": "


def validate_boot_plugin(plugin_dir: Path) -> list[str]:
    """Return the boot plugin contract violations of plugin_dir (empty if valid)."""
    plugin_dir = plugin_dir.resolve()
    if not plugin_dir.is_dir():
        return [f"{plugin_dir} is not a directory"]
    errors: list[str] = []
    _check_layout(plugin_dir, errors)
    _check_descriptor(plugin_dir, errors)
    _check_tasks(plugin_dir, errors)
    _check_schemas(plugin_dir, errors)
    return errors

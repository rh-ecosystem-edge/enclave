#!/bin/bash
# Plugin Validation Script
# Validates that all plugins under plugins/ have correct structure and valid YAML
#
# Checks per plugin:
#   1. plugin.yaml exists and is a valid YAML mapping with required fields (name, type)
#   2. plugin.yaml name field matches the plugin directory name (enforces uniqueness)
#   3. Plugin cannot have both defaults.yaml and a defaults: field in plugin.yaml
#   4. Top-level properties in schemas/config.yaml and schemas/defaults.yaml must be
#      prefixed with the plugin name (e.g. lvms → lvms*, vast-csi → vastCsi*/vast_csi*/vastCSI*)
#   5. Task files under tasks/ are valid YAML task lists (if present)
#   6. No unexpected files outside plugin.yaml and tasks/ directory
#
# Full field validation (types, enums, operator structure) is handled by JSON Schema
# in playbooks/tasks/schema_validation.yaml using schemas/plugin.yaml.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

PLUGINS_DIR="${ENCLAVE_DIR}/plugins"
FAILED=0

if [ ! -d "$PLUGINS_DIR" ]; then
    echo "No plugins directory found, skipping plugin validation"
    exit 0
fi

# Count plugins
PLUGIN_COUNT=0
for plugin_dir in "$PLUGINS_DIR"/*/; do
    [ -d "$plugin_dir" ] || continue
    PLUGIN_COUNT=$((PLUGIN_COUNT + 1))
done

if [ "$PLUGIN_COUNT" -eq 0 ]; then
    echo "No plugins found, skipping plugin validation"
    exit 0
fi

echo "Validating $PLUGIN_COUNT plugin(s)..."
echo ""

# Helper: validate a YAML file is a list of task mappings
validate_yaml_tasklist() {
    python3 -c "
import yaml, sys
try:
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f)
except Exception as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
if not isinstance(data, list):
    print(f'Expected YAML list of tasks, got {type(data).__name__}', file=sys.stderr)
    sys.exit(1)
for i, item in enumerate(data):
    if not isinstance(item, dict):
        print(f'Task {i} is not a mapping', file=sys.stderr)
        sys.exit(1)
" "$1"
}

for plugin_dir in "$PLUGINS_DIR"/*/; do
    [ -d "$plugin_dir" ] || continue
    plugin_name=$(basename "$plugin_dir")
    plugin_failed=0
    echo "--- Plugin: $plugin_name ---"

    # 1. Check plugin.yaml exists and has required fields
    plugin_yaml="${plugin_dir}plugin.yaml"
    if [ ! -f "$plugin_yaml" ]; then
        error "  Missing plugin.yaml"
        FAILED=1
        continue
    fi

    if ! python3 -c "
import yaml, sys, os

filepath = sys.argv[1]
dir_name = sys.argv[2]
required_fields = ['name', 'type']
valid_fields = ['name', 'type', 'order', 'catalog', 'operators', 'defaults',
                'installOperators', 'registries', 'additionalImages', 'blockedImages',
                'requires', 'helm', 'clusterSelector']

try:
    with open(filepath) as f:
        data = yaml.safe_load(f)
except Exception as e:
    print(f'  plugin.yaml is not valid YAML: {e}', file=sys.stderr)
    sys.exit(1)

if not isinstance(data, dict):
    print('  plugin.yaml must be a YAML mapping', file=sys.stderr)
    sys.exit(1)

missing = [f for f in required_fields if f not in data]
if missing:
    print(f'  plugin.yaml missing required fields: {missing}', file=sys.stderr)
    sys.exit(1)

if data['name'] != dir_name:
    print(f'  plugin.yaml name \"{data[\"name\"]}\" must match directory name \"{dir_name}\"', file=sys.stderr)
    sys.exit(1)

unexpected = [f for f in data if f not in valid_fields]
if unexpected:
    print(f'  plugin.yaml has unexpected fields: {unexpected}', file=sys.stderr)
    sys.exit(1)
" "$plugin_yaml" "$plugin_name" 2>&1; then
        FAILED=1
        plugin_failed=1
    fi

    # 2. Check defaults.yaml and defaults: field are mutually exclusive
    if [ -f "${plugin_dir}defaults.yaml" ] && python3 -c "
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
sys.exit(0 if 'defaults' in data else 1)
" "$plugin_yaml" 2>/dev/null; then
        error "  Has both defaults.yaml and a defaults: field in plugin.yaml. Only one is allowed."
        FAILED=1
        plugin_failed=1
    fi

    # 3. Check top-level schema property names are prefixed with the plugin name
    if [ -d "${plugin_dir}schemas" ]; then
        for schema_file in "${plugin_dir}schemas/config.yaml" "${plugin_dir}schemas/defaults.yaml"; do
            [ -f "$schema_file" ] || continue
            if ! python3 -c "
import re, yaml, sys

def valid_prefixes(name):
    segs = re.split(r'[^a-zA-Z0-9]+', name)
    segs = [s for s in segs if s]
    if len(segs) == 1:
        return [segs[0].lower()]
    first = segs[0].lower()
    rest = segs[1:]
    return [
        '_'.join(s.lower() for s in segs),
        first + ''.join(s.capitalize() for s in rest),
        first + ''.join(s.upper() for s in rest),
    ]

plugin_name = sys.argv[1]
schema_file = sys.argv[2]
with open(schema_file) as f:
    schema = yaml.safe_load(f)
props = list(schema.get('properties', {}).keys())
prefixes = valid_prefixes(plugin_name)
bad = [p for p in props if not any(p.startswith(px) for px in prefixes)]
if bad:
    print(f'  {schema_file}: properties {bad} must start with one of {prefixes}', file=sys.stderr)
    sys.exit(1)
" "$plugin_name" "$schema_file" 2>&1; then
                FAILED=1
                plugin_failed=1
            fi
        done
    fi

    # 4. Validate task files under tasks/ are valid task lists
    if [ -d "${plugin_dir}tasks" ]; then
        for task_file in "${plugin_dir}"tasks/*.yaml; do
            [ -f "$task_file" ] || continue
            if ! validate_yaml_tasklist "$task_file" 2>/dev/null; then
                error "  tasks/$(basename "$task_file") must be a YAML list of tasks"
                FAILED=1
                plugin_failed=1
            fi
        done
    fi

    # 5. Check for unexpected files/directories (only plugin.yaml, defaults.yaml, tasks/, schemas/ allowed)
    for entry in "${plugin_dir}"*; do
        entry_name=$(basename "$entry")
        if [ "$entry_name" != "plugin.yaml" ] && [ "$entry_name" != "defaults.yaml" ] && \
           [ "$entry_name" != "tasks" ] && [ "$entry_name" != "files" ] && \
           [ "$entry_name" != "charts" ] && [ "$entry_name" != "templates" ] && \
           [ "$entry_name" != "schemas" ] && [ "$entry_name" != "test-fixtures" ]; then
            error "  Unexpected file or directory: $entry_name (only plugin.yaml, defaults.yaml, tasks/, files/, charts/, templates/, schemas/ and test-fixtures/ are allowed)"
            FAILED=1
            plugin_failed=1
        fi
    done

    if [ $plugin_failed -eq 0 ]; then
        echo "  OK"
    fi
done

echo ""
if [ $FAILED -eq 0 ]; then
    success "All plugins validated successfully"
    exit 0
else
    error "Plugin validation failed"
    exit 1
fi

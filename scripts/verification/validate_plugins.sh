#!/bin/bash
# Plugin Validation Script
# Validates that all plugins under plugins/ have correct structure and valid YAML
#
# Checks per plugin:
#   1. plugin.yaml exists and contains required fields (name, type, order, mirror, operators)
#   2. config/defaults.yaml is valid YAML (if present)
#   3. pre-validate.yaml, deploy.yaml, post-validate.yaml are valid YAML (if present)
#   4. operators/operators.yaml is valid YAML with plugin_operators key (if operators: true)
#   5. mirror/ directory exists (if mirror: true)

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

# Helper: validate a YAML file is syntactically valid
validate_yaml_file() {
    python3 -c "
import yaml, sys
try:
    with open(sys.argv[1]) as f:
        yaml.safe_load(f)
except Exception as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
" "$1"
}

# Helper: validate a YAML file is a mapping (dict)
validate_yaml_mapping() {
    python3 -c "
import yaml, sys
try:
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f)
except Exception as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
if not isinstance(data, dict):
    print(f'Expected YAML mapping, got {type(data).__name__}', file=sys.stderr)
    sys.exit(1)
" "$1"
}

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

    # 1. Check plugin.yaml exists
    plugin_yaml="${plugin_dir}plugin.yaml"
    if [ ! -f "$plugin_yaml" ]; then
        error "  Missing plugin.yaml"
        FAILED=1
        continue
    fi

    # Validate plugin.yaml is valid YAML and has required fields
    if ! python3 -c "
import yaml, sys

filepath = sys.argv[1]
required_fields = ['name', 'type', 'order', 'mirror', 'operators']

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
" "$plugin_yaml" 2>&1; then
        FAILED=1
        plugin_failed=1
    fi

    # 2. Validate config/defaults.yaml is a mapping if present
    defaults_yaml="${plugin_dir}config/defaults.yaml"
    if [ -f "$defaults_yaml" ]; then
        if ! validate_yaml_mapping "$defaults_yaml" 2>/dev/null; then
            error "  config/defaults.yaml must be a YAML mapping"
            FAILED=1
            plugin_failed=1
        fi
    fi

    # 3. Validate lifecycle YAML files are task lists if present
    for lifecycle_file in pre-validate.yaml deploy.yaml post-validate.yaml; do
        filepath="${plugin_dir}${lifecycle_file}"
        if [ -f "$filepath" ]; then
            if ! validate_yaml_tasklist "$filepath" 2>/dev/null; then
                error "  ${lifecycle_file} must be a YAML list of tasks"
                FAILED=1
                plugin_failed=1
            fi
        fi
    done

    # 4. If operators: true, check operators/operators.yaml
    operators_enabled=$(python3 -c "
import yaml, sys
try:
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict):
        print(str(data.get('operators', False)).lower())
    else:
        print('false')
except Exception:
    print('false')
" "$plugin_yaml" 2>/dev/null)

    if [ "$operators_enabled" = "true" ]; then
        operators_yaml="${plugin_dir}operators/operators.yaml"
        if [ ! -f "$operators_yaml" ]; then
            error "  operators: true but operators/operators.yaml is missing"
            FAILED=1
            plugin_failed=1
        else
            if ! python3 -c "
import yaml, sys

filepath = sys.argv[1]
required_keys = ['name', 'channel']

try:
    with open(filepath) as f:
        data = yaml.safe_load(f)
except Exception as e:
    print(f'  operators/operators.yaml is not valid YAML: {e}', file=sys.stderr)
    sys.exit(1)

if not isinstance(data, dict) or 'plugin_operators' not in data:
    print('  operators/operators.yaml must contain plugin_operators key', file=sys.stderr)
    sys.exit(1)

ops = data['plugin_operators']
if not isinstance(ops, list):
    print('  plugin_operators must be a list', file=sys.stderr)
    sys.exit(1)

for i, op in enumerate(ops):
    if not isinstance(op, dict):
        print(f'  plugin_operators[{i}] is not a mapping', file=sys.stderr)
        sys.exit(1)
    missing = [k for k in required_keys if k not in op]
    if missing:
        print(f'  plugin_operators[{i}] missing keys: {missing}', file=sys.stderr)
        sys.exit(1)
" "$operators_yaml" 2>&1; then
                FAILED=1
                plugin_failed=1
            fi
        fi
    fi

    # 5. If mirror: true, check mirror/ directory exists
    mirror_enabled=$(python3 -c "
import yaml, sys
try:
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict):
        print(str(data.get('mirror', False)).lower())
    else:
        print('false')
except Exception:
    print('false')
" "$plugin_yaml" 2>/dev/null)

    if [ "$mirror_enabled" = "true" ]; then
        mirror_dir="${plugin_dir}mirror/"
        if [ ! -d "$mirror_dir" ]; then
            error "  mirror: true but mirror/ directory is missing"
            FAILED=1
            plugin_failed=1
        fi
    fi

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

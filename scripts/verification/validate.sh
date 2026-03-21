#!/bin/bash
# Enclave Lab Validation Script
# Validates code quality (shell scripts, YAML, Ansible playbooks, Makefile)

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

# Custom helper functions for this script
print_header() {
    echo -e "${GREEN}━━━ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
    echo ""
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
    echo ""
}

print_info() {
    echo -e "${YELLOW}ℹ️  $1${NC}"
    echo ""
}

# Validation functions
validate_shell() {
    print_header "Validating shell scripts with shellcheck"

    if find scripts/ -name "*.sh" -type f 2>/dev/null | grep -q .; then
        if find scripts/ -name "*.sh" -type f -print0 | xargs -0 shellcheck -x -S warning; then
            print_success "Shell script validation passed"
            return 0
        else
            print_error "Shell script validation failed"
            return 1
        fi
    else
        print_info "No shell scripts found to validate"
        return 0
    fi
}

validate_yaml() {
    print_header "Validating YAML files with yamllint"

    if yamllint -c .yamllint.yml .; then
        print_success "YAML validation passed"
        return 0
    else
        print_error "YAML validation failed"
        return 1
    fi
}

validate_json_schema() {
    print_header "Validating JSON schema"

    if ansible-playbook playbooks/validate-schema.yaml -e@config/global.example.yaml; then
        print_success "JSON schema validation passed"
        return 0
    else
        print_error "JSON schema validation failed"
        return 1
    fi
}

validate_ansible() {
    print_header "Validating Ansible playbooks with ansible-lint"

    # Check if playbooks directory exists or any playbook files exist
    local has_playbooks=0
    if [ -d playbooks ]; then
        has_playbooks=1
    else
        # Check for YAML files that are playbooks (not vars files)
        for file in *.yaml *.yml; do
            if [ -f "$file" ] && [[ ! "$file" =~ ^vars ]]; then
                has_playbooks=1
                break
            fi
        done
    fi

    if [ $has_playbooks -eq 1 ]; then
        if ansible-lint; then
            print_success "Ansible playbook validation passed"
            return 0
        else
            print_error "Ansible playbook validation failed"
            return 1
        fi
    else
        print_info "No Ansible playbooks found to validate"
        return 0
    fi
}

validate_tags() {
    print_header "Validating Ansible playbook tags"

    if [ ! -d playbooks ]; then
        print_info "No playbooks directory found, skipping tag validation"
        return 0
    fi

    # Create temporary config/global.yaml if it doesn't exist
    local cleanup_vars=0
    if [ ! -f config/global.yaml ]; then
        touch config/global.yaml
        cleanup_vars=1
    fi

    local failed=0

    # Define tag -> expected task mapping
    # Format: "playbook:tag:expected_task_name"
    local tag_tests=(
        "playbooks/01-prepare.yaml:download-content:Download content"
        "playbooks/01-prepare.yaml:download-control-binaries:Download control binaries"
        "playbooks/02-mirror.yaml:mirror-registry:Include tasks for mirror-registry"
        "playbooks/02-mirror.yaml:mirror-plugins:Collect plugin operators for imageset"
        "playbooks/03-deploy.yaml:pre-install-validate:Pre-install validate plugins"
        "playbooks/03-deploy.yaml:configure-abi:Include tasks for OCP ABI"
        "playbooks/03-deploy.yaml:hardware:Configure and boot hosts via Ironic"
        "playbooks/03-deploy.yaml:wait-deployment:Wait for deployment"
        "playbooks/04-post-install.yaml:post-install-config:Post-install configurations"
        "playbooks/05-operators.yaml:operators:Configure operators"
        "playbooks/05-operators.yaml:operators:Auto-discover and deploy foundation plugins"
        "playbooks/05-operators.yaml:foundation-plugins:Auto-discover and deploy foundation plugins"
        "playbooks/06-day2.yaml:clair-disconnected:Configure Clair in disconnected environments"
        "playbooks/06-day2.yaml:acm-policy-catalogsources:Mirrored catalogsource configuration ACM policy"
        "playbooks/06-day2.yaml:model-config:Model configurations"
        "playbooks/validate-schema.yaml:schema-validation:Include schema validation tasks"
    )

    for test in "${tag_tests[@]}"; do
        local playbook
        local tag
        local expected_task
        local output

        playbook=$(echo "$test" | cut -d: -f1)
        tag=$(echo "$test" | cut -d: -f2)
        expected_task=$(echo "$test" | cut -d: -f3-)

        if [ ! -f "$playbook" ]; then
            print_error "Playbook not found: $playbook"
            failed=1
            continue
        fi

        # Run ansible-playbook --list-tasks with the tag and check if expected task appears
        output=$(ansible-playbook "$playbook" -e workingDir=/tmp --tags "$tag" --list-tasks 2>&1)

        if echo "$output" | grep -q "$expected_task"; then
            : # Tag works, task found
        else
            print_error "Tag '$tag' in $playbook does not include task '$expected_task'"
            failed=1
        fi
    done

    # Cleanup temporary config/global.yaml
    if [ $cleanup_vars -eq 1 ]; then
        rm -f config/global.yaml
    fi

    if [ $failed -eq 0 ]; then
        print_success "Ansible playbook tags validation passed"
        return 0
    else
        print_error "Ansible playbook tags validation failed"
        return 1
    fi
}

validate_makefile() {
    print_header "Validating Makefile syntax"

    if make -n help >/dev/null 2>&1; then
        print_success "Makefile syntax is valid"
        return 0
    else
        print_error "Makefile syntax validation failed"
        return 1
    fi
}

validate_plugins() {
    print_header "Validating plugin directory structure"

    if "${ENCLAVE_DIR}/scripts/verification/validate_plugins.sh"; then
        print_success "Plugin validation passed"
        return 0
    else
        print_error "Plugin validation failed"
        return 1
    fi
}

# Main function
validate_all() {
    local failed=0

    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║  Enclave Lab Code Quality Validation      ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""

    validate_shell || failed=1
    validate_yaml || failed=1
    validate_json_schema || failed=1
    validate_ansible || failed=1
    validate_tags || failed=1
    validate_makefile || failed=1
    validate_plugins || failed=1

    if [ $failed -eq 0 ]; then
        echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║  ✅ All validation checks passed!       ║${NC}"
        echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
        echo ""
        return 0
    else
        echo -e "${RED}╔════════════════════════════════════════╗${NC}"
        echo -e "${RED}║  ❌ Some validation checks failed       ║${NC}"
        echo -e "${RED}╚════════════════════════════════════════╝${NC}"
        echo ""
        return 1
    fi
}

# Parse command line arguments
case "${1:-all}" in
    shell)
        validate_shell
        ;;
    yaml)
        validate_yaml
        ;;
    json-schema)
        validate_json_schema
        ;;
    ansible)
        validate_ansible
        ;;
    tags)
        validate_tags
        ;;
    makefile)
        validate_makefile
        ;;
    plugins)
        validate_plugins
        ;;
    all)
        validate_all
        ;;
    *)
        echo "Usage: $0 {all|shell|yaml|json-schema|ansible|tags|makefile|plugins}"
        exit 1
        ;;
esac

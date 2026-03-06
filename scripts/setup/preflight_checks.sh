#!/bin/bash
# Pre-flight checks for CI workflows
#
# Validates environment variables and system resources before workflow execution.
# Supports both local and GitHub Actions execution.
#
# Usage:
#   ./preflight_checks.sh [OPTIONS]
#
# Options:
#   --title TITLE               - Title for the checks section (default: "Pre-flight Checks")
#   --check-pull-secret         - Verify PULL_SECRET is set
#   --check-system-resources    - Check available RAM and disk space
#   --check-libvirt             - Verify libvirt access
#   --deployment-mode MODE      - Display deployment mode in output
#
# Environment Variables:
#   DEV_SCRIPTS_PATH - Path to dev-scripts installation (required)
#   WORKING_DIR - Cluster working directory (required)
#   PULL_SECRET - OpenShift pull secret (required if --check-pull-secret)

set +e  # Don't exit on first error, collect all failures

# Colors for terminal output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# Detect GitHub Actions environment
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    USE_GITHUB=true
else
    USE_GITHUB=false
fi

# Output helper that works both locally and in CI
output() {
    local msg="$1"
    echo -e "$msg"
    if [ "$USE_GITHUB" = true ]; then
        # Strip ANSI color codes for GitHub summary
        echo "$msg" | sed 's/\x1b\[[0-9;]*m//g' >> "$GITHUB_STEP_SUMMARY"
    fi
}

# Parse command-line arguments
TITLE="Pre-flight Checks"
CHECK_PULL_SECRET=false
CHECK_SYSTEM_RESOURCES=false
CHECK_LIBVIRT=false
DEPLOYMENT_MODE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --title)
            TITLE="$2"
            shift 2
            ;;
        --check-pull-secret)
            CHECK_PULL_SECRET=true
            shift
            ;;
        --check-system-resources)
            CHECK_SYSTEM_RESOURCES=true
            shift
            ;;
        --check-libvirt)
            CHECK_LIBVIRT=true
            shift
            ;;
        --deployment-mode)
            DEPLOYMENT_MODE="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--title TITLE] [--check-pull-secret] [--check-system-resources] [--check-libvirt] [--deployment-mode MODE]"
            exit 1
            ;;
    esac
done

# Track failures
FAILED=0

output "## $TITLE"
output ""

# Check required environment variables
output "### Environment Variables"

if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
    output "${RED}❌ DEV_SCRIPTS_PATH not set${NC}"
    FAILED=1
else
    output "${GREEN}✅ DEV_SCRIPTS_PATH: $DEV_SCRIPTS_PATH${NC}"
fi

# Check for BASE_WORKING_DIR or WORKING_DIR
# BASE_WORKING_DIR is used for initial setup, WORKING_DIR is set later by setup-working-dir
if [ -z "${BASE_WORKING_DIR:-}" ] && [ -z "${WORKING_DIR:-}" ]; then
    output "${RED}❌ Neither BASE_WORKING_DIR nor WORKING_DIR is set${NC}"
    FAILED=1
else
    if [ -n "${BASE_WORKING_DIR:-}" ]; then
        output "${GREEN}✅ BASE_WORKING_DIR: $BASE_WORKING_DIR${NC}"
    fi
    if [ -n "${WORKING_DIR:-}" ]; then
        output "${GREEN}✅ WORKING_DIR: $WORKING_DIR${NC}"
    fi
fi

if [ "$CHECK_PULL_SECRET" = true ]; then
    if [ -z "${PULL_SECRET:-}" ]; then
        output "${RED}❌ PULL_SECRET not set${NC}"
        FAILED=1
    else
        output "${GREEN}✅ PULL_SECRET: configured${NC}"
    fi
fi

if [ -n "$DEPLOYMENT_MODE" ]; then
    output "${GREEN}✅ Deployment mode: $DEPLOYMENT_MODE${NC}"
fi

# Check system resources if requested
if [ "$CHECK_SYSTEM_RESOURCES" = true ]; then
    output ""
    output "### System Resources"

    TOTAL_RAM=$(free -g | awk '/^Mem:/{print $2}')
    output "${GREEN}✅ Total RAM: ${TOTAL_RAM}GB${NC}"

    # Check disk space on WORKING_DIR if set, otherwise BASE_WORKING_DIR
    CHECK_DIR="${WORKING_DIR:-${BASE_WORKING_DIR}}"
    if [ -n "$CHECK_DIR" ]; then
        AVAILABLE_DISK=$(df -h "$CHECK_DIR" 2>/dev/null | awk 'NR==2{print $4}')
        if [ -n "$AVAILABLE_DISK" ]; then
            output "${GREEN}✅ Available disk space: $AVAILABLE_DISK${NC}"
        fi
    fi
fi

# Check libvirt access if requested
if [ "$CHECK_LIBVIRT" = true ]; then
    output ""
    output "### Libvirt Access"

    if sudo virsh list --all > /dev/null 2>&1; then
        output "${GREEN}✅ Libvirt access verified${NC}"
    else
        output "${RED}❌ Cannot access libvirt${NC}"
        FAILED=1
    fi
fi

# Final status
output ""
if [ $FAILED -eq 0 ]; then
    output "${GREEN}✅ All pre-flight checks passed${NC}"
    echo -e "${GREEN}✅ All pre-flight checks passed${NC}" >&2
    exit 0
else
    output "${RED}❌ Pre-flight checks failed${NC}"
    output ""
    output "**Action Required**: Configure repository variables and secrets in Settings → Secrets and variables → Actions"
    echo -e "${RED}❌ Pre-flight checks failed${NC}" >&2
    exit 1
fi

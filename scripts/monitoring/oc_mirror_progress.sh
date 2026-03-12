#!/bin/bash
#
# OC-Mirror Progress Monitor
# Monitors oc-mirror log and displays progress statistics
#
# Usage: oc_mirror_progress.sh <log-file> [update-interval-seconds]
#

set -euo pipefail

LOG_FILE="${1:-}"
UPDATE_INTERVAL="${2:-5}"  # Default: update every 5 seconds

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Validation
if [ -z "$LOG_FILE" ]; then
    echo "Usage: $0 <oc-mirror-log-file> [update-interval-seconds]"
    exit 1
fi

# Wait for log file to be created
echo "Waiting for oc-mirror to start..."
WAIT_COUNT=0
while [ ! -f "$LOG_FILE" ] && [ $WAIT_COUNT -lt 30 ]; do
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

if [ ! -f "$LOG_FILE" ]; then
    echo "Error: Log file not found after 30 seconds: $LOG_FILE"
    exit 1
fi

echo "Monitoring oc-mirror progress (log: $LOG_FILE)"
echo ""

START_TIME=$(date +%s)

# Function to format seconds as HH:MM:SS
format_time() {
    local seconds=$1
    local hours=$((seconds / 3600))
    local minutes=$(((seconds % 3600) / 60))
    local secs=$((seconds % 60))
    printf "%02d:%02d:%02d" $hours $minutes $secs
}

# Function to format bytes
format_bytes() {
    local bytes=$1
    if [ $bytes -lt 1024 ]; then
        echo "${bytes}B"
    elif [ $bytes -lt $((1024 * 1024)) ]; then
        echo "$((bytes / 1024))KB"
    elif [ $bytes -lt $((1024 * 1024 * 1024)) ]; then
        echo "$((bytes / 1024 / 1024))MB"
    else
        echo "$((bytes / 1024 / 1024 / 1024))GB"
    fi
}

# Function to clear screen and show progress
show_progress() {
    local now
    now=$(date +%s)
    local elapsed=$((now - START_TIME))

    # Parse log file for statistics
    local total_images
    total_images=$(grep -c "mirroring image docker://" "$LOG_FILE" 2>/dev/null || echo "0")
    local completed_images
    completed_images=$(grep -c "successfully mirrored" "$LOG_FILE" 2>/dev/null || echo "0")
    local failed_images
    failed_images=$(grep -c "error mirroring image" "$LOG_FILE" 2>/dev/null || echo "0")
    local skipped_images
    skipped_images=$(grep -c "skipping operator bundle" "$LOG_FILE" 2>/dev/null || echo "0")

    # Get current operation
    local current_image
    current_image=$(tail -20 "$LOG_FILE" 2>/dev/null | grep "mirroring image docker://" | tail -1 | sed 's/.*docker:\/\///' | sed 's/ .*//' || echo "Starting...")

    # Get recent errors (last 3)
    local recent_errors
    recent_errors=$(grep "error mirroring image" "$LOG_FILE" 2>/dev/null | tail -3 | sed 's/error mirroring image docker:\/\///' | sed 's/ .*//' || echo "")

    # Calculate progress percentage
    local progress_pct=0
    if [ $total_images -gt 0 ]; then
        progress_pct=$(( (completed_images + skipped_images) * 100 / total_images ))
    fi

    # Estimate remaining time (rough)
    local eta="calculating..."
    if [ $completed_images -gt 10 ] && [ $elapsed -gt 60 ]; then
        local avg_time_per_image=$((elapsed / (completed_images + skipped_images)))
        local remaining_images=$((total_images - completed_images - skipped_images))
        local eta_seconds=$((avg_time_per_image * remaining_images))
        eta=$(format_time $eta_seconds)
    fi

    # Get workspace size if available
    local workspace_size="N/A"
    if [ -d "$(dirname "$LOG_FILE")/../config/oc-mirror-workspace" ]; then
        workspace_size=$(du -sh "$(dirname "$LOG_FILE")/../config/oc-mirror-workspace" 2>/dev/null | cut -f1 || echo "N/A")
    fi

    # Clear screen and display
    clear
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                   OC-Mirror Progress Monitor${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${CYAN}⏱️  Runtime:${NC} $(format_time $elapsed)"
    echo -e "${CYAN}📦 Workspace:${NC} $workspace_size"
    echo ""

    # Progress bar
    local bar_width=50
    local filled=$((progress_pct * bar_width / 100))
    local empty=$((bar_width - filled))
    echo -ne "${BOLD}Progress: ${NC}["
    echo -ne "${GREEN}"
    for ((i=0; i<filled; i++)); do echo -n "█"; done
    echo -ne "${NC}"
    for ((i=0; i<empty; i++)); do echo -n "░"; done
    echo -e "] ${BOLD}${progress_pct}%${NC}"
    echo ""

    # Statistics
    echo -e "${BOLD}Statistics:${NC}"
    echo -e "  ${GREEN}✓${NC} Completed:  ${BOLD}${completed_images}${NC}"
    if [ $skipped_images -gt 0 ]; then
        echo -e "  ${YELLOW}⊘${NC} Skipped:    ${BOLD}${skipped_images}${NC}"
    fi
    if [ $failed_images -gt 0 ]; then
        echo -e "  ${RED}✗${NC} Failed:     ${BOLD}${failed_images}${NC}"
    fi
    echo -e "  ${BLUE}∑${NC} Total:      ${BOLD}${total_images}${NC}"
    echo ""

    # Time estimate
    if [ "$eta" != "calculating..." ]; then
        echo -e "${CYAN}⏰ Estimated remaining:${NC} ${BOLD}${eta}${NC}"
        echo ""
    fi

    # Current operation
    echo -e "${BOLD}Current operation:${NC}"
    if [ ${#current_image} -gt 70 ]; then
        echo -e "  ${YELLOW}→${NC} ...${current_image: -70}"
    else
        echo -e "  ${YELLOW}→${NC} ${current_image}"
    fi
    echo ""

    # Recent errors (if any)
    if [ -n "$recent_errors" ] && [ $failed_images -gt 0 ]; then
        echo -e "${BOLD}Recent errors:${NC}"
        echo "$recent_errors" | while IFS= read -r error; do
            if [ -n "$error" ]; then
                if [ ${#error} -gt 65 ]; then
                    echo -e "  ${RED}✗${NC} ...${error: -65}"
                else
                    echo -e "  ${RED}✗${NC} ${error}"
                fi
            fi
        done
        echo ""
    fi

    # Footer
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "Press Ctrl+C to stop monitoring (oc-mirror will continue)"
    echo -e "Log: ${LOG_FILE}"
    echo -e "Last update: $(date '+%Y-%m-%d %H:%M:%S')"
}

# Initial display
show_progress

# Monitor loop
while true; do
    sleep $UPDATE_INTERVAL

    # Check if oc-mirror is still running
    if grep -q "Total images mirrored:" "$LOG_FILE" 2>/dev/null; then
        # OC-mirror completed
        show_progress
        echo ""
        echo -e "${GREEN}${BOLD}✓ OC-Mirror completed!${NC}"
        echo ""

        # Show final summary
        grep "Total images mirrored:" "$LOG_FILE" 2>/dev/null || true
        grep "Phase.*:" "$LOG_FILE" 2>/dev/null | tail -5 || true

        break
    fi

    # Check if oc-mirror failed
    if grep -q "FATAL" "$LOG_FILE" 2>/dev/null; then
        show_progress
        echo ""
        echo -e "${RED}${BOLD}✗ OC-Mirror encountered a fatal error${NC}"
        echo ""
        grep "FATAL" "$LOG_FILE" | tail -3
        exit 1
    fi

    show_progress
done

#!/bin/bash
# Verify Ceph cluster health for CI pre-flight checks
#
# Checks that the Ceph cluster is accessible and healthy before
# attempting an ODF deployment.
#
# Usage: CEPH_HOST_IP=<ip> ./verify_ceph.sh
# Exit codes:
#   0 - All checks passed
#   1 - One or more checks failed

set -euo pipefail

CEPH_HOST_IP="${CEPH_HOST_IP:?CEPH_HOST_IP must be set}"
RGW_PORT="${RGW_PORT:-7480}"

FAILED=0

info()    { echo "[INFO]  $*"; }
pass()    { echo "[PASS]  $*"; }
fail()    { echo "[FAIL]  $*"; FAILED=1; }

# Port connectivity check -- tries nc first, falls back to bash /dev/tcp
check_port() {
    local host="$1" port="$2"
    if command -v nc &>/dev/null; then
        nc -z -w5 "$host" "$port" 2>/dev/null
    else
        timeout 5 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null
    fi
}

# Check MON port reachable
info "Checking Ceph MON port (3300) on $CEPH_HOST_IP..."
if check_port "$CEPH_HOST_IP" 3300; then
    pass "MON port 3300 is reachable"
else
    fail "MON port 3300 is NOT reachable on $CEPH_HOST_IP"
fi

# Check RGW endpoint
info "Checking RadosGW endpoint on $CEPH_HOST_IP:$RGW_PORT..."
if curl -sf --connect-timeout 5 "http://${CEPH_HOST_IP}:${RGW_PORT}/" >/dev/null 2>&1; then
    pass "RadosGW endpoint is responding"
else
    fail "RadosGW endpoint is NOT responding on http://${CEPH_HOST_IP}:${RGW_PORT}/"
fi

# Check metrics endpoint (port 9283, required by ODF)
info "Checking Ceph metrics endpoint on $CEPH_HOST_IP:9283..."
if curl -sf --connect-timeout 5 "http://${CEPH_HOST_IP}:9283/metrics" >/dev/null 2>&1; then
    pass "Metrics endpoint is responding"
else
    fail "Metrics endpoint is NOT responding on http://${CEPH_HOST_IP}:9283/metrics"
fi

# Check ceph health (only works if running on the Ceph host itself)
if command -v cephadm &>/dev/null; then
    info "Checking Ceph cluster health via cephadm..."
    health=$(sudo cephadm shell -- ceph health 2>/dev/null || echo "UNREACHABLE")
    if echo "$health" | grep -qE "HEALTH_OK|HEALTH_WARN"; then
        pass "Ceph health: $health"
    else
        fail "Ceph health: $health"
    fi
else
    info "cephadm not available locally - skipping ceph health check"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    pass "All Ceph pre-flight checks passed"
    exit 0
else
    fail "Some Ceph pre-flight checks failed"
    exit 1
fi

#!/bin/bash
# Set up a single-node Ceph cluster using cephadm for ODF CI testing
#
# This script is idempotent and can be re-run safely. It:
#   1. Installs cephadm and ceph-common
#   2. Bootstraps a single-node Ceph cluster
#   3. Creates loopback-backed OSDs
#   4. Configures single-node replication
#   5. Deploys RadosGW (S3-compatible)
#   6. Creates S3 user and bucket for Quay
#   7. Creates RBD pool for block storage
#   8. Configures firewall to restrict Ceph ports
#   9. Exports odfExternalConfig and prints GitHub secrets
#
# Usage: sudo ./setup_ceph.sh
# Environment:
#   CEPH_HOST_IP           - Host IP for Ceph to bind to (auto-detected if unset)
#   OSD_COUNT              - Number of loopback OSDs to create (default: 3)
#   OSD_SIZE_GB            - Size of each loopback OSD in GB (default: 50)
#   RGW_PORT               - RadosGW port (default: 7480)
#   LOOP_DIR               - Directory for loopback files (default: /var/lib/ceph-loops)
#   SKIP_LOOPBACK_SERVICE  - Skip systemd loopback service (default: false)
#   SKIP_FIREWALL          - Skip firewall configuration (default: false)
#   CEPH_CONFIG_DIR        - Write config files for CI (unset = skip)

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================
OSD_COUNT="${OSD_COUNT:-3}"
OSD_SIZE_GB="${OSD_SIZE_GB:-50}"
RGW_PORT="${RGW_PORT:-7480}"
LOOP_DIR="${LOOP_DIR:-/var/lib/ceph-loops}"
LOOPBACK_SERVICE="ceph-loopback"
LOOPBACK_HELPER="/usr/local/bin/ceph-attach-loops.sh"
RGW_SERVICE_NAME="ci-rgw"
S3_USER="quay-ci"
S3_BUCKET="quay-storage"
RBD_POOL="ceph-rbd"

# Internal CIDRs allowed to reach Ceph ports
ALLOWED_CIDRS=("192.168.0.0/16" "100.64.0.0/16" "127.0.0.0/8")
CEPH_TCP_PORTS=(3300 6789 "$RGW_PORT")
CEPH_OSD_PORT_RANGE="6800-7300"

# ============================================================================
# Helpers
# ============================================================================
info()    { echo "[INFO]  $*"; }
warn()    { echo "[WARN]  $*"; }
error()   { echo "[ERROR] $*" >&2; }
success() { echo "[OK]    $*"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error "This script must be run as root"
        exit 1
    fi
}

detect_host_ip() {
    if [ -n "${CEPH_HOST_IP:-}" ]; then
        info "Using CEPH_HOST_IP from environment: $CEPH_HOST_IP"
        return
    fi
    # Get IP of the default route interface
    CEPH_HOST_IP=$(ip -4 route get 8.8.8.8 2>/dev/null | awk '/src/ {print $7; exit}')
    if [ -z "$CEPH_HOST_IP" ]; then
        error "Could not detect host IP. Set CEPH_HOST_IP manually."
        exit 1
    fi
    info "Auto-detected host IP: $CEPH_HOST_IP"
}

wait_for_ceph_health() {
    local max_attempts="${1:-60}"
    local interval="${2:-10}"
    info "Waiting for Ceph cluster to become healthy..."
    for i in $(seq 1 "$max_attempts"); do
        health=$(cephadm shell -- ceph health 2>/dev/null || echo "UNKNOWN")
        if echo "$health" | grep -qE "HEALTH_OK|HEALTH_WARN"; then
            success "Ceph health: $health"
            return 0
        fi
        echo "  Attempt $i/$max_attempts: $health"
        sleep "$interval"
    done
    warn "Ceph did not reach HEALTH_OK/HEALTH_WARN within $((max_attempts * interval))s"
    warn "Current health: $(cephadm shell -- ceph health 2>/dev/null || echo 'UNKNOWN')"
    return 1
}

wait_for_osds() {
    local expected="$1"
    local max_attempts="${2:-60}"
    local interval="${3:-10}"
    info "Waiting for $expected OSD(s) to come up..."
    for i in $(seq 1 "$max_attempts"); do
        up_count=$(cephadm shell -- ceph osd stat 2>/dev/null | grep -oP '\d+ up' | awk '{print $1}' || echo "0")
        if [ "$up_count" -ge "$expected" ]; then
            success "$up_count OSD(s) are up"
            return 0
        fi
        echo "  Attempt $i/$max_attempts: $up_count/$expected OSDs up"
        sleep "$interval"
    done
    warn "Only $up_count/$expected OSDs came up"
    return 1
}

wait_for_rgw() {
    local max_attempts=30
    local interval=10
    info "Waiting for RadosGW to respond on port $RGW_PORT..."
    for i in $(seq 1 "$max_attempts"); do
        if curl -sf "http://${CEPH_HOST_IP}:${RGW_PORT}/" >/dev/null 2>&1; then
            success "RadosGW is responding"
            return 0
        fi
        echo "  Attempt $i/$max_attempts: not ready"
        sleep "$interval"
    done
    warn "RadosGW did not respond within $((max_attempts * interval))s"
    return 1
}

# ============================================================================
# Main
# ============================================================================
require_root
detect_host_ip

# Step 1: Install cephadm and ceph-common
CEPH_RELEASE="${CEPH_RELEASE:-reef}"
info "Step 1: Installing cephadm and ceph-common (release: $CEPH_RELEASE)..."
if command -v cephadm &>/dev/null; then
    info "cephadm already installed"
else
    # Download cephadm from the official Ceph download server
    info "Downloading cephadm from download.ceph.com..."
    curl --silent --location -o cephadm.py \
        "https://download.ceph.com/rpm-${CEPH_RELEASE}/el9/noarch/cephadm" || {
        # Fallback to GitHub if the official server doesn't have this release
        warn "Official download server failed, falling back to GitHub..."
        curl --silent --remote-name --location \
            "https://raw.githubusercontent.com/ceph/ceph/${CEPH_RELEASE}/src/cephadm/cephadm.py"
    }

    # Try to add repo and install via package manager
    if python3 cephadm.py add-repo --release "$CEPH_RELEASE" 2>/dev/null; then
        python3 cephadm.py install
    else
        # Repo not available for this OS (e.g. CentOS Stream 10)
        # Install cephadm as a standalone script -- it pulls container images
        # and does not need packages from the repo
        warn "Ceph repo not available for this OS -- installing cephadm standalone"
        install -m 0755 cephadm.py /usr/sbin/cephadm
    fi
    rm -f cephadm.py
    success "cephadm installed"
fi

if ! command -v ceph &>/dev/null; then
    info "Installing ceph-common..."
    # ceph-common may not be available without a repo; use cephadm shell instead
    cephadm install ceph-common 2>/dev/null || \
        warn "ceph-common not available -- will use 'cephadm shell' for CLI commands"
fi

# Step 2: Bootstrap cluster (if not already bootstrapped)
info "Step 2: Bootstrapping Ceph cluster..."
if cephadm shell -- ceph status &>/dev/null; then
    info "Ceph cluster already bootstrapped"
else
    cephadm bootstrap \
        --mon-ip "$CEPH_HOST_IP" \
        --single-host-defaults \
        --skip-monitoring-stack \
        --skip-dashboard \
        --allow-fqdn-hostname \
        --cleanup-on-failure
    success "Ceph cluster bootstrapped"
fi

# Step 3: Create loopback files and attach them
info "Step 3: Creating loopback OSD backing files..."
mkdir -p "$LOOP_DIR"

for i in $(seq 0 $((OSD_COUNT - 1))); do
    IMG="${LOOP_DIR}/osd-${i}.img"
    if [ -f "$IMG" ]; then
        info "  $IMG already exists"
    else
        truncate -s "${OSD_SIZE_GB}G" "$IMG"
        success "  Created $IMG (${OSD_SIZE_GB}GB sparse)"
    fi

    # Attach if not already attached
    if ! losetup -j "$IMG" | grep -q .; then
        LOOP_DEV=$(losetup --find --show "$IMG")
        success "  Attached $IMG -> $LOOP_DEV"
    else
        LOOP_DEV=$(losetup -j "$IMG" | head -1 | cut -d: -f1)
        info "  $IMG already attached on $LOOP_DEV"
    fi

    # Create LVM on loop device (cephadm filters out raw loop devices)
    VG_NAME="ceph-vg${i}"
    LV_NAME="ceph-lv${i}"
    if vgs "$VG_NAME" &>/dev/null; then
        info "  VG $VG_NAME already exists"
    else
        pvcreate --yes "$LOOP_DEV"
        vgcreate "$VG_NAME" "$LOOP_DEV"
        success "  Created VG $VG_NAME on $LOOP_DEV"
    fi
    if lvs "$VG_NAME/$LV_NAME" &>/dev/null; then
        info "  LV $VG_NAME/$LV_NAME already exists"
    else
        lvcreate -l 100%FREE -n "$LV_NAME" "$VG_NAME" --yes
        success "  Created LV $VG_NAME/$LV_NAME"
    fi
done

# Step 4: Install loopback re-attachment systemd service
if [ "${SKIP_LOOPBACK_SERVICE:-false}" = "true" ]; then
    info "Step 4: Skipping ceph-loopback systemd service (SKIP_LOOPBACK_SERVICE=true)"
else
    info "Step 4: Installing ceph-loopback systemd service..."

    # Install the helper script
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
    if [ -f "${SCRIPT_DIR}/ceph-attach-loops.sh" ]; then
        cp "${SCRIPT_DIR}/ceph-attach-loops.sh" "$LOOPBACK_HELPER"
    else
        # Inline fallback if helper not found alongside this script
        cat > "$LOOPBACK_HELPER" <<'HELPER_EOF'
#!/bin/bash
LOOP_DIR="${LOOP_DIR:-/var/lib/ceph-loops}"
for img in "$LOOP_DIR"/osd-*.img; do
    [ -f "$img" ] || continue
    if ! losetup -j "$img" | grep -q .; then
        losetup --find --show "$img"
    fi
done
# Activate Ceph LVM volume groups after loop devices are attached
for vg in $(vgs --noheadings -o vg_name 2>/dev/null | grep 'ceph-vg'); do
    vgchange -ay "$vg" 2>/dev/null || true
done
HELPER_EOF
    fi
    chmod +x "$LOOPBACK_HELPER"

    cat > "/etc/systemd/system/${LOOPBACK_SERVICE}.service" <<EOF
[Unit]
Description=Attach Ceph loopback OSD devices
DefaultDependencies=no
Before=ceph.target
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=LOOP_DIR=${LOOP_DIR}
ExecStart=${LOOPBACK_HELPER}

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "${LOOPBACK_SERVICE}.service"
    success "ceph-loopback systemd service installed and enabled"
fi

# Step 5: Add OSDs on loopback devices
info "Step 5: Adding OSDs on loopback devices..."
HOSTNAME=$(hostname)

existing_osds=$(cephadm shell -- ceph osd stat 2>/dev/null | grep -oP '^\d+' || echo "0")
if [ "$existing_osds" -ge "$OSD_COUNT" ]; then
    info "Already have $existing_osds OSD(s), skipping OSD creation"
else
    for i in $(seq 0 $((OSD_COUNT - 1))); do
        VG_NAME="ceph-vg${i}"
        LV_NAME="ceph-lv${i}"
        LV_PATH="/dev/${VG_NAME}/${LV_NAME}"
        if [ ! -e "$LV_PATH" ]; then
            error "  LV $LV_PATH does not exist - skipping"
            continue
        fi
        info "  Adding OSD on $LV_PATH..."
        cephadm shell -- ceph orch daemon add osd "$HOSTNAME:$LV_PATH" || {
            warn "  OSD add returned non-zero for $LV_PATH (may already exist)"
        }
    done
    wait_for_osds "$OSD_COUNT"
fi

# Step 6: Configure single-node replication
info "Step 6: Configuring single-node replication..."
cephadm shell -- ceph config set global osd_pool_default_size 1
cephadm shell -- ceph config set global osd_pool_default_min_size 1
cephadm shell -- ceph config set global mon_allow_pool_size_one true
success "Single-node replication configured"

# Step 7: Deploy RadosGW
info "Step 7: Deploying RadosGW..."
rgw_running=$(cephadm shell -- ceph orch ls --service-type rgw --format json 2>/dev/null | python3 -c "
import json,sys
data = json.load(sys.stdin)
print(sum(1 for s in data if s.get('service_name','').startswith('rgw.')))
" 2>/dev/null || echo "0")

if [ "$rgw_running" -gt 0 ]; then
    info "RadosGW service already exists"
else
    cephadm shell -- ceph orch apply rgw "$RGW_SERVICE_NAME" \
        --placement="1 $HOSTNAME" \
        --port="$RGW_PORT"
    success "RadosGW service deployed"
fi
wait_for_rgw

# Enable MGR prometheus module for monitoring endpoint (port 9283, required by ODF)
info "Enabling MGR prometheus module for monitoring metrics..."
if cephadm shell -- ceph mgr module enable prometheus 2>/dev/null; then
    success "MGR prometheus module enabled (port 9283)"
else
    warn "MGR prometheus module enable failed - monitoring metrics may be unavailable"
fi

# Step 8: Create S3 user and bucket
info "Step 8: Creating S3 user and bucket..."
if cephadm shell -- radosgw-admin user info --uid="$S3_USER" &>/dev/null; then
    info "S3 user '$S3_USER' already exists"
else
    cephadm shell -- radosgw-admin user create \
        --uid="$S3_USER" \
        --display-name="Quay CI" \
        --system
    success "S3 user '$S3_USER' created"
fi

# Extract S3 keys
S3_ACCESS_KEY=$(cephadm shell -- radosgw-admin user info --uid="$S3_USER" 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['keys'][0]['access_key'])")
S3_SECRET_KEY=$(cephadm shell -- radosgw-admin user info --uid="$S3_USER" 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['keys'][0]['secret_key'])")

# Create bucket via S3 API (radosgw-admin doesn't have 'bucket create')
if cephadm shell -- radosgw-admin bucket stats --bucket="$S3_BUCKET" &>/dev/null; then
    info "Bucket '$S3_BUCKET' already exists"
else
    S3_ACCESS_KEY="$S3_ACCESS_KEY" \
    S3_SECRET_KEY="$S3_SECRET_KEY" \
    CEPH_HOST_IP="$CEPH_HOST_IP" \
    RGW_PORT="$RGW_PORT" \
    S3_BUCKET="$S3_BUCKET" \
    python3 -c "
import os, urllib.request, hmac, hashlib, base64, datetime
host = os.environ['CEPH_HOST_IP'] + ':' + os.environ['RGW_PORT']
bucket = os.environ['S3_BUCKET']
access_key = os.environ['S3_ACCESS_KEY']
secret_key = os.environ['S3_SECRET_KEY']
date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')
string_to_sign = 'PUT\n\n\n' + date + '\n/' + bucket
sig = base64.b64encode(hmac.new(
    secret_key.encode(), string_to_sign.encode(), hashlib.sha1
).digest()).decode()
req = urllib.request.Request(
    'http://' + host + '/' + bucket,
    method='PUT',
    headers={
        'Date': date,
        'Authorization': 'AWS ' + access_key + ':' + sig,
    }
)
urllib.request.urlopen(req)
"
    success "Bucket '$S3_BUCKET' created"
fi

# Step 9: Create RBD pool
info "Step 9: Creating RBD pool..."
if cephadm shell -- ceph osd pool ls | grep -qx "$RBD_POOL"; then
    info "Pool '$RBD_POOL' already exists"
else
    cephadm shell -- ceph osd pool create "$RBD_POOL" 32
    success "Pool '$RBD_POOL' created"
fi
cephadm shell -- ceph osd pool application enable "$RBD_POOL" rbd 2>/dev/null || true

# Adjust pool replication for single-node
cephadm shell -- ceph osd pool set "$RBD_POOL" size 1 --yes-i-really-mean-it
cephadm shell -- ceph osd pool set "$RBD_POOL" min_size 1

# Step 10: Export odfExternalConfig
info "Step 10: Exporting odfExternalConfig..."

# Try to find the exporter script inside the cephadm container
EXPORTER_PATH=$(cephadm shell -- bash -c \
    'for p in /usr/sbin/ceph-external-cluster-details-exporter.py /usr/share/ceph/ceph-external-cluster-details-exporter.py; do [ -f "$p" ] && echo "$p" && break; done' \
    2>/dev/null || true)

if [ -n "$EXPORTER_PATH" ]; then
    info "Using exporter at $EXPORTER_PATH"
    ODF_EXTERNAL_CONFIG=$(cephadm shell -- python3 "$EXPORTER_PATH" \
        --rbd-data-pool-name "$RBD_POOL" \
        --monitoring-endpoint "$CEPH_HOST_IP" \
        --monitoring-endpoint-port 9283 \
        --rgw-endpoint "http://${CEPH_HOST_IP}:${RGW_PORT}")
else
    info "Exporter script not found in container, generating config manually..."

    # Extract cluster details
    FSID=$(cephadm shell -- ceph fsid 2>/dev/null)
    MON_DUMP=$(cephadm shell -- ceph mon dump --format json 2>/dev/null)
    MON_HOSTS=$(echo "$MON_DUMP" | python3 -c "
import json,sys
d = json.load(sys.stdin)
mons = d.get('mons', [])
addrs = []
for m in mons:
    a = m.get('public_addrs',{}).get('addrvec',[])
    for e in a:
        if e.get('type') == 'v1':
            addrs.append(e['addr'])
print(','.join(addrs))
")
    ADMIN_KEY=$(cephadm shell -- ceph auth get-key client.admin 2>/dev/null)

    # Create dedicated CSI users for RBD (rook uses admin creds to manage them,
    # but the auth entries must exist in Ceph for the CSI pods to authenticate)
    cephadm shell -- ceph auth get-or-create client.csi-rbd-node \
        mon 'profile rbd' \
        osd "profile rbd pool=${RBD_POOL}" 2>/dev/null || true

    cephadm shell -- ceph auth get-or-create client.csi-rbd-provisioner \
        mon 'profile rbd' \
        mgr 'allow rw' \
        osd "profile rbd pool=${RBD_POOL}" 2>/dev/null || true

    # Get RGW keys
    RGW_ACCESS_KEY="$S3_ACCESS_KEY"
    RGW_SECRET_KEY="$S3_SECRET_KEY"

    # Note: rook-csi-rbd-node and rook-csi-rbd-provisioner are NOT included here.
    # Rook creates these secrets itself using the admin credentials. Including them
    # causes a type conflict: ODF creates them as Opaque, rook expects kubernetes.io/rook.
    ODF_EXTERNAL_CONFIG=$(
        FSID="$FSID" \
        MON_HOSTS="$MON_HOSTS" \
        ADMIN_KEY="$ADMIN_KEY" \
        CEPH_HOST_IP="$CEPH_HOST_IP" \
        RGW_PORT="$RGW_PORT" \
        RBD_POOL="$RBD_POOL" \
        RGW_ACCESS_KEY="$RGW_ACCESS_KEY" \
        RGW_SECRET_KEY="$RGW_SECRET_KEY" \
        python3 -c "
import os, json
e = os.environ
data = [
    {'name': 'rook-ceph-mon-endpoints', 'kind': 'ConfigMap', 'data': {'data': e['FSID']+'='+e['MON_HOSTS'], 'maxMonId': '0', 'mapping': '{}'}},
    {'name': 'rook-ceph-mon', 'kind': 'Secret', 'data': {'admin-secret': 'admin-secret', 'fsid': e['FSID'], 'mon-secret': 'mon-secret'}},
    {'name': 'rook-ceph-operator-creds', 'kind': 'Secret', 'data': {'userID': 'client.admin', 'userKey': e['ADMIN_KEY']}},
    {'name': 'monitoring-endpoint', 'kind': 'CephCluster', 'data': {'MonitoringEndpoint': e['CEPH_HOST_IP'], 'MonitoringPort': '9283'}},
    {'name': 'ceph-rbd', 'kind': 'StorageClass', 'data': {'pool': e['RBD_POOL']}},
    {'name': 'ceph-rgw', 'kind': 'StorageClass', 'data': {'endpoint': e['CEPH_HOST_IP']+':'+e['RGW_PORT'], 'poolPrefix': 'default'}},
    {'name': 'rgw-admin-ops-user', 'kind': 'Secret', 'data': {'accessKey': e['RGW_ACCESS_KEY'], 'secretKey': e['RGW_SECRET_KEY']}}
]
print(json.dumps(data))
")
fi
success "odfExternalConfig exported"

# Step 11: Configure firewall
CEPH_METRICS_PORT=9283
if [ "${SKIP_FIREWALL:-false}" = "true" ]; then
    info "Step 11: Skipping firewall configuration (SKIP_FIREWALL=true)"
elif ! command -v firewall-cmd &>/dev/null; then
    info "Step 11: Configuring firewall rules..."
    warn "firewalld not found - skipping firewall configuration"
    warn "Ensure Ceph ports are protected by other means"
else
    info "Step 11: Configuring firewall rules..."
    # --- public zone: allow internal CIDRs, reject everything else ---
    for port in "${CEPH_TCP_PORTS[@]}" "$CEPH_METRICS_PORT"; do
        for cidr in "${ALLOWED_CIDRS[@]}"; do
            firewall-cmd --permanent \
                --add-rich-rule="rule family=ipv4 source address=$cidr port port=$port protocol=tcp accept" \
                2>/dev/null || true
        done
    done
    # OSD data ports - only internal networks (skip 127.0.0.0/8 for port range)
    for cidr in "192.168.0.0/16" "100.64.0.0/16"; do
        firewall-cmd --permanent \
            --add-rich-rule="rule family=ipv4 source address=$cidr port port=$CEPH_OSD_PORT_RANGE protocol=tcp accept" \
            2>/dev/null || true
    done

    # Reject all other (external) traffic to Ceph ports.
    # Rich rules with a source match (above) take priority over these
    # source-less reject rules, so internal traffic is still allowed.
    CEPH_ALL_REJECT_PORTS=("${CEPH_TCP_PORTS[@]}" "$CEPH_METRICS_PORT" 8443 "$CEPH_OSD_PORT_RANGE")
    for port in "${CEPH_ALL_REJECT_PORTS[@]}"; do
        firewall-cmd --permanent \
            --add-rich-rule="rule family=ipv4 port port=$port protocol=tcp reject" \
            2>/dev/null || true
    done

    # --- libvirt zone: allow Ceph ports for VM traffic ---
    # Libvirt bridges are in the 'libvirt' zone, not 'public'.
    # VM traffic to the host IP enters through the bridge interface,
    # so it is evaluated against the libvirt zone rules.
    if firewall-cmd --info-zone=libvirt &>/dev/null; then
        for port in "${CEPH_TCP_PORTS[@]}" "$CEPH_METRICS_PORT"; do
            firewall-cmd --permanent --zone=libvirt --add-port="${port}/tcp" 2>/dev/null || true
        done
        firewall-cmd --permanent --zone=libvirt --add-port="${CEPH_OSD_PORT_RANGE}/tcp" 2>/dev/null || true
        success "Libvirt zone: Ceph ports opened for VM traffic"
    fi

    firewall-cmd --reload
    success "Firewall rules configured - Ceph ports restricted to internal networks"
fi

# Step 12: Wait for cluster to stabilize
wait_for_ceph_health 30 10

# Step 13: Write config files for CI consumption
CEPH_CONFIG_DIR="${CEPH_CONFIG_DIR:-}"
if [ -n "$CEPH_CONFIG_DIR" ]; then
    info "Step 13: Writing config files to $CEPH_CONFIG_DIR..."
    mkdir -p "$CEPH_CONFIG_DIR"
    echo "$ODF_EXTERNAL_CONFIG" > "${CEPH_CONFIG_DIR}/odf_external_config.json"
    cat > "${CEPH_CONFIG_DIR}/quay_backend_rgw_config.yaml" <<EOF
{access_key: ${S3_ACCESS_KEY}, secret_key: ${S3_SECRET_KEY}, bucket_name: ${S3_BUCKET}, hostname: ${CEPH_HOST_IP}, port: ${RGW_PORT}, is_secure: false}
EOF
    # Make files readable by non-root users
    chmod 644 "${CEPH_CONFIG_DIR}/odf_external_config.json"
    chmod 644 "${CEPH_CONFIG_DIR}/quay_backend_rgw_config.yaml"
    success "Config files written to $CEPH_CONFIG_DIR"
else
    info "Step 13: Skipping config file output (CEPH_CONFIG_DIR not set)"
fi

# ============================================================================
# Output Summary (credentials redacted)
# ============================================================================
echo ""
echo "============================================================================"
echo "SETUP COMPLETE"
echo "============================================================================"
echo ""
echo "Ceph cluster is running on $CEPH_HOST_IP"
echo ""
echo "  FSID:       $(cephadm shell -- ceph fsid 2>/dev/null || echo 'unknown')"
echo "  MON:        $CEPH_HOST_IP:3300,6789"
echo "  RGW:        http://$CEPH_HOST_IP:$RGW_PORT"
echo "  Metrics:    http://$CEPH_HOST_IP:9283"
echo "  RBD pool:   $RBD_POOL"
echo "  S3 bucket:  $S3_BUCKET"
echo "  S3 user:    $S3_USER"
echo ""
if [ -n "$CEPH_CONFIG_DIR" ]; then
    echo "Config files: $CEPH_CONFIG_DIR/"
    echo "  - odf_external_config.json"
    echo "  - quay_backend_rgw_config.yaml"
else
    echo "Credentials (set as GitHub secrets for manual use):"
    echo "  ODF_EXTERNAL_CONFIG:     <use CEPH_CONFIG_DIR to write to file>"
    echo "  QUAY_BACKEND_RGW_CONFIG: <use CEPH_CONFIG_DIR to write to file>"
fi
echo ""
echo "To verify:"
echo "  ceph health"
echo "  curl http://${CEPH_HOST_IP}:${RGW_PORT}/"
echo "  radosgw-admin bucket stats --bucket=${S3_BUCKET}"
echo "============================================================================"

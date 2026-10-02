#!/bin/bash
#
# mirror_registry_probe.sh - system/podman sampler for mirror-registry runs.
#
# Purpose
#   Compare mirror-registry behaviour across versions (e.g. 1.3.11 vs 2.0.12)
#   during a mirror.sh run on the same host. It takes a one-time capacity
#   snapshot, then samples live host + podman + DB metrics at a fixed interval
#   and packages everything into a tarball so two runs can be analysed and
#   diffed side by side.
#
#   The two versions do NOT share a database backend:
#     - 1.3.11 ships a bundled PostgreSQL (fixed max_connections, default 100).
#       Connection-pool exhaustion is the known failure signature, so
#       pg_stat_activity is sampled every tick.
#     - 2.0.12 defaults to an embedded SQLite database (no quay-postgres
#       container, no connection pool). There Postgres metrics do not apply;
#       the probe instead tracks the SQLite file growth and "database is
#       locked" errors as the contention signal.
#
#   Everything else (host CPU/iowait/load/memory/swap, per-container podman
#   stats, disk usage, Quay gunicorn worker count, kernel PSI pressure,
#   iostat) is collected identically for both, so the runs are comparable.
#
#   The DB backend and the running containers are auto-detected; the probe
#   works unchanged against either version. Pass --label to tag the run
#   (recorded in every CSV row and in the output dir name) so comparisons
#   are self-identifying.
#
# Usage
#   Run it as the SAME user that owns the mirror-registry containers (the user
#   that ran the installer / runs mirror.sh), from the enclave repo directory,
#   in a separate terminal:
#
#       ./scripts/local/mirror_registry_probe.sh --label v1.3.11
#       # ... in another terminal: ./mirror.sh
#       # ... press Ctrl-C here once mirror.sh finishes or fails
#
#   Options:
#     -i, --interval N       seconds between samples (default: 15)
#     -d, --duration N       auto-stop after N seconds (default: 0 = until Ctrl-C)
#     -l, --label TEXT       run label, e.g. v1.3.11 / v2.0.12 (default: none)
#     -o, --out DIR          output directory (default: ./mirror-probe-<label>-<host>-<ts>)
#     -e, --enclave-dir DIR  enclave repo dir for config (default: .)
#     -w, --working-dir DIR  Quay/oc-mirror working dir (default: from config/global.yaml);
#                            its logs/ subdir is synced into the report every tick
#     -c, --containers "A B" explicit container names to monitor (default: auto-detect)
#         --allow-degraded   continue even if a present PostgreSQL cannot be sampled
#     -h, --help             show this help
#
# It is safe to run: it only reads state (podman inspect/logs/stats, psql
# SELECTs against pg_stat_activity, /proc, file sizes). It never modifies the
# registry.
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Defaults / args
# ----------------------------------------------------------------------------
INTERVAL=15
DURATION=0
ENCLAVE_DIR="."
WORKING_DIR_OVERRIDE=""
OUT=""
LABEL=""
CONTAINERS_OVERRIDE=""
ALLOW_DEGRADED=0

SCRIPT_NAME=$(basename "$0")

usage() { sed -n '2,/^set -euo pipefail/p' "$0" | sed '/^set -euo pipefail/d' | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        -i|--interval)     INTERVAL="${2:?}"; shift 2 ;;
        -d|--duration)     DURATION="${2:?}"; shift 2 ;;
        -l|--label)        LABEL="${2:?}"; shift 2 ;;
        -o|--out)          OUT="${2:?}"; shift 2 ;;
        -e|--enclave-dir)  ENCLAVE_DIR="${2:?}"; shift 2 ;;
        -w|--working-dir)  WORKING_DIR_OVERRIDE="${2:?}"; shift 2 ;;
        -c|--containers)   CONTAINERS_OVERRIDE="${2:?}"; shift 2 ;;
        --allow-degraded)  ALLOW_DEGRADED=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
    esac
done

case "$INTERVAL" in ''|*[!0-9]*) echo "interval must be an integer" >&2; exit 2 ;; esac
case "$DURATION" in ''|*[!0-9]*) echo "duration must be an integer" >&2; exit 2 ;; esac
# Sanitize label for use in paths (keep comparison dirs predictable).
LABEL_SLUG=$(printf '%s' "$LABEL" | tr -c 'A-Za-z0-9._-' '-' | sed 's/^-*//; s/-*$//')

TS=$(date +%Y%m%d_%H%M%S)
PROBE_START_EPOCH=$(date +%s)
HOST=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo host)
if [ -z "$OUT" ]; then
    if [ -n "$LABEL_SLUG" ]; then
        OUT="${PWD}/mirror-probe-${LABEL_SLUG}-${HOST}-${TS}"
    else
        OUT="${PWD}/mirror-probe-${HOST}-${TS}"
    fi
fi

# ----------------------------------------------------------------------------
# Logging helpers
# ----------------------------------------------------------------------------
c_red=$'\e[31m'; c_yel=$'\e[33m'; c_grn=$'\e[32m'; c_dim=$'\e[2m'; c_rst=$'\e[0m'
ERRLOG=""   # set after OUT dir exists
info()  { printf '%s[INFO]%s %s\n'  "$c_grn" "$c_rst" "$*"; }
warn()  { printf '%s[WARN]%s %s\n'  "$c_yel" "$c_rst" "$*" >&2; [ -n "$ERRLOG" ] && echo "[WARN] $*" >>"$ERRLOG" || true; }
err()   { printf '%s[ERR ]%s %s\n'  "$c_red" "$c_rst" "$*" >&2; [ -n "$ERRLOG" ] && echo "[ERR ] $*" >>"$ERRLOG" || true; }
has()   { command -v "$1" >/dev/null 2>&1; }

# Convert a podman-stats size token (e.g. "1.2GB", "512MB", "900kB", "4B") to MB.
# Used to flatten podman stats into a tidy CSV for cross-run comparison.
to_mb() { # value-with-unit
    awk -v s="$1" 'BEGIN{
        if (s=="" || s=="--") { print "NA"; exit }
        u=s; sub(/^[0-9.]+/,"",u); v=s; sub(/[^0-9.].*$/,"",v);
        if (v=="") { print "NA"; exit }
        f=1;
        if (u=="B")                       f=1/1048576;
        else if (u=="kB"||u=="KB"||u=="KiB") f=1/1024;
        else if (u=="MB"||u=="MiB")       f=1;
        else if (u=="GB"||u=="GiB")       f=1024;
        else if (u=="TB"||u=="TiB")       f=1048576;
        printf "%.3f", v*f;
    }'
}

# ----------------------------------------------------------------------------
# Preflight: verify commands
# ----------------------------------------------------------------------------
REQUIRED=(podman awk sed grep date sleep mkdir tar cat printf hostname cp)
OPTIONAL=(lscpu lsblk iostat mpstat vmstat ss ip numactl dmesg journalctl python3 curl free uptime nproc df stat find)

echo
info "mirror_registry_probe preflight"
echo   "  interval=${INTERVAL}s duration=$([ "$DURATION" = 0 ] && echo 'until Ctrl-C' || echo "${DURATION}s") label='${LABEL:-none}' out=${OUT}"
echo

missing_required=0
printf '  %-14s %-9s %s\n' "COMMAND" "ROLE" "STATUS"
printf '  %-14s %-9s %s\n' "-------" "----" "------"
for c in "${REQUIRED[@]}"; do
    if has "$c"; then printf '  %-14s %-9s %sfound%s\n' "$c" "required" "$c_grn" "$c_rst"
    else printf '  %-14s %-9s %sMISSING%s\n' "$c" "required" "$c_red" "$c_rst"; missing_required=1; fi
done
for c in "${OPTIONAL[@]}"; do
    if has "$c"; then printf '  %-14s %-9s %sfound%s\n' "$c" "optional" "$c_grn" "$c_rst"
    else printf '  %-14s %-9s %sskip%s\n'  "$c" "optional" "$c_dim" "$c_rst"; fi
done
echo

if [ "$missing_required" -ne 0 ]; then
    err "Required commands are missing. Aborting (fail fast)."
    exit 1
fi

# ----------------------------------------------------------------------------
# Container + DB backend + path detection (lazy, re-tried every tick)
# ----------------------------------------------------------------------------
# Container names differ slightly across mirror-registry versions, and 2.0.x
# has no quay-postgres container at all (SQLite). Rather than hardcode, detect
# the running set and classify the DB backend from it.
#
# Detection is LAZY: each piece (containers, DB backend, PostgreSQL sampling,
# data dir, oc-mirror log dir, SQLite file) is retried on every tick until it
# resolves. This means the probe can be started BEFORE the registry is up / the
# mirror has begun, without losing the container, DB and log data (the earlier
# one-shot-at-startup detection captured host metrics only in that case).
QUAY_APP=""; PG_CONT=""
MONITOR_CONTAINERS=()
DB_BACKEND="unknown"

# one-time announcement guards (so lazy re-detection doesn't spam the console)
_announced_containers=0
_announced_backend=0
_announced_pg=0
_announced_datadir=0
_announced_logdir=0
_announced_sqlite=0
_container_snapshot_done=0

detect_containers() {
    # Already resolved (or explicitly overridden)? nothing to do.
    [ "${#MONITOR_CONTAINERS[@]}" -gt 0 ] && return 0

    if [ -n "$CONTAINERS_OVERRIDE" ]; then
        # shellcheck disable=SC2206
        MONITOR_CONTAINERS=($CONTAINERS_OVERRIDE)
    else
        local running_names name
        running_names=$(podman ps --format '{{.Names}}' 2>/dev/null || true)
        [ -n "$running_names" ] || return 1
        while IFS= read -r name; do
            [ -n "$name" ] || continue
            case "$name" in
                *postgres*|*redis*|*quay*|*registry*) MONITOR_CONTAINERS+=("$name") ;;
            esac
        done <<<"$running_names"
    fi
    [ "${#MONITOR_CONTAINERS[@]}" -gt 0 ] || return 1

    local n
    for n in "${MONITOR_CONTAINERS[@]}"; do
        case "$n" in *postgres*) PG_CONT="$n" ;; esac
    done
    # Pick the Quay application container: prefer an exact quay-app, else first
    # quay*/registry* that is not the postgres/redis sidecar.
    if podman container exists quay-app 2>/dev/null; then
        QUAY_APP="quay-app"
    else
        for n in "${MONITOR_CONTAINERS[@]}"; do
            case "$n" in
                *postgres*|*redis*) : ;;
                *quay*|*registry*)  QUAY_APP="$n"; break ;;
            esac
        done
    fi
    # Classify DB backend.
    if [ -n "$PG_CONT" ]; then
        DB_BACKEND="postgres"
    elif [ -n "$QUAY_APP" ]; then
        DB_BACKEND="sqlite"   # 2.0.x default when no postgres sidecar is present
    fi
    return 0
}

# ----------------------------------------------------------------------------
# PostgreSQL connection detection (1.3.11 only; the key metric there)
# ----------------------------------------------------------------------------
PG_USER=""; PG_DB=""; PG_PASS=""; PG_OK=0; PG_MAX="NA"

try_pg() { # user db pass
    local u="$1" d="$2" p="$3"
    if [ -n "$p" ]; then
        podman exec -e PGPASSWORD="$p" "$PG_CONT" psql -U "$u" -d "$d" -tAc 'select 1' >/dev/null 2>&1
    else
        podman exec "$PG_CONT" psql -U "$u" -d "$d" -tAc 'select 1' >/dev/null 2>&1
    fi
}

detect_pg() {
    [ -n "$PG_CONT" ] || return 1
    podman container exists "$PG_CONT" 2>/dev/null || return 1
    local admin app_user app_pass app_db
    admin=$(podman exec "$PG_CONT" printenv POSTGRESQL_ADMIN_PASSWORD 2>/dev/null || true)
    app_user=$(podman exec "$PG_CONT" printenv POSTGRESQL_USER 2>/dev/null || true)
    app_pass=$(podman exec "$PG_CONT" printenv POSTGRESQL_PASSWORD 2>/dev/null || true)
    app_db=$(podman exec "$PG_CONT" printenv POSTGRESQL_DATABASE 2>/dev/null || true)

    if try_pg postgres postgres ""; then PG_USER=postgres; PG_DB=postgres; PG_PASS=""; return 0; fi
    if [ -n "$admin" ] && try_pg postgres "${app_db:-postgres}" "$admin"; then
        PG_USER=postgres; PG_DB="${app_db:-postgres}"; PG_PASS="$admin"; return 0; fi
    if [ -n "$app_user" ] && try_pg "$app_user" "${app_db:-$app_user}" "$app_pass"; then
        PG_USER="$app_user"; PG_DB="${app_db:-$app_user}"; PG_PASS="$app_pass"; return 0; fi
    if [ -n "$app_user" ] && try_pg "$app_user" "${app_db:-$app_user}" ""; then
        PG_USER="$app_user"; PG_DB="${app_db:-$app_user}"; PG_PASS=""; return 0; fi
    return 1
}

pg_query() { # runs a query, pipe separated, empty on failure
    if [ "$PG_OK" -ne 1 ]; then return 0; fi
    if [ -n "$PG_PASS" ]; then
        podman exec -e PGPASSWORD="$PG_PASS" "$PG_CONT" psql -U "$PG_USER" -d "$PG_DB" -tAF'|' -c "$1" 2>/dev/null || true
    else
        podman exec "$PG_CONT" psql -U "$PG_USER" -d "$PG_DB" -tAF'|' -c "$1" 2>/dev/null || true
    fi
}

pg_query_raw() { # human-readable table form
    if [ "$PG_OK" -ne 1 ]; then echo "PostgreSQL not sampled"; return 0; fi
    if [ -n "$PG_PASS" ]; then
        podman exec -e PGPASSWORD="$PG_PASS" "$PG_CONT" psql -U "$PG_USER" -d "$PG_DB" -c "$1" 2>/dev/null || echo "query failed"
    else
        podman exec "$PG_CONT" psql -U "$PG_USER" -d "$PG_DB" -c "$1" 2>/dev/null || echo "query failed"
    fi
}

# ----------------------------------------------------------------------------
# Working dir / data dir / log dir / SQLite file resolution (lazy)
# ----------------------------------------------------------------------------
DATA_DIR=""
WORK_DIR=""
OC_MIRROR_LOG_DIR=""
SQLITE_DB=""
SYNCED_LOGS_DIR=""   # set after OUT exists; durable copy of <workingDir>/logs
GLOBAL_YAML="${ENCLAVE_DIR%/}/config/global.yaml"

resolve_work_dir() {
    [ -n "$WORK_DIR" ] && return 0
    if [ -n "$WORKING_DIR_OVERRIDE" ]; then
        WORK_DIR="${WORKING_DIR_OVERRIDE%/}"
        return 0
    fi
    if has python3 && [ -f "$GLOBAL_YAML" ]; then
        local wd
        wd=$(python3 -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])) or {}; print(d.get("workingDir",""))' "$GLOBAL_YAML" 2>/dev/null || true)
        [ -n "$wd" ] && WORK_DIR="${wd%/}"
    fi
    [ -n "$WORK_DIR" ]
}

resolve_paths() {
    resolve_work_dir || true
    [ -n "$WORK_DIR" ] || return 0
    if [ -z "$DATA_DIR" ] && [ -d "${WORK_DIR}/data" ]; then
        DATA_DIR="${WORK_DIR}/data"
    fi
    # oc-mirror writes its progress logs under <workingDir>/logs (see
    # playbooks/tasks/mirror_registry.yaml); mirror.sh logs land there too.
    if [ -z "$OC_MIRROR_LOG_DIR" ] && [ -d "${WORK_DIR}/logs" ]; then
        OC_MIRROR_LOG_DIR="${WORK_DIR}/logs"
    fi
    # For the SQLite backend, locate the largest *.sqlite/*.db file under the
    # working dir so we can track its growth (the SQLite analogue to watching
    # the PG connection pool). Best effort; NA until it appears.
    if [ "$DB_BACKEND" = "sqlite" ] && [ -z "$SQLITE_DB" ] && has find; then
        local search_root
        search_root="${WORK_DIR:-$DATA_DIR}"
        if [ -n "$search_root" ] && [ -d "$search_root" ]; then
            SQLITE_DB=$(find "$search_root" -type f \( -iname '*.sqlite' -o -iname '*.sqlite3' -o -iname '*.db' \) \
                -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -1 | cut -f2- || true)
        fi
    fi
}

# Incrementally mirror <workingDir>/logs into the report so oc-mirror / mirror.sh
# logs are preserved even if the playbook (or a later run) removes or rewrites
# them. -a keeps mtimes intact so the "created since probe start" filter in
# finalize still works; -u copies only newer files (cheap every tick).
sync_logs() {
    [ -n "$OC_MIRROR_LOG_DIR" ] && [ -d "$OC_MIRROR_LOG_DIR" ] || return 0
    [ -n "$SYNCED_LOGS_DIR" ] || return 0
    mkdir -p "$SYNCED_LOGS_DIR" 2>/dev/null || true
    cp -a -u "$OC_MIRROR_LOG_DIR"/. "$SYNCED_LOGS_DIR"/ 2>>"$ERRLOG" || true
}

# Container-dependent part of the one-time snapshot, deferred until the
# containers actually appear (so starting early still captures inspect/env).
capture_container_snapshot() {
    [ "$_container_snapshot_done" = 1 ] && return 0
    [ "${#MONITOR_CONTAINERS[@]}" -gt 0 ] || return 1
    local S="${OUT}/snapshot" cont
    podman ps -a >"$S/podman-ps.txt" 2>&1 || true
    # Image tags identify the mirror-registry version under test.
    podman ps --format '{{.Names}}\t{{.Image}}' >"$S/podman-images.txt" 2>&1 || true
    for cont in "${MONITOR_CONTAINERS[@]}"; do
        podman container exists "$cont" 2>/dev/null && \
            podman inspect "$cont" >"$S/inspect-${cont}.json" 2>&1 || true
    done
    # Quay worker-relevant environment (no secrets: only WORKER/GUNICORN/COUNT keys)
    if [ -n "$QUAY_APP" ]; then
        podman exec "$QUAY_APP" sh -c 'printenv 2>/dev/null | grep -Ei "worker|gunicorn|count|_conn|pool|sqlite|database" || true' \
            >"$S/quay-app-env.txt" 2>&1 || true
    fi
    if [ "$PG_OK" = 1 ]; then
        pg_query_raw "SELECT name, setting, unit FROM pg_settings WHERE name IN ('max_connections','superuser_reserved_connections','shared_buffers','work_mem','effective_cache_size','maintenance_work_mem') ORDER BY name;" \
            >"$S/pg-settings.txt" 2>&1 || true
    fi
    _container_snapshot_done=1
}

# Single lazy-detection pass: fill in anything not yet resolved and announce
# each piece exactly once. Safe to call every tick.
rescan() {
    detect_containers || true
    if [ "$DB_BACKEND" = "postgres" ] && [ "$PG_OK" != 1 ]; then
        if detect_pg; then
            PG_OK=1
            PG_MAX=$(pg_query "SHOW max_connections;" | head -1)
            [ -n "$PG_MAX" ] || PG_MAX="NA"
        fi
    fi
    resolve_paths

    if [ "$_announced_containers" = 0 ] && [ "${#MONITOR_CONTAINERS[@]}" -gt 0 ]; then
        info "detected containers: ${MONITOR_CONTAINERS[*]}"
        info "quay app container : ${QUAY_APP:-not found}"
        info "postgres container : ${PG_CONT:-none (SQLite / embedded backend assumed)}"
        _announced_containers=1
    fi
    if [ "$_announced_backend" = 0 ] && [ "$DB_BACKEND" != unknown ]; then
        info "db backend         : ${DB_BACKEND}"
        _announced_backend=1
    fi
    if [ "$_announced_pg" = 0 ] && [ "$PG_OK" = 1 ]; then
        info "PostgreSQL reachable as user='${PG_USER}' db='${PG_DB}' max_connections=${PG_MAX}"
        _announced_pg=1
    fi
    if [ "$_announced_datadir" = 0 ] && [ -n "$DATA_DIR" ]; then
        info "watching data dir  : $DATA_DIR"; _announced_datadir=1
    fi
    if [ "$_announced_logdir" = 0 ] && [ -n "$OC_MIRROR_LOG_DIR" ]; then
        info "working-dir logs   : $OC_MIRROR_LOG_DIR (synced into report)"; _announced_logdir=1
    fi
    if [ "$_announced_sqlite" = 0 ] && [ -n "$SQLITE_DB" ]; then
        info "watching sqlite db : $SQLITE_DB"; _announced_sqlite=1
    fi

    capture_container_snapshot || true
}

# ----------------------------------------------------------------------------
# Prepare output layout
# ----------------------------------------------------------------------------
mkdir -p "$OUT"/{snapshot,timeseries/pg_stat_activity,timeseries/podman_stats,timeseries/iostat,timeseries/pressure,logs/working-dir-logs}
ERRLOG="${OUT}/errors.log"; : >"$ERRLOG"
SYNCED_LOGS_DIR="${OUT}/logs/working-dir-logs"

# Host/DB time-series (one row per tick): the primary comparison artifact.
CSV="${OUT}/timeseries/samples.csv"
echo "ts_epoch,ts_iso,label,db_backend,load1,cpu_busy_pct,cpu_iowait_pct,mem_used_mb,mem_avail_mb,swap_used_mb,pg_conn_total,pg_conn_active,pg_conn_idle,pg_conn_idle_in_tx,pg_max_conn,pg_conn_pct,sqlite_db_mb,sqlite_wal_mb,gunicorn_workers,data_df_used_pct" >"$CSV"

# Per-container podman stats, tidy/long format: easy to pivot and diff per run.
PODMAN_CSV="${OUT}/timeseries/podman_stats.csv"
echo "ts_epoch,label,container,cpu_pct,mem_used_mb,mem_pct,block_read_mb,block_write_mb,net_rx_mb,net_tx_mb,pids" >"$PODMAN_CSV"

# Written at startup and rewritten at finalize so it reflects whatever lazy
# detection managed to resolve by the end of the run.
write_meta() {
    {
        echo "script: $SCRIPT_NAME"
        echo "started: $(date -Is -d "@${PROBE_START_EPOCH}" 2>/dev/null || date -Is 2>/dev/null || date)"
        echo "host: $(hostname 2>/dev/null)"
        echo "user: $(id -un 2>/dev/null) (uid $(id -u 2>/dev/null))"
        echo "label: ${LABEL:-none}"
        echo "interval_s: $INTERVAL"
        echo "duration_s: $DURATION (0 = until interrupted)"
        echo "enclave_dir: $ENCLAVE_DIR"
        echo "db_backend: $DB_BACKEND"
        echo "containers: ${MONITOR_CONTAINERS[*]:-none}"
        echo "quay_app: ${QUAY_APP:-none}"
        echo "work_dir: ${WORK_DIR:-unresolved}"
        echo "data_dir: ${DATA_DIR:-unresolved}"
        echo "sqlite_db: ${SQLITE_DB:-n/a}"
        echo "oc_mirror_log_dir: ${OC_MIRROR_LOG_DIR:-unresolved}"
        echo "pg_sampling: $([ "$PG_OK" = 1 ] && echo "yes (user=$PG_USER db=$PG_DB max_connections=$PG_MAX)" || echo no)"
    } >"${OUT}/meta.txt"
}
write_meta

# ----------------------------------------------------------------------------
# One-time capacity snapshot
# ----------------------------------------------------------------------------
info "collecting one-time snapshot ..."
S="${OUT}/snapshot"
{
    echo "=== uname ==="; uname -a 2>/dev/null || true
    echo; echo "=== /etc/redhat-release ==="; cat /etc/redhat-release 2>/dev/null || true
    echo; echo "=== nproc ==="; { has nproc && nproc; } 2>/dev/null || true
    echo; echo "=== uptime ==="; uptime 2>/dev/null || true
} >"$S/host.txt" 2>&1
{ has lscpu && lscpu; } >"$S/lscpu.txt" 2>&1 || true
cat /proc/cpuinfo >"$S/cpuinfo.txt" 2>&1 || true
{ has free && free -h; echo; cat /proc/meminfo; } >"$S/memory.txt" 2>&1 || true
{ has df && df -hT; } >"$S/df.txt" 2>&1 || true
{ has lsblk && lsblk -o NAME,SIZE,TYPE,ROTA,MOUNTPOINT,MODEL; } >"$S/lsblk.txt" 2>&1 || true
mount >"$S/mount.txt" 2>&1 || true
{ has numactl && numactl --hardware; } >"$S/numa.txt" 2>&1 || true
# Kernel/sysctl knobs that affect I/O and connection behaviour.
{ cat /proc/sys/fs/file-nr; echo; sysctl -a 2>/dev/null | grep -Ei 'vm.swappiness|vm.dirty|fs.file-max|net.core.somaxconn|net.ipv4.tcp_fin_timeout'; } >"$S/sysctl.txt" 2>&1 || true
podman version >"$S/podman-version.txt" 2>&1 || true
podman info >"$S/podman-info.txt" 2>&1 || true
# oc-mirror concurrency defaults in effect (for correlation)
cp "${ENCLAVE_DIR%/}/defaults/oc_mirror.yaml" "$S/oc_mirror.yaml" 2>/dev/null || true
if has python3 && [ -f "$GLOBAL_YAML" ]; then
    python3 -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])) or {}; print("quayBackend:", d.get("quayBackend")); print("workingDir:", d.get("workingDir"))' \
        "$GLOBAL_YAML" >"$S/global-relevant.txt" 2>&1 || true
fi
# Container/DB-dependent snapshot (podman ps/inspect, quay env, pg-settings) is
# captured by capture_container_snapshot() the moment the containers appear.

# ----------------------------------------------------------------------------
# Initial lazy detection (re-tried every tick in the sampling loop)
# ----------------------------------------------------------------------------
rescan
write_meta

if [ "${#MONITOR_CONTAINERS[@]}" -eq 0 ]; then
    warn "No mirror-registry containers detected yet."
    warn "Run this script as the user that installed mirror-registry / runs mirror.sh"
    warn "(rootless podman containers are per-user). Detection is retried every tick,"
    warn "so it is fine to start the probe before the registry is up."
fi
# When the registry is already up (the typical 1.3.11 case) a present-but-
# unsampleable PostgreSQL is a hard error unless --allow-degraded is given.
if [ "$DB_BACKEND" = "postgres" ] && [ "$PG_OK" != 1 ]; then
    warn "A PostgreSQL container ('${PG_CONT}') is present but could not be sampled."
    warn "This is the most important metric for the 1.3.11 investigation."
    if [ "$ALLOW_DEGRADED" -ne 1 ]; then
        err "Aborting. Re-run with --allow-degraded to collect host metrics only."
        exit 1
    fi
    warn "Continuing in degraded mode (host metrics only) as requested."
fi

# ----------------------------------------------------------------------------
# Finalize / package
# ----------------------------------------------------------------------------
FINALIZED=0
finalize() {
    [ "$FINALIZED" = 1 ] && return 0
    FINALIZED=1
    echo
    info "finalizing: collecting logs and packaging ..."
    # Final log sync + meta refresh so the report reflects the resolved state.
    sync_logs
    write_meta
    # container logs (whatever is present)
    for cont in "${MONITOR_CONTAINERS[@]}"; do
        podman logs --tail 3000 "$cont" >"${OUT}/logs/${cont}.log" 2>&1 || true
    done
    # oc-mirror progress logs (the mirror itself): read from the durable synced
    # copy (sync_logs preserves mtimes) and keep only the ones created since the
    # probe started so the error classification reflects THIS run and is not
    # contaminated by logs from prior runs. Reading the synced copy means the
    # logs survive even if the playbook / a later run removed the originals.
    oc_logs_captured=0
    oc_src_dir="${SYNCED_LOGS_DIR:-$OC_MIRROR_LOG_DIR}"
    if [ -n "$oc_src_dir" ] && [ -d "$oc_src_dir" ]; then
        mkdir -p "${OUT}/logs/oc-mirror" 2>/dev/null || true
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            cp "$f" "${OUT}/logs/oc-mirror/" 2>>"$ERRLOG" && oc_logs_captured=$((oc_logs_captured + 1))
        done < <(find "$oc_src_dir" -maxdepth 1 -type f -name 'oc-mirror*.log' \
                     -newermt "@${PROBE_START_EPOCH}" 2>/dev/null | sort | tail -20)
        info "captured ${oc_logs_captured} oc-mirror log(s) created since probe start"
    fi
    # kernel OOM / connection-slot evidence
    { dmesg -T 2>/dev/null | grep -iE 'oom|killed process|out of memory' || true; } >"${OUT}/logs/dmesg-oom.txt" 2>&1
    { journalctl -k --no-pager 2>/dev/null | grep -iE 'oom|killed process|out of memory' || true; } >>"${OUT}/logs/dmesg-oom.txt" 2>&1 || true

    # Summary / verdict
    local sum="${OUT}/SUMMARY.txt"
    {
        echo "mirror_registry_probe summary"
        echo "============================="
        echo "label: ${LABEL:-none}    db_backend: ${DB_BACKEND}"
        echo "host: $(hostname 2>/dev/null)   cpus: $({ has nproc && nproc; } 2>/dev/null || echo '?')"
        echo "samples: $(($(wc -l <"$CSV" 2>/dev/null || echo 1) - 1))   interval: ${INTERVAL}s"
        echo "pg max_connections: ${PG_MAX}"
        echo
        if [ "$(wc -l <"$CSV" 2>/dev/null || echo 1)" -gt 1 ]; then
            awk -F, 'NR>1 {
                if($11!="NA"&&$11+0>maxc){maxc=$11}
                if($16!="NA"&&$16+0>maxp){maxp=$16}
                if($5+0>maxl){maxl=$5}
                if($6!="NA"&&$6+0>maxcpu){maxcpu=$6}
                if($7!="NA"&&$7+0>maxio){maxio=$7}
                if(mina==""||($9!="NA"&&$9+0<mina)){mina=$9}
                if($10!="NA"&&$10+0>maxsw){maxsw=$10}
                if($17!="NA"&&$17+0>maxdb){maxdb=$17}
                if($19!="NA"&&$19+0>maxw){maxw=$19}
            } END {
                printf "peak pg connections : %s (%s%% of max)\n", (maxc==""?"NA":maxc), (maxp==""?"NA":sprintf("%.0f",maxp))
                printf "peak sqlite db size : %s MB\n", (maxdb==""?"NA":sprintf("%.1f",maxdb))
                printf "peak load1          : %.2f\n", maxl
                printf "peak cpu busy       : %.0f%%\n", maxcpu
                printf "peak cpu iowait     : %.0f%%\n", maxio
                printf "min mem available   : %s MB\n", (mina==""?"NA":mina)
                printf "peak swap used      : %s MB\n", (maxsw==""?"0":maxsw)
                printf "peak gunicorn workers: %s\n", (maxw==""?"NA":maxw)
            }' "$CSV"
        else
            echo "(no samples collected)"
        fi
        echo
        if [ "$DB_BACKEND" = "postgres" ] && [ -f "${OUT}/logs/${PG_CONT}.log" ]; then
            echo "PostgreSQL 'connection slot' / 'too many clients' errors in captured log window:"
            pg_err=$(grep -icE 'remaining connection slots|sorry, too many clients|too many connections' \
                "${OUT}/logs/${PG_CONT}.log" 2>/dev/null || true)
            echo "  matches: ${pg_err:-0}"
            echo
        fi
        if [ "$DB_BACKEND" = "sqlite" ] && [ -n "$QUAY_APP" ] && [ -f "${OUT}/logs/${QUAY_APP}.log" ]; then
            echo "SQLite contention errors in captured quay-app log window:"
            sq_err=$(grep -icE 'database is locked|database table is locked|OperationalError.*lock' \
                "${OUT}/logs/${QUAY_APP}.log" 2>/dev/null || true)
            echo "  database-locked matches: ${sq_err:-0}"
            echo
        fi
        oom_n=$(grep -icE 'oom|killed process|out of memory' "${OUT}/logs/dmesg-oom.txt" 2>/dev/null || true)
        echo "OOM events captured: ${oom_n:-0}"
        echo
        echo "oc-mirror errors (captured logs):"
        if [ "${oc_logs_captured:-0}" -gt 0 ]; then
            local oc_files=()
            while IFS= read -r f; do oc_files+=("$f"); done \
                < <(find "${OUT}/logs/oc-mirror" -maxdepth 1 -type f -name '*.log' 2>/dev/null)
            # destination-side: local Quay 500 on bearer token -> DB contention.
            # grep|wc -l (not grep -c) on purpose: -c prints one count per file,
            # we want the total across all captured logs. grep is wrapped in
            # `|| true` because a no-match exit 1 would, under `set -o pipefail`,
            # abort finalize() before packaging on a clean (0-error) run.
            # shellcheck disable=SC2126
            oc_dest=$({ grep -ahE 'at destination|bearer token|500 Internal' "${oc_files[@]}" 2>/dev/null || true; } | wc -l | tr -d ' ')
            # shellcheck disable=SC2126
            oc_src=$({ grep -ahE 'authentication required|manifest unknown|unauthorized' "${oc_files[@]}" 2>/dev/null || true; } | wc -l | tr -d ' ')
            # shellcheck disable=SC2126
            oc_slot=$({ grep -ahE 'remaining connection slots|too many clients|too many connections|database is locked' "${oc_files[@]}" 2>/dev/null || true; } | wc -l | tr -d ' ')
            printf '  destination-side (Quay 500 / bearer token) : %s\n' "${oc_dest:-0}"
            printf '  db contention (pg slots / sqlite locked)   : %s\n' "${oc_slot:-0}"
            printf '  source-side (upstream auth / manifest)     : %s\n' "${oc_src:-0}"
            echo
            echo "  distinct images that failed to mirror:"
            grep -ahoE 'error mirroring image [^ ]+' "${oc_files[@]}" 2>/dev/null \
                | sed 's/\x1b\[[0-9;]*m//g' | sort -u | sed 's/^/    /' || true
        else
            echo "  (no oc-mirror logs captured)"
        fi
    } >"$sum" 2>&1
    cat "$sum" || true

    echo "finished: $(date -Is 2>/dev/null || date)" >>"${OUT}/meta.txt"

    # Package
    local tarball="${OUT}.tgz"
    if tar czf "$tarball" -C "$(dirname "$OUT")" "$(basename "$OUT")" 2>>"$ERRLOG"; then
        info "report packaged: ${tarball}"
    else
        warn "tar failed; the raw report directory is at ${OUT}"
    fi
}

# ----------------------------------------------------------------------------
# Sampling loop
# ----------------------------------------------------------------------------
RUNNING=1
trap 'RUNNING=0' INT TERM
trap 'finalize' EXIT

# CPU accounting: read cumulative jiffies, diff across ticks.
read_cpu() { awk '/^cpu /{io=$6; idle=$5; t=0; for(i=2;i<=NF;i++)t+=$i; print t, idle, io; exit}' /proc/stat; }
read -r prev_total prev_idle prev_io < <(read_cpu) || { prev_total=0; prev_idle=0; prev_io=0; }

info "sampling started (Ctrl-C to stop)"
echo
start_epoch=$(date +%s)
tick=0
while [ "$RUNNING" = 1 ]; do
    now_epoch=$(date +%s)
    now_iso=$(date -Is 2>/dev/null || date +%Y-%m-%dT%H:%M:%S)

    # Lazy detection + incremental log preservation (cheap; no-ops once resolved).
    rescan
    sync_logs

    # --- CPU (delta over interval) ---
    read -r cur_total cur_idle cur_io < <(read_cpu) || { cur_total=$prev_total; cur_idle=$prev_idle; cur_io=$prev_io; }
    cpu_busy="NA"; cpu_io="NA"
    dtot=$((cur_total - prev_total))
    if [ "$dtot" -gt 0 ]; then
        didle=$((cur_idle - prev_idle)); dio=$((cur_io - prev_io))
        cpu_busy=$(awk -v t="$dtot" -v i="$didle" -v w="$dio" 'BEGIN{printf "%.1f", 100*(t-i-w)/t}')
        cpu_io=$(awk -v t="$dtot" -v w="$dio" 'BEGIN{printf "%.1f", 100*w/t}')
    fi
    prev_total=$cur_total; prev_idle=$cur_idle; prev_io=$cur_io

    # --- load ---
    load1=$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo NA)

    # --- memory (kB from /proc/meminfo) ---
    read -r mem_used mem_avail swap_used < <(awk '
        /^MemTotal:/{t=$2}/^MemAvailable:/{a=$2}/^SwapTotal:/{st=$2}/^SwapFree:/{sf=$2}
        END{printf "%d %d %d\n", (t-a)/1024, a/1024, (st-sf)/1024}' /proc/meminfo 2>/dev/null) \
        || { mem_used=NA; mem_avail=NA; swap_used=NA; }

    # --- PostgreSQL connections (1.3.11 only) ---
    pg_total=NA; pg_active=NA; pg_idle=NA; pg_idletx=NA; pg_pct=NA
    if [ "$PG_OK" = 1 ]; then
        line=$(pg_query "SELECT count(*), count(*) FILTER (WHERE state='active'), count(*) FILTER (WHERE state='idle'), count(*) FILTER (WHERE state='idle in transaction') FROM pg_stat_activity;" | head -1)
        if [ -n "$line" ]; then
            IFS='|' read -r pg_total pg_active pg_idle pg_idletx <<<"$line"
            if [ "$PG_MAX" != "NA" ] && [ -n "$pg_total" ]; then
                pg_pct=$(awk -v c="$pg_total" -v m="$PG_MAX" 'BEGIN{printf "%.0f", (m>0)?100*c/m:0}')
            fi
        fi
        # full per-tick detail + breakdown by application_name
        {
            echo "# $now_iso"
            pg_query_raw "SELECT count(*) AS conns, state, application_name FROM pg_stat_activity GROUP BY state, application_name ORDER BY conns DESC;"
            echo
            pg_query_raw "SELECT pid, usename, application_name, client_addr, state, wait_event_type, wait_event, date_trunc('second', now()-xact_start) AS xact_age, date_trunc('second', now()-query_start) AS query_age FROM pg_stat_activity ORDER BY xact_start NULLS LAST;"
        } >"${OUT}/timeseries/pg_stat_activity/tick-${now_epoch}.txt" 2>&1 || true
    fi

    # --- SQLite db size (2.0.12 only) ---
    sqlite_mb=NA; sqlite_wal_mb=NA
    if [ -n "$SQLITE_DB" ] && has stat; then
        sz=$(stat -c %s "$SQLITE_DB" 2>/dev/null || true)
        [ -n "$sz" ] && sqlite_mb=$(awk -v b="$sz" 'BEGIN{printf "%.3f", b/1048576}')
        wsz=$(stat -c %s "${SQLITE_DB}-wal" 2>/dev/null || true)
        [ -n "$wsz" ] && sqlite_wal_mb=$(awk -v b="$wsz" 'BEGIN{printf "%.3f", b/1048576}')
    fi

    # --- Quay gunicorn worker count (read via /proc, no ps dependency) ---
    workers=NA
    if [ -n "$QUAY_APP" ]; then
        workers=$(podman exec "$QUAY_APP" sh -c 'cat /proc/*/comm 2>/dev/null' 2>/dev/null | grep -c gunicorn) || workers=NA
    fi

    # --- data dir disk usage ---
    data_used=NA
    if [ -n "$DATA_DIR" ] && has df; then
        data_used=$(df -P "$DATA_DIR" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}') || data_used=NA
        [ -n "$data_used" ] || data_used=NA
    fi

    # --- append host/DB CSV ---
    echo "${now_epoch},${now_iso},${LABEL},${DB_BACKEND},${load1},${cpu_busy},${cpu_io},${mem_used},${mem_avail},${swap_used},${pg_total},${pg_active},${pg_idle},${pg_idletx},${PG_MAX},${pg_pct},${sqlite_mb},${sqlite_wal_mb},${workers},${data_used}" >>"$CSV"

    # --- per-container podman stats (raw tick file + tidy CSV rows) ---
    if [ "${#MONITOR_CONTAINERS[@]}" -gt 0 ]; then
        raw=$(podman stats --no-stream \
            --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.BlockIO}}|{{.NetIO}}|{{.PIDS}}' \
            "${MONITOR_CONTAINERS[@]}" 2>/dev/null || true)
        if [ -n "$raw" ]; then
            printf '# %s\n%s\n' "$now_iso" "$raw" >"${OUT}/timeseries/podman_stats/tick-${now_epoch}.txt" 2>/dev/null || true
            while IFS='|' read -r c cpu memu memp blk net pids; do
                [ -n "$c" ] || continue
                cpu=${cpu%\%}; memp=${memp%\%}
                memu_v=$(to_mb "$(printf '%s' "$memu" | awk -F' / ' '{print $1}')")
                blk_r=$(to_mb "$(printf '%s' "$blk" | awk -F' / ' '{print $1}')")
                blk_w=$(to_mb "$(printf '%s' "$blk" | awk -F' / ' '{print $2}')")
                net_r=$(to_mb "$(printf '%s' "$net" | awk -F' / ' '{print $1}')")
                net_w=$(to_mb "$(printf '%s' "$net" | awk -F' / ' '{print $2}')")
                echo "${now_epoch},${LABEL},${c},${cpu},${memu_v},${memp},${blk_r},${blk_w},${net_r},${net_w},${pids}" >>"$PODMAN_CSV"
            done <<<"$raw"
        fi
    fi

    # --- iostat + kernel pressure (best effort) ---
    if has iostat; then
        iostat -xdt 1 1 >"${OUT}/timeseries/iostat/tick-${now_epoch}.txt" 2>/dev/null || true
    fi
    if [ -d /proc/pressure ]; then
        { echo "# $now_iso";
          for p in cpu io memory; do echo "== $p =="; cat "/proc/pressure/$p" 2>/dev/null || true; done
        } >"${OUT}/timeseries/pressure/tick-${now_epoch}.txt" 2>&1 || true
    fi

    # --- live status line ---
    flag=""
    if [ "$DB_BACKEND" = "postgres" ]; then
        if [ "$pg_pct" != "NA" ] && [ "${pg_pct%.*}" -ge 80 ] 2>/dev/null; then flag="${c_red} <-- PG POOL HIGH${c_rst}"; fi
        printf '%s[%s]%s load=%s cpu=%s%% iowait=%s%% mem_used=%sM swap=%sM pg=%s/%s(act=%s idle=%s iot=%s) workers=%s%s\n' \
            "$c_dim" "$(date +%H:%M:%S)" "$c_rst" \
            "$load1" "$cpu_busy" "$cpu_io" "$mem_used" "$swap_used" \
            "$pg_total" "$PG_MAX" "$pg_active" "$pg_idle" "$pg_idletx" "$workers" "$flag"
    else
        printf '%s[%s]%s load=%s cpu=%s%% iowait=%s%% mem_used=%sM swap=%sM sqlite=%sMB(wal=%sMB) workers=%s\n' \
            "$c_dim" "$(date +%H:%M:%S)" "$c_rst" \
            "$load1" "$cpu_busy" "$cpu_io" "$mem_used" "$swap_used" \
            "$sqlite_mb" "$sqlite_wal_mb" "$workers"
    fi

    tick=$((tick + 1))
    if [ "$DURATION" -gt 0 ] && [ $((now_epoch - start_epoch)) -ge "$DURATION" ]; then
        info "duration reached; stopping"
        break
    fi

    # interruptible sleep
    for _ in $(seq 1 "$INTERVAL"); do
        [ "$RUNNING" = 1 ] || break
        sleep 1
    done
done

finalize

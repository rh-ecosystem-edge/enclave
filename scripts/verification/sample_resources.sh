#!/bin/bash
# In-flight resource sampler for E2E CI runs.
#
# Runs on the CI runner (the KVM hypervisor host) and periodically records
# resource usage and API latency from three vantage points, so transient
# stalls during the long deploy/operators phase can be diagnosed after the
# fact. The existing artifact collection only runs post-mortem, once the host
# has already recovered, and therefore misses the spike that caused the run to
# fail (SSH read timeouts to the Landing Zone, oc calls hitting their timeout).
#
#   host          - runner/hypervisor: memory, swap, load, io, qemu processes
#   landing-zone  - the LZ VM via SSH (best-effort, short timeout)
#   cluster       - the guest OpenShift API via SSH->oc on the LZ (call latency)
#
# Every remote probe is wrapped in `timeout`; a hang is recorded as a TIMEOUT
# marker (which is itself the signal we are hunting) instead of stalling the
# sampler. A failing probe never aborts the loop, so partial data always
# survives, including on the very failure we are chasing.
#
# Usage:
#   sample_resources.sh start [output_dir] [interval_seconds]
#   sample_resources.sh stop  [output_dir]
#
# `start` launches a detached background loop and returns immediately, writing
# its PID to <output_dir>/sampler.pid. `stop` signals that loop to exit and
# waits for it to flush. Point output_dir under the uploaded artifacts/ tree
# (default: artifacts/inflight) so the samples ship with the run artifacts.
#
# Note: `set -e` is intentionally omitted - a failing sample must never kill
# the loop; each probe handles its own errors instead.

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# SSH options for best-effort remote probes. BatchMode avoids any prompt that
# could hang the sampler; the per-probe `timeout` bounds the wall-clock cost.
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o BatchMode=yes"
LZ_USER="${LZ_USER:-cloud-user}"
LZ_KUBECONFIG="${LZ_KUBECONFIG:-/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig}"
# The LZ oc binary is under the session bin dir, not on the default non-login
# SSH PATH; without this every cluster probe fails with "oc: command not found".
LZ_OC_BIN="${LZ_OC_BIN:-/home/cloud-user/sessions/1/bin}"
SSH_PROBE_TIMEOUT="${SSH_PROBE_TIMEOUT:-15}"
OC_PROBE_TIMEOUT="${OC_PROBE_TIMEOUT:-25}"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# Sample the runner/hypervisor host itself (always reliable - local, no SSH).
sample_host() {
    local out="$1"
    {
        echo "===== HOST $(ts) ====="
        echo "--- loadavg ---"
        cat /proc/loadavg 2>&1
        echo "--- free -m ---"
        free -m 2>&1
        echo "--- meminfo (key) ---"
        grep -E 'MemFree|MemAvailable|SwapTotal|SwapFree|Committed_AS|Dirty|Writeback' /proc/meminfo 2>&1
        echo "--- vmstat (last line: si/so=swap, bi/bo=io, wa=iowait) ---"
        vmstat 1 2 2>&1 | tail -1
        echo "--- top (by cpu, header + first rows) ---"
        top -bn1 2>&1 | head -12
        echo "--- top processes by rss (qemu/virt/Runner) ---"
        ps -eo pid,user,rss,vsz,%cpu,%mem,comm --sort=-rss 2>&1 \
            | awk 'NR==1 || /qemu|virt|Runner/' | head -12
        echo "--- iostat ---"
        if command -v iostat >/dev/null 2>&1; then
            iostat -xd 1 2 2>&1 | tail -n +4
        else
            echo "iostat not available"
        fi
        echo
    } >> "$out" 2>&1
}

# Sample the Landing Zone VM over SSH. Best-effort: a hang becomes a TIMEOUT
# marker, which tells us the LZ (or the path to it) is what stalled.
sample_lz() {
    local out="$1" lz="$2" start end dur rc data
    {
        echo "===== LANDING-ZONE $(ts) (ip=${lz:-unknown}) ====="
    } >> "$out"
    if [ -z "$lz" ]; then
        { echo "  (no LZ IP resolved yet)"; echo; } >> "$out"
        return
    fi
    start=$(date +%s)
    # shellcheck disable=SC2086 # SSH_OPTS must word-split into separate flags
    if data=$(timeout "$SSH_PROBE_TIMEOUT" ssh $SSH_OPTS "${LZ_USER}@${lz}" \
        'echo "--- uptime ---"; uptime; echo "--- free -m ---"; free -m; echo "--- top ---"; top -bn1 | head -8' 2>&1); then
        end=$(date +%s); dur=$((end - start))
        { echo "  ssh_probe_seconds=${dur}"; echo "$data"; echo; } >> "$out"
    else
        rc=$?; end=$(date +%s); dur=$((end - start))
        if [ "$rc" -eq 124 ]; then
            { echo "  *** SSH PROBE TIMEOUT after ${dur}s (LZ unresponsive) ***"; echo; } >> "$out"
        else
            { echo "  *** SSH PROBE FAILED rc=${rc} after ${dur}s ***"; echo "$data"; echo; } >> "$out"
        fi
    fi
}

# Sample the guest OpenShift API via oc on the LZ, measuring call latency.
# Slow/timed-out oc here is exactly the exit-2 failure mode; the durations and
# TIMEOUT markers pinpoint when the API server became unresponsive.
sample_cluster() {
    local out="$1" lz="$2" start end dur rc data remote
    {
        echo "===== CLUSTER $(ts) (ip=${lz:-unknown}) ====="
    } >> "$out"
    if [ -z "$lz" ]; then
        { echo "  (no LZ IP resolved yet)"; echo; } >> "$out"
        return
    fi
    remote="export KUBECONFIG=${LZ_KUBECONFIG}; export PATH=${LZ_OC_BIN}:\$PATH;"
    remote+='if [ ! -f "$KUBECONFIG" ]; then echo "(kubeconfig not present yet)"; exit 0; fi;'
    remote+='echo "--- readyz latency ---"; { time oc get --raw /readyz; echo; } 2>&1;'
    remote+='echo "--- nodes ---"; oc get nodes --no-headers 2>&1;'
    remote+='echo "--- clusteroperators not Available=True ---"; oc get co --no-headers 2>&1 | awk "\$3!=\"True\"{print \$1,\$3,\$4,\$5}";'
    remote+='echo "--- etcd pods not Running ---"; oc -n openshift-etcd get pods --no-headers 2>&1 | grep -v " Running " | head;'
    remote+='echo "--- kube-apiserver pods not Running ---"; oc -n openshift-kube-apiserver get pods --no-headers 2>&1 | grep -v " Running " | head'
    start=$(date +%s)
    # shellcheck disable=SC2086 # SSH_OPTS must word-split into separate flags
    if data=$(timeout "$OC_PROBE_TIMEOUT" ssh $SSH_OPTS "${LZ_USER}@${lz}" "$remote" 2>&1); then
        end=$(date +%s); dur=$((end - start))
        { echo "  oc_probe_seconds=${dur}"; echo "$data"; echo; } >> "$out"
    else
        rc=$?; end=$(date +%s); dur=$((end - start))
        if [ "$rc" -eq 124 ]; then
            { echo "  *** OC PROBE TIMEOUT after ${dur}s (API/SSH unresponsive) ***"; echo; } >> "$out"
        else
            { echo "  *** OC PROBE FAILED rc=${rc} after ${dur}s ***"; echo "$data"; echo; } >> "$out"
        fi
    fi
}

# The sampling loop. Runs detached in the background until stop() drops the
# stop-flag. Re-resolves the LZ IP each iteration so it works even if the
# sampler is started before the LZ IP is available.
run_loop() {
    local out_dir="$1" interval="$2"
    local host_log="${out_dir}/host.log"
    local lz_log="${out_dir}/landing-zone.log"
    local cluster_log="${out_dir}/cluster.log"
    local stop_flag="${out_dir}/sampler.stop"
    local lz_ip waited

    mkdir -p "$out_dir"
    rm -f "$stop_flag"
    echo "sampler started pid=$$ interval=${interval}s at $(ts)" >> "${out_dir}/sampler.log"

    while [ ! -f "$stop_flag" ]; do
        lz_ip=$("${ENCLAVE_DIR}/scripts/utils/get_landing_zone_ip.sh" 2>/dev/null || true)
        sample_host "$host_log"
        sample_lz "$lz_log" "$lz_ip"
        sample_cluster "$cluster_log" "$lz_ip"
        # Interruptible sleep so stop() is responsive within ~1s.
        waited=0
        while [ "$waited" -lt "$interval" ] && [ ! -f "$stop_flag" ]; do
            sleep 1
            waited=$((waited + 1))
        done
    done

    echo "sampler stopped at $(ts)" >> "${out_dir}/sampler.log"
    rm -f "$stop_flag" "${out_dir}/sampler.pid"
}

start() {
    local out_dir="${1:-artifacts/inflight}" interval="${2:-15}"
    mkdir -p "$out_dir"

    if [ -f "${out_dir}/sampler.pid" ] && kill -0 "$(cat "${out_dir}/sampler.pid")" 2>/dev/null; then
        echo "resource sampler already running (pid $(cat "${out_dir}/sampler.pid"))"
        return 0
    fi

    # nohup + disown so the loop survives the end of this CI step.
    nohup "$0" __run "$out_dir" "$interval" >> "${out_dir}/sampler.boot.log" 2>&1 &
    local pid=$!
    disown "$pid" 2>/dev/null || true
    echo "$pid" > "${out_dir}/sampler.pid"
    echo "resource sampler started (pid ${pid}, out=${out_dir}, interval=${interval}s)"
}

stop() {
    local out_dir="${1:-artifacts/inflight}" pid n f
    if [ ! -f "${out_dir}/sampler.pid" ]; then
        echo "no sampler.pid in ${out_dir}; nothing to stop"
        return 0
    fi
    pid=$(cat "${out_dir}/sampler.pid")
    touch "${out_dir}/sampler.stop"

    # Grace period must exceed one full sampling iteration so the loop can flush
    # its logs and remove its PID file before we force-kill. The longest single
    # probe is the cluster oc probe (OC_PROBE_TIMEOUT, default 25s), plus SSH and
    # local collection; wait a bit past that.
    local grace=$((OC_PROBE_TIMEOUT + SSH_PROBE_TIMEOUT + 5))
    n=0
    while kill -0 "$pid" 2>/dev/null && [ "$n" -lt "$grace" ]; do
        sleep 1
        n=$((n + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "resource sampler did not exit gracefully; killing pid ${pid}"
        kill "$pid" 2>/dev/null || true
    fi
    echo "resource sampler stopped"

    for f in host landing-zone cluster; do
        if [ -f "${out_dir}/${f}.log" ]; then
            echo "  ${f}.log: $(wc -l < "${out_dir}/${f}.log") lines, $(grep -c 'TIMEOUT' "${out_dir}/${f}.log" 2>/dev/null || true) TIMEOUT markers"
        fi
    done
}

main() {
    local cmd="${1:-}"
    shift || true
    case "$cmd" in
        start) start "$@" ;;
        stop) stop "$@" ;;
        __run) run_loop "$@" ;; # internal: the detached loop entrypoint
        *)
            echo "Usage: $0 start [output_dir] [interval_seconds]" >&2
            echo "       $0 stop  [output_dir]" >&2
            exit 1
            ;;
    esac
}

main "$@"

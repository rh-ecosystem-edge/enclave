#!/usr/bin/env bash
# Re-run e2e jobs whose spot runner was reclaimed by AWS mid-job.
#
# Invoked by .github/workflows/e2e-spot-retry.yml when an e2e run completes with failure.
# Lists the run's failed jobs and, for each, maps its runner name (which IS the EC2 instance
# id on the AWS self-hosted fleet, e.g. i-0abc...) to an AWS spot-reclaim signal:
#   - ec2 DescribeInstances StateTransitionReason == "Server.SpotInstanceTermination"
#   - fallback (instance aged out of DescribeInstances ~1h): a CloudTrail BidEvictedEvent,
#     looked up both by ResourceName (this instance) and by EventName (recent evictions).
# An aged-out instance may surface either as an InvalidInstanceID.NotFound error OR as a
# successful call with an empty result set ("[]"); both route to the CloudTrail fallback, so a
# reclaim is never missed just because the detailed record expired before the run completed.
# Only the jobs with a confirmed spot signal are re-run, and each is re-run individually
# (never `gh run rerun --failed`, which would also re-run a genuinely-failed sibling job in
# the same run). A genuine test failure carries no spot signal, so it is left red.
#
# An AWS lookup error (permissions, throttling, service outage) is never treated as "no
# reclaim" — it fails the workflow so a real reclaim is not silently dropped.
#
# INSTRUMENTED: while we validate the detection heuristics this script logs, for each failed
# job, exactly what AWS returned (instance state/reason, CloudTrail events) and how far in the
# past the run failed. Set DEBUG=false to quiet it. Debug output includes EC2 instance ids
# (not sensitive) and, for context, raw runner names.
#
# Requires: gh (GH_TOKEN with actions:write) and aws (read-only role via OIDC).
# Env: RUN_ID, REPO (owner/name); optional DEBUG (default true).
set -euo pipefail

RUN_ID="${RUN_ID:?RUN_ID required}"
REPO="${REPO:?REPO required}"
DEBUG="${DEBUG:-true}"
readonly SPOT_REASON="Server.SpotInstanceTermination"

dbg() { [ "$DEBUG" = true ] && echo "[debug] $*" || true; }

# spot_reclaimed <instance-id>: 0 if the instance was spot-reclaimed, 1 if not. Exits the
# script (non-zero) if the spot signal cannot be checked, so an AWS error is never mistaken
# for a genuine test failure.
spot_reclaimed() {
  local id="$1" out rc details events_by_resource events_by_name

  echo "::group::spot check for instance ${id}"

  # 1) describe-instances — rich view for diagnosis (state + both reason fields).
  set +e
  details="$(aws ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[].Instances[].{state:State.Name,transition:StateTransitionReason,reasonCode:StateReason.Code,reasonMsg:StateReason.Message}' \
    --output json 2>&1)"
  rc=$?
  set -e
  dbg "describe-instances rc=${rc}"
  dbg "describe-instances output: ${details}"

  if [ "$rc" -eq 0 ]; then
    if grep -qF "$SPOT_REASON" <<< "$details"; then
      dbg "MATCH: describe-instances reports ${SPOT_REASON} for ${id}"
      echo "::endgroup::"
      return 0
    fi
    # A rc=0 result is only conclusive when it actually contains the instance. An instance that
    # has aged out of the detailed record returns rc=0 with an EMPTY result set ("[]"), NOT an
    # InvalidInstanceID.NotFound error — so an empty result must NOT be read as "not spot". Only
    # a populated record (a "state" field is present) with no spot reason is a genuine, non-spot
    # termination; the empty case falls through to CloudTrail, exactly like NotFound below.
    if grep -q '"state"' <<< "$details"; then
      dbg "no ${SPOT_REASON} for ${id} (instance still visible; not a spot termination)"
      echo "::endgroup::"
      return 1
    fi
    dbg "empty describe-instances result for ${id} (aged out ~1h); falling back to CloudTrail"
  else
    # describe-instances errored. Only a genuinely aged-out instance (InvalidInstanceID.NotFound)
    # is an expected miss we resolve via CloudTrail; any other error means the signal is
    # un-checkable -> fail loudly.
    if ! grep -q "InvalidInstanceID.NotFound" <<< "$details"; then
      echo "::endgroup::"
      echo "ERROR: describe-instances failed for ${id}: ${details}" >&2
      exit 1
    fi
    dbg "instance ${id} not in describe-instances (aged out ~1h); falling back to CloudTrail"
  fi

  # 2a) CloudTrail events for THIS instance (ResourceName) — targeted, small, scan for eviction.
  set +e
  events_by_resource="$(aws cloudtrail lookup-events \
    --lookup-attributes "AttributeKey=ResourceName,AttributeValue=${id}" \
    --query 'Events[].{name:EventName,time:EventTime}' --output json 2>&1)"
  rc=$?
  set -e
  dbg "cloudtrail ResourceName=${id} rc=${rc}: ${events_by_resource}"
  if [ "$rc" -ne 0 ]; then
    echo "::endgroup::"
    echo "ERROR: cloudtrail lookup-events (ResourceName) failed for ${id}: ${events_by_resource}" >&2
    exit 1
  fi

  # 2b) BidEvictedEvent(s) account-wide — scan for THIS instance id (original heuristic).
  # CloudTrail returns events newest-first; a fixed --max-items cap could scroll past the
  # runner's eviction on a busy fleet, so bound by TIME instead and let the CLI paginate the
  # window fully. A reclaim relevant to a just-failed run is recent, so a 1-day lookback is
  # ample while keeping the scan cheap.
  local lookback
  lookback="$(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%SZ)"
  set +e
  events_by_name="$(aws cloudtrail lookup-events \
    --lookup-attributes "AttributeKey=EventName,AttributeValue=BidEvictedEvent" \
    --start-time "$lookback" \
    --query 'Events[].CloudTrailEvent' --output text 2>&1)"
  rc=$?
  set -e
  dbg "cloudtrail EventName=BidEvictedEvent since ${lookback} rc=${rc} (output bytes: ${#events_by_name})"
  if [ "$rc" -ne 0 ]; then
    echo "::endgroup::"
    echo "ERROR: cloudtrail lookup-events (EventName) failed for ${id}: ${events_by_name}" >&2
    exit 1
  fi

  if grep -qF "BidEvictedEvent" <<< "$events_by_resource"; then
    dbg "MATCH: BidEvictedEvent for ${id} via ResourceName lookup"
    echo "::endgroup::"
    return 0
  fi
  if grep -qF "$id" <<< "$events_by_name"; then
    dbg "MATCH: ${id} appears in a recent BidEvictedEvent via EventName lookup"
    echo "::endgroup::"
    return 0
  fi

  dbg "no BidEvictedEvent evidence for ${id} in CloudTrail"
  echo "::endgroup::"
  return 1
}

# --- context: how stale is this run? (describe-instances retention ~1h, CloudTrail latency) ---
echo "::group::Context for run ${RUN_ID}"
dbg "now (UTC): $(date -u +%FT%TZ)"
if run_info="$(gh api "/repos/${REPO}/actions/runs/${RUN_ID}" \
  --jq '{status,conclusion,run_attempt,run_started_at,updated_at}' 2>&1)"; then
  dbg "run info: ${run_info}"
else
  dbg "could not fetch run info: ${run_info}"
fi
echo "::endgroup::"

# --- list failed jobs (id + runner) ---
echo "::group::Failed jobs for run ${RUN_ID}"
if ! failed_jobs="$(
  gh api --paginate "/repos/${REPO}/actions/runs/${RUN_ID}/jobs" \
    --jq '.jobs[] | select(.conclusion=="failure") | [.id, (.runner_name // "")] | @tsv'
)"; then
  echo "ERROR: could not list jobs for run ${RUN_ID}; cannot determine spot reclaim." >&2
  exit 1
fi
dbg "raw failed jobs (id<TAB>runner):"
dbg "${failed_jobs:-<none>}"
failed_ids="$(cut -f1 <<< "$failed_jobs" | paste -sd' ' -)"
echo "failed job ids: ${failed_ids:-(none)}"
echo "::endgroup::"

# Job ids whose runner was confirmed spot-reclaimed.
spot_job_ids=()
while IFS=$'\t' read -r job_id runner; do
  [ -n "$job_id" ] || continue
  dbg "job ${job_id}: runner=[${runner:-<none>}]"

  # Fleet runner name == EC2 instance id. Skip anything else (GitHub-hosted jobs, or a job
  # that failed before ever acquiring a runner).
  case "$runner" in
    i-*) ;;
    *)
      echo "job ${job_id}: non-instance runner (skipped)"
      continue
      ;;
  esac

  if spot_reclaimed "$runner"; then
    echo "job ${job_id}: spot reclaim confirmed"
    spot_job_ids+=("$job_id")
  else
    echo "job ${job_id}: no spot signal (treated as a genuine failure)"
  fi
done <<< "$failed_jobs"

if [ "${#spot_job_ids[@]}" -eq 0 ]; then
  echo "No spot reclaim among failed jobs — not retrying (treating as a genuine failure)."
  exit 0
fi

echo "Spot reclaim confirmed for job id(s): ${spot_job_ids[*]} — re-running them individually."
hard_error=false
for job_id in "${spot_job_ids[@]}"; do
  # Re-run just this job (and its dependents), not the whole run's failed set.
  set +e
  out="$(gh run rerun --job "$job_id" -R "$REPO" 2>&1)"
  rc=$?
  set -e
  dbg "gh run rerun --job ${job_id} rc=${rc}: ${out}"

  if [ "$rc" -eq 0 ]; then
    echo "re-ran job ${job_id}"
    continue
  fi

  # GitHub rejects a second re-run while the run is already re-running; that is expected when
  # more than one spot job shares a run — the rest are picked up on the next attempt (they stay
  # failed and still carry the spot signal), bounded by the workflow's run_attempt cap. Any
  # other rejection (permissions, invalid job id, ...) is a real failure the workflow must show.
  if grep -qiE "currently pending or in progress|already.*in progress" <<< "$out"; then
    echo "job ${job_id}: run already re-running; deferred to the next attempt"
  else
    echo "ERROR: could not re-run job ${job_id}: ${out}" >&2
    hard_error=true
  fi
done

if [ "$hard_error" = true ]; then
  exit 1
fi

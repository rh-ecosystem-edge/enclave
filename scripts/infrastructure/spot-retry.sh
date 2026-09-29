#!/usr/bin/env bash
# Re-run e2e jobs whose spot runner was reclaimed by AWS mid-job.
#
# Invoked by .github/workflows/e2e-spot-retry.yml when an e2e run completes with failure.
# Lists the run's failed jobs and, for each, maps its runner name (which IS the EC2 instance
# id on the AWS self-hosted fleet, e.g. i-0abc...) to an AWS spot-reclaim signal:
#   - ec2 DescribeInstances StateTransitionReason == "Server.SpotInstanceTermination"
#   - fallback (instance aged out of DescribeInstances ~1h): a CloudTrail BidEvictedEvent.
# Only the jobs with a confirmed spot signal are re-run, and each is re-run individually
# (never `gh run rerun --failed`, which would also re-run a genuinely-failed sibling job in
# the same run). A genuine test failure carries no spot signal, so it is left red — that is
# what keeps this quiet.
#
# The spot signal must be *checkable*: an AWS lookup error (permissions, throttling, service
# outage) is never treated as "no reclaim" — it fails the workflow so a real reclaim is not
# silently dropped. Runner names are not echoed raw (they may embed internal hostnames); only
# job ids and EC2 instance ids appear in the log.
#
# Requires: gh (GH_TOKEN with actions:write) and aws (read-only role via OIDC).
# Env: RUN_ID, REPO (owner/name).
set -euo pipefail

RUN_ID="${RUN_ID:?RUN_ID required}"
REPO="${REPO:?REPO required}"
readonly SPOT_REASON="Server.SpotInstanceTermination"

# spot_reclaimed <instance-id>: 0 if the instance was spot-reclaimed, 1 if not. Exits the
# script (non-zero) if the spot signal cannot be checked, so an AWS error is never mistaken
# for a genuine test failure.
spot_reclaimed() {
  local id="$1" out rc reason events

  set +e
  out="$(aws ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[].Instances[].StateTransitionReason' \
    --output text 2>&1)"
  rc=$?
  set -e

  if [ "$rc" -eq 0 ]; then
    reason="$out"
    case "$reason" in
      *"${SPOT_REASON}"*) return 0 ;;
      *) return 1 ;;
    esac
  fi

  # describe-instances failed. Only a genuinely aged-out instance (InvalidInstanceID.NotFound)
  # is an expected miss we resolve via CloudTrail; any other error (AccessDenied, throttling,
  # service outage) means the signal is un-checkable -> fail loudly.
  if ! grep -q "InvalidInstanceID.NotFound" <<< "$out"; then
    echo "ERROR: describe-instances failed for ${id}: ${out}" >&2
    exit 1
  fi

  # Aged out of DescribeInstances (~1h) -> fall back to CloudTrail BidEvictedEvent. Capture the
  # full response first: piping straight into `grep -q` lets grep close the pipe on first match,
  # which under `pipefail` fails the aws producer and would hide a real eviction.
  set +e
  events="$(aws cloudtrail lookup-events \
    --lookup-attributes "AttributeKey=EventName,AttributeValue=BidEvictedEvent" \
    --query 'Events[].CloudTrailEvent' --output text 2>&1)"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    echo "ERROR: cloudtrail lookup-events failed while checking ${id}: ${events}" >&2
    exit 1
  fi

  grep -qF "$id" <<< "$events"
}

# List failed jobs as `<job_id>\t<runner_name>` rows. Capture into a variable and check the
# call's exit status (set -e + the `if !`) so a lookup failure surfaces as an error instead of
# a silent "nothing to retry".
echo "::group::Failed jobs for run ${RUN_ID}"
if ! failed_jobs="$(
  gh api --paginate "/repos/${REPO}/actions/runs/${RUN_ID}/jobs" \
    --jq '.jobs[] | select(.conclusion=="failure") | [.id, (.runner_name // "")] | @tsv'
)"; then
  echo "ERROR: could not list jobs for run ${RUN_ID}; cannot determine spot reclaim." >&2
  exit 1
fi
# Log job ids only — runner names may embed internal hostnames (no-sensitive-data-in-logs).
failed_ids="$(cut -f1 <<< "$failed_jobs" | paste -sd' ' -)"
echo "failed job ids: ${failed_ids:-(none)}"
echo "::endgroup::"

# Job ids whose runner was confirmed spot-reclaimed.
spot_job_ids=()
while IFS=$'\t' read -r job_id runner; do
  [ -n "$job_id" ] || continue

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

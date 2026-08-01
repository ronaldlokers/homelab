#!/usr/bin/env bash
# Retries a command with exponential backoff.
#
# Every flate render pulls chart sources over the network, so a transient
# registry error fails the job even though the manifests are fine. Now that
# flux-diff is a required status check, that flake blocks the merge outright
# rather than just looking untidy, so the network-bound calls are wrapped.
#
# Invoked as `pr/.github/scripts/retry.sh` — flux-diff checks the repo out
# into `pr/` and `base/` rather than the workspace root, so there is no
# `.github/` at the cwd this runs from.
#
# Commands that write a render to a file must be passed as `bash -c '... > f'`
# so each attempt truncates the file itself; a redirect applied by the caller
# is opened once and would concatenate a failed attempt's partial output with
# the successful one's.

set -euo pipefail

max_attempts="${RETRY_MAX_ATTEMPTS:-3}"
delay="${RETRY_INITIAL_DELAY:-5}"
# Each attempt gets its own deadline. The common failure here is not a fast
# error but a *stall*: a chart fetch that never returns, which a plain retry
# loop would wait on forever and which would otherwise burn the whole job
# timeout for a single hung attempt. A render that normally takes ~15s and has
# gone quiet for 90s is not going to recover, so kill it and retry instead.
attempt_timeout="${RETRY_TIMEOUT:-90}"

for (( attempt = 1; attempt <= max_attempts; attempt++ )); do
  rc=0
  timeout --kill-after=10s "${attempt_timeout}" "$@" || rc=$?
  if (( rc == 0 )); then
    exit 0
  fi

  # 124 = timeout sent TERM, 137 = it had to follow up with KILL.
  if (( rc == 124 || rc == 137 )); then
    reason="timed out after ${attempt_timeout}s"
  else
    reason="exited ${rc}"
  fi

  if (( attempt == max_attempts )); then
    echo "::error::Failed after ${max_attempts} attempts (last: ${reason}): $*"
    exit "${rc}"
  fi

  echo "::warning::Attempt ${attempt}/${max_attempts} ${reason}, retrying in ${delay}s: $*"
  sleep "${delay}"
  delay=$(( delay * 2 ))
done

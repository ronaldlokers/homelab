#!/usr/bin/env bash
# Retries a command with exponential backoff.
#
# Every flate render pulls chart sources over the network, so a transient
# registry error fails the job even though the manifests are fine. Now that
# flux-diff is a required status check, that flake blocks the merge outright
# rather than just looking untidy, so the network-bound calls are wrapped.
#
# Commands that write a render to a file must be passed as `bash -c '... > f'`
# so each attempt truncates the file itself; a redirect applied by the caller
# is opened once and would concatenate a failed attempt's partial output with
# the successful one's.

set -euo pipefail

max_attempts="${RETRY_MAX_ATTEMPTS:-4}"
delay="${RETRY_INITIAL_DELAY:-5}"

for (( attempt = 1; attempt <= max_attempts; attempt++ )); do
  if "$@"; then
    exit 0
  fi

  if (( attempt == max_attempts )); then
    echo "::error::Failed after ${max_attempts} attempts: $*"
    exit 1
  fi

  echo "::warning::Attempt ${attempt}/${max_attempts} failed, retrying in ${delay}s: $*"
  sleep "${delay}"
  delay=$(( delay * 2 ))
done

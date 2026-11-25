#!/usr/bin/env bash
# Install (or dry-run install) one requirement from PyPI, retrying ONLY while the
# index is behind: uv's "no version of <requirement>" diagnostic, matched as a
# fixed string with the version literal. Any other failure ends at once.
#
#   install_with_retry.sh PYTHON REQUIREMENT [extra uv pip install args...]
#
# One elapsed-time budget covers every call that shares RETRY_DEADLINE (epoch
# seconds, set once per job), and the number of calls is bounded as well.
# Test seams: UV (the uv command), SLEEP_SECONDS, MAX_ATTEMPTS.
set -u

python="${1:?python}"
spec="${2:?requirement}"
shift 2
uv_bin="${UV:-uv}"
sleep_seconds="${SLEEP_SECONDS:-15}"
max_attempts="${MAX_ATTEMPTS:-40}"
deadline="${RETRY_DEADLINE:?RETRY_DEADLINE (epoch seconds) must be set once per job}"

# The diagnostic names the requirement as asked. For an extras form, the plain
# form (without the extras) is also an allowed spelling of the same lag.
allowed=("no version of ${spec}")
if [[ "${spec}" == *"["*"]"* ]]; then
  allowed+=("no version of ${spec%%\[*}${spec##*\]}")
fi

log="$(mktemp)"
trap 'rm -f "${log}"' EXIT
attempt=0
while :; do
  attempt=$((attempt + 1))
  if "${uv_bin}" pip install --python "${python}" --no-cache \
       --default-index https://pypi.org/simple/ "$@" "${spec}" >"${log}" 2>&1; then
    cat "${log}"
    echo "installed ${spec} (attempt ${attempt})"
    exit 0
  fi
  # The text is a literal (dots and brackets are not patterns) and must end at a
  # version boundary, so "no version of zeocore==0.55.4" never matches 0.55.40.
  lagging=0
  for text in "${allowed[@]}"; do
    escaped="$(printf '%s' "${text}" | sed 's/[][\\.^$*+?(){}|]/\\&/g')"
    if grep -qE -- "${escaped}([^0-9A-Za-z.+!_-]|\$)" "${log}"; then lagging=1; fi
  done
  if [ "${lagging}" -ne 1 ]; then
    cat "${log}"
    echo "::error::installing ${spec} failed for a reason other than index lag"
    exit 1
  fi
  now="$(date +%s)"
  if [ "${attempt}" -ge "${max_attempts}" ] || [ $((now + sleep_seconds)) -ge "${deadline}" ]; then
    cat "${log}"
    echo "::error::${spec} was still not installable when the retry budget ran out"
    exit 1
  fi
  sleep "${sleep_seconds}"
done

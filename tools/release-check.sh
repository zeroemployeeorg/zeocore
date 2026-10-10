#!/usr/bin/env bash
# tools/release-check.sh — the pre-tag gate for zeocore.
#
# Invoked by `make release-check`. KEEP_GOING, same as tools/doctor.sh:
# every check runs regardless of earlier failures, a running FAIL count is
# kept, and the script's exit code IS that count. This is the gate
# RULING-414 names as missing: the release sequence was blocked twice by
# facts nothing checked (a version literal that drifted from the tag, and
# a TestPyPI filename collision discovered only by attempting to publish).
#
# Usage: tools/release-check.sh <repo_root> <venv_python_path>
set -uo pipefail

REPO_ROOT="${1:?release-check.sh: missing repo_root argument}"
VENV_PYTHON="${2:?release-check.sh: missing venv_python_path argument}"

cd "$REPO_ROOT" || { echo "release-check: cannot cd to $REPO_ROOT"; exit 1; }

GREEN=$(tput -Txterm setaf 2 2>/dev/null || true)
YELLOW=$(tput -Txterm setaf 3 2>/dev/null || true)
RED=$(tput -Txterm setaf 1 2>/dev/null || true)
BLUE=$(tput -Txterm setaf 4 2>/dev/null || true)
RESET=$(tput -Txterm sgr0 2>/dev/null || true)

FAILS=0
CHECKS=0

pass() {
    CHECKS=$((CHECKS + 1))
    echo "  ${GREEN}✓${RESET} $1"
}

fail() {
    CHECKS=$((CHECKS + 1))
    FAILS=$((FAILS + 1))
    local name="$1" check="$2" fix="$3"
    echo "  ${RED}✗ ${name}${RESET}"
    echo "      ${YELLOW}checked:${RESET} ${check}"
    echo "      ${YELLOW}fix:${RESET}     ${fix}"
}

echo "${BLUE}zeocore release-check — is trunk ready to tag?${RESET}"
echo ""

PYPROJECT_VERSION=$(grep -E '^version = ' pyproject.toml | head -1 | sed -E 's/version = "(.*)"/\1/')
echo "  ${BLUE}pyproject.toml version: ${PYPROJECT_VERSION:-<unparseable>}${RESET}"
echo ""

# ------------------------------------------------------------------
# 1. PACKAGE IDENTITY: zeo_core.__version__ == pyproject.toml version.
#    (Same fact tests/test_package_init.py's
#    test_package_identity_the_shipped_artifact_must_agree_with_its_own_metadata
#    asserts, run here from the CLI so this gate does not require pytest to
#    have been run first.)
#
#    This is NOT an ordinary test failure if it goes red. RULING-414: real
#    PyPI 0.4.0 is LIVE TODAY shipping zeo_core.__version__ == "0.3.0"; the
#    package did not know its own version, and make verify's full 8-stage
#    gate passed over it anyway. A red here means DO NOT SHIP, full stop --
#    it is answered by fixing __version__'s derivation, never by rerunning
#    or ignoring the check.
# ------------------------------------------------------------------
if [ -z "$PYPROJECT_VERSION" ]; then
    fail "cannot read pyproject.toml version" \
         "grep -E '^version = ' pyproject.toml" \
         "pyproject.toml's [project] version line is missing or unparseable."
elif [ -x "$VENV_PYTHON" ]; then
    RUNTIME_VERSION=$("$VENV_PYTHON" -c "import zeo_core; print(zeo_core.__version__)" 2>/dev/null)
    METADATA_VERSION=$("$VENV_PYTHON" -c "import importlib.metadata as m; print(m.version('zeocore'))" 2>/dev/null)
    if [ -z "$RUNTIME_VERSION" ] || [ -z "$METADATA_VERSION" ]; then
        fail "could not read zeo_core.__version__ / installed metadata" \
             "${VENV_PYTHON} -c 'import zeo_core; print(zeo_core.__version__)'" \
             "Run 'make install' so zeocore is installed in this venv, then re-run."
    elif [ "$RUNTIME_VERSION" = "$PYPROJECT_VERSION" ] && [ "$METADATA_VERSION" = "$PYPROJECT_VERSION" ]; then
        pass "PACKAGE IDENTITY OK: pyproject=${PYPROJECT_VERSION}, zeo_core.__version__=${RUNTIME_VERSION}, installed metadata=${METADATA_VERSION} all agree"
    else
        fail "PACKAGE IDENTITY MISMATCH -- DO NOT SHIP (this is not an ordinary test failure)" \
             "pyproject=${PYPROJECT_VERSION} vs zeo_core.__version__=${RUNTIME_VERSION} vs installed metadata=${METADATA_VERSION} -- these must be IDENTICAL, not merely all present" \
             "This is the exact RULING-414 defect: the package does not know its own version. __version__ must be derived from importlib.metadata.version('zeocore') in src/zeo_core/__init__.py, never a hand-maintained literal. Reinstall ('make install') so the editable install picks up pyproject.toml's version, then re-run -- if it still disagrees after reinstalling, the derivation itself is broken or was reverted."
    fi
else
    fail "no virtual environment to check against" "test -x ${VENV_PYTHON}" \
         "Run 'make setup' first."
fi
echo ""

# ------------------------------------------------------------------
# 2. Floor agreement: reuse the pytest self-consistency suite (item 2)
#    rather than re-implementing its parsing here -- one source for
#    "what counts as agreement" between pyproject/CI/Makefile/docs.
# ------------------------------------------------------------------
if [ -x "$VENV_PYTHON" ]; then
    if "$VENV_PYTHON" -m pytest tests/test_release_consistency.py -q >/tmp/release-check-consistency.$$.log 2>&1; then
        pass "Python floor agrees across pyproject/CI/Makefile/CONTRIBUTING/QUICKSTART/README"
    else
        fail "Python floor DISAGREES somewhere" \
             "${VENV_PYTHON} -m pytest tests/test_release_consistency.py -q" \
             "Run that command directly to see which file is stale, then fix it. Full output: /tmp/release-check-consistency.$$.log"
    fi
    rm -f "/tmp/release-check-consistency.$$.log" 2>/dev/null || true
else
    fail "no virtual environment to check against" "test -x ${VENV_PYTHON}" \
         "Run 'make setup' first."
fi
echo ""

# ------------------------------------------------------------------
# 3. CHANGELOG has an entry for the current version.
# ------------------------------------------------------------------
if [ -z "$PYPROJECT_VERSION" ]; then
    fail "cannot check CHANGELOG" "pyproject version was unparseable (see check 1)" \
         "Fix check 1 first."
elif [ ! -f CHANGELOG.md ]; then
    fail "CHANGELOG.md missing" "test -f CHANGELOG.md" \
         "Create CHANGELOG.md (Keep a Changelog format is what this repo uses)."
elif grep -qE "^## \[${PYPROJECT_VERSION}\]" CHANGELOG.md; then
    pass "CHANGELOG.md has an entry for ${PYPROJECT_VERSION}"
else
    fail "CHANGELOG.md has NO entry for ${PYPROJECT_VERSION}" \
         "grep -E '^## \\[${PYPROJECT_VERSION}\\]' CHANGELOG.md -> no match" \
         "Add a '## [${PYPROJECT_VERSION}] - YYYY-MM-DD' section to CHANGELOG.md describing what shipped."
fi
echo ""

# ------------------------------------------------------------------
# 4. Target version not already on the index -- real PyPI AND TestPyPI.
#
#    RULING-415 §3b: a probe with no positive control cannot distinguish
#    "this version is absent" (404) from "I could not reach the index"
#    (any other status, including curl's 000 for a connection failure).
#    Sparring's first pass at this mapped every non-404 to TAKEN, which
#    would have reported a live version burned on a network hiccup.
#
#    Two amendments landed on top of the base design (RULING-415 §3b,
#    org PR #242), both load-bearing:
#
#    AMENDMENT 1 -- the control's OWN status is asserted, not assumed. A
#    wrongly-chosen control (e.g. using 0.5.0 against TestPyPI, which
#    never published it) returns 404 -- indistinguishable in form from a
#    correct "target is free" answer. So the control is a THIRD probe
#    into the same three-way {200, 404, else} classifier as the target:
#    it MUST resolve to exactly 200, or the whole check fails as
#    misconfigured rather than proceeding on a decorative control.
#
#    AMENDMENT 2 -- transient network failure must not be fatal on the
#    first bad response. Sparring saw both TestPyPI probes return 000
#    minutes after a clean run on the same machine; a hard fail on the
#    first non-{200,404} status would red the pipeline on ordinary
#    network noise, and a guard that cries wolf gets a `|| true` bolted
#    on at 2am and never removed -- a dead guard is worse than none. So
#    every probe (control AND target) retries with backoff before an
#    unexpected status is accepted as genuine. Only after retries are
#    exhausted does it become INCONCLUSIVE -- which still FAILS the
#    check (fail closed): INCONCLUSIVE is never silently mapped to
#    either FREE or TAKEN.
# ------------------------------------------------------------------
RETRY_ATTEMPTS=3
RETRY_WAIT_SECONDS=2  # doubles each retry: 2s, 4s -- brief, since this
                      # gate runs interactively/in CI, not unattended.

# probe_with_retry <url> -> prints the FINAL http status (200/404/other)
# to stdout after up to RETRY_ATTEMPTS tries, retrying only on a status
# that is neither 200 nor 404 (both of those are trustworthy answers on
# the first try; only an ambiguous status is worth spending retries on).
probe_with_retry() {
    local url="$1"
    local attempt=1
    local status wait_s=$RETRY_WAIT_SECONDS curl_exit
    while [ "$attempt" -le "$RETRY_ATTEMPTS" ]; do
        # curl already writes "000" to stdout AND exits non-zero on a
        # connection failure (verified: --max-time'd/unresolvable host ->
        # stdout "000", exit 6). `|| echo "000"` only fires when curl's
        # OWN invocation fails to run at all, which is a different,
        # rarer case -- for the connection-failure case both the `-w`
        # output and the `|| echo` fire, and command substitution
        # concatenates them into "000000" (6 chars), not a valid status.
        # Capture curl's exit status explicitly and only synthesize
        # "000" when curl's stdout is not already a clean 3-digit code,
        # so every return path yields exactly 3 characters.
        status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null)
        curl_exit=$?
        if [ "$curl_exit" -ne 0 ] || ! [[ "$status" =~ ^[0-9]{3}$ ]]; then
            status="000"
        fi
        if [ "$status" = "200" ] || [ "$status" = "404" ]; then
            echo "$status"
            return
        fi
        if [ "$attempt" -lt "$RETRY_ATTEMPTS" ]; then
            sleep "$wait_s"
            wait_s=$((wait_s * 2))
        fi
        attempt=$((attempt + 1))
    done
    echo "$status"  # exhausted retries; report the last status seen
}

# pick_control_version <json_url_base> <target_version> <changelog_versions...>
# -> prints a version to use as the control, or nothing if none could be
#    confirmed live.
#
# A control is only proof of reachability if it (a) is NOT the version
# under test and (b) is independently confirmed live on THIS index, right
# now -- a hardcoded literal satisfies neither guarantee forever. Walk
# CHANGELOG.md's version history (already newest-first, Keep a Changelog
# order -- the same file and order check 3 above already depends on),
# skip the target itself, and probe each older release in turn until one
# is confirmed 200. This self-heals as releases accumulate instead of
# drifting into a hardcoded value that eventually collides with the
# target -- which is exactly what happened here: the control was a
# literal "0.6.0" and the 0.6.0 release made it, silently, the same
# version as the target it was meant to independently verify against.
pick_control_version() {
    local json_url_base="$1" target_version="$2"
    shift 2
    local candidate status
    for candidate in "$@"; do
        if [ "$candidate" = "$target_version" ]; then
            continue
        fi
        status=$(probe_with_retry "${json_url_base}/${candidate}/json")
        if [ "$status" = "200" ]; then
            echo "$candidate"
            return
        fi
    done
    # none found; caller treats an empty result as control failure
}

check_index() {
    local index_name="$1" json_url_base="$2" control_version="$3" target_version="$4"

    # A control equal to the target proves nothing about reachability --
    # it would just be probing the target twice and calling the second
    # probe a "control". This must never happen silently; refuse instead
    # of running a degenerate check that reads exactly like a real one.
    if [ -z "$control_version" ]; then
        fail "${index_name}: no control version available for ${target_version}" \
             "pick_control_version found no CHANGELOG.md entry other than ${target_version} that is confirmed live on ${index_name}" \
             "This index has no prior release to probe as a control (first-ever publish?), or every candidate failed to resolve. Check network/DNS, or verify manually: GET ${json_url_base}/<some known-published version>/json -> 200."
        return
    fi
    if [ "$control_version" = "$target_version" ]; then
        fail "${index_name}: control version equals target version (${target_version})" \
             "pick_control_version returned the target itself, which is a caller bug -- the target must be excluded before a candidate is ever probed" \
             "This is exactly the degenerate case Amendment 1 forbids: a control that IS the target proves nothing about reachability. Fix pick_control_version's exclusion of target_version; do not bypass this check."
        return
    fi

    local control_status
    control_status=$(probe_with_retry "${json_url_base}/${control_version}/json")

    if [ "$control_status" != "200" ]; then
        fail "${index_name}: cannot verify availability of ${target_version} (control failed)" \
             "control probe: GET ${json_url_base}/${control_version}/json -> ${control_status} after ${RETRY_ATTEMPTS} attempts (expected 200 -- ${control_version} is known to already exist on ${index_name})" \
             "The control never returned 200, so a 404 on the target would be INDISTINGUISHABLE from a network failure or a wrongly-chosen control. If this control version genuinely is not on ${index_name}, that is a bug in this script (wrong control per-index) -- fix the control, don't bypass the check. Otherwise check your network/DNS/proxy and re-run."
        return
    fi

    local target_status
    target_status=$(probe_with_retry "${json_url_base}/${target_version}/json")

    case "$target_status" in
        404)
            pass "${index_name}: ${target_version} is FREE (control ${control_version} confirmed reachable: 200; target: 404)"
            ;;
        200)
            fail "${index_name}: ${target_version} is ALREADY PUBLISHED" \
                 "GET ${json_url_base}/${target_version}/json -> 200 (control ${control_version} also 200, so this probe is trustworthy)" \
                 "This is the exact RULING-414 TestPyPI collision: an index never re-accepts a filename, even after deletion. Bump pyproject.toml's version to one that is actually free on ${index_name}, or if this was intentional, this release cannot use ${target_version} on ${index_name}."
            ;;
        *)
            fail "${index_name}: INCONCLUSIVE for ${target_version}" \
                 "GET ${json_url_base}/${target_version}/json -> ${target_status} after ${RETRY_ATTEMPTS} attempts (neither 404 nor 200; control ${control_version} was 200, so the index IS reachable but this specific response would not resolve)" \
                 "Do not treat this as free or taken -- this check FAILS closed on purpose. Re-run later, or query ${json_url_base}/${target_version}/json by hand and read the response before tagging."
            ;;
    esac
}

if [ -z "$PYPROJECT_VERSION" ]; then
    fail "cannot check index availability" "pyproject version was unparseable (see check 1)" \
         "Fix check 1 first."
else
    # CONTROLS ARE DERIVED, NEVER HARDCODED (fixed 2026-09-01). The
    # previous form used fixed literals ("0.5.0" for real PyPI, "0.6.0"
    # for TestPyPI) chosen by a live probe at the time they were written.
    # That is exactly the kind of constant that goes stale: the moment
    # PYPROJECT_VERSION itself reached "0.6.0", the TestPyPI control
    # silently became the SAME STRING as the target it was meant to
    # independently verify against -- provably degenerate, not merely
    # theoretically so (see the release-smoke-verifies-nothing SOW chain
    # for the live trace: both probes hit the identical URL).
    #
    # Instead, read CHANGELOG.md's version history (already newest-first,
    # Keep a Changelog order -- the same file and order check 3 already
    # depends on) and hand the ordered list to pick_control_version,
    # which excludes the target and probes candidates until one is
    # confirmed live on THAT index. Real PyPI and TestPyPI genuinely
    # differ in publish history (0.3.0-0.5.0 never reached TestPyPI --
    # verified live 2026-09-01), so each index picks its own control
    # independently rather than sharing one value.
    # A while-read loop rather than `mapfile` -- mapfile is bash 4+ only
    # and macOS ships bash 3.2 by default, which this script must still
    # run under for a developer's local `make release-check`.
    CHANGELOG_VERSIONS=()
    while IFS= read -r line; do
        CHANGELOG_VERSIONS+=("$line")
    done < <(grep -oE '^## \[[0-9]+\.[0-9]+\.[0-9]+\]' CHANGELOG.md | sed -E 's/^## \[(.*)\]$/\1/')

    PYPI_CONTROL=$(pick_control_version "https://pypi.org/pypi/zeocore" "$PYPROJECT_VERSION" "${CHANGELOG_VERSIONS[@]}")
    check_index "real PyPI" "https://pypi.org/pypi/zeocore" "$PYPI_CONTROL" "$PYPROJECT_VERSION"

    TESTPYPI_CONTROL=$(pick_control_version "https://test.pypi.org/pypi/zeocore" "$PYPROJECT_VERSION" "${CHANGELOG_VERSIONS[@]}")
    check_index "TestPyPI" "https://test.pypi.org/pypi/zeocore" "$TESTPYPI_CONTROL" "$PYPROJECT_VERSION"
fi
echo ""

# ------------------------------------------------------------------
# 5. uv.lock agrees with pyproject.toml.
#
#    0.12.0 shipped with uv.lock still recording zeocore 0.11.0, so
#    `uv lock --check` failed on the released trunk. Nothing in the tag
#    gate looked at the lockfile; the zeocore elders ruled on org issue
#    #787 (Z2) that release preparation must, so this cannot recur.
#    `uv lock --check` is the authoritative test (it re-derives the lock
#    from pyproject without writing); the version line is reported
#    alongside so the failure says WHAT disagrees, not only that
#    something does.
# ------------------------------------------------------------------
LOCK_VERSION=$(awk '/^name = "zeocore"$/{getline; if ($1=="version") {gsub(/"/,"",$3); print $3}; exit}' uv.lock 2>/dev/null)
if ! command -v uv >/dev/null 2>&1; then
    fail "uv not found -- cannot check uv.lock" "command -v uv" \
         "Install uv (https://docs.astral.sh/uv/), then re-run."
elif [ ! -f uv.lock ]; then
    fail "uv.lock missing" "test -f uv.lock" "Run 'uv lock' and commit uv.lock."
elif LOCK_CHECK_OUTPUT=$(uv lock --check 2>&1); then
    pass "uv.lock is consistent with pyproject.toml (records zeocore ${LOCK_VERSION:-?})"
elif printf '%s' "$LOCK_CHECK_OUTPUT" | grep -q "needs to be updated"; then
    # uv exits 1 for a stale lock and for a resolver that could not run
    # (no network, a cache miss offline), so only uv's own message says
    # which. Anything else is reported as the check not running.
    fail "uv.lock is out of date (records zeocore ${LOCK_VERSION:-<unparseable>}, pyproject says ${PYPROJECT_VERSION:-<unparseable>})" \
         "uv lock --check" \
         "Run 'uv lock', check that the diff changes only what you expect, and commit uv.lock."
else
    fail "uv lock --check could not run: $(printf '%s' "$LOCK_CHECK_OUTPUT" | tail -n 5 | tr -s ' \n' ' ')" \
         "uv lock --check" \
         "This is not a verdict on uv.lock. Fix what uv reports (often the network or the package index), then re-run."
fi
echo ""

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo "${BLUE}────────────────────────────────────────────${RESET}"
if [ "$FAILS" -eq 0 ]; then
    echo "${GREEN}✓ All ${CHECKS} release checks passed. Trunk is ready to tag v${PYPROJECT_VERSION}.${RESET}"
else
    echo "${RED}✗ ${FAILS} of ${CHECKS} release checks failed.${RESET} Fix each ${RED}✗${RESET} above before tagging."
fi

exit "$FAILS"

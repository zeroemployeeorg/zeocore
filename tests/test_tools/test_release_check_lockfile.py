"""The uv.lock check in tools/release-check.sh tells a stale lock from a check
that could not run (Architect review of #84).

uv exits 1 both for a lock that needs updating and for a resolver that
could not run, such as with no network or a cold cache offline. Only uv's
own message tells them apart, so the check reads it. These tests extract the
live section from the script, as test_release_check_probe does, and run it
against a fake ``uv`` on ``PATH``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RELEASE_CHECK = REPO_ROOT / "tools" / "release-check.sh"

STALE = (
    "The lockfile at `uv.lock` needs to be updated, but `--check` was"
    " provided. To update the lockfile, run `uv lock`."
)
OFFLINE = "hint: Packages were unavailable because the network was disabled."


def _lock_section() -> str:
    text = RELEASE_CHECK.read_text()
    match = re.search(r"^LOCK_VERSION=.*?^fi$", text, re.MULTILINE | re.DOTALL)
    assert match, "the uv.lock section was not found in release-check.sh"
    return match.group(0)


def _run(tmp_path: Path, *, exit_code: int, output: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(f"#!/bin/sh\necho '{output}' >&2\nexit {exit_code}\n")
    fake_uv.chmod(0o755)
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "zeocore"\nversion = "0.12.0"\n'
    )
    script = tmp_path / "section.sh"
    script.write_text(
        "set -uo pipefail\n"
        'pass() { echo "PASS: $1"; }\n'
        'fail() { echo "FAIL: $1 | $3"; }\n'
        "PYPROJECT_VERSION=0.12.0\n" + _lock_section() + "\n"
    )
    bash = shutil.which("bash")
    assert bash is not None
    proc = subprocess.run(  # noqa: S603 -- fixed argv, test-only
        [bash, str(script)],
        cwd=tmp_path,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def test_a_consistent_lock_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, exit_code=0, output="Resolved").startswith(
        "PASS: uv.lock is consistent with pyproject.toml (records zeocore 0.12.0)"
    )


def test_a_stale_lock_is_reported_as_out_of_date(tmp_path: Path) -> None:
    out = _run(tmp_path, exit_code=1, output=STALE)
    assert out.startswith("FAIL: uv.lock is out of date")
    assert "Run 'uv lock'" in out


@pytest.mark.parametrize("output", [OFFLINE, "error: Failed to fetch: index"])
def test_a_check_that_could_not_run_is_never_called_a_stale_lock(
    tmp_path: Path, output: str
) -> None:
    out = _run(tmp_path, exit_code=1, output=output)
    assert out.startswith("FAIL: uv lock --check could not run:")
    assert output.split(":")[0] in out
    assert "out of date" not in out
    assert "not a verdict on uv.lock" in out

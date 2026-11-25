"""The smoke test's index-lag retry (`.github/release/install_with_retry.sh`).

A fake `uv` stands in for the installer, so nothing here needs the network. The
script retries only on uv's "no version of <requirement>" diagnostic, inside one
elapsed-time budget and a bounded number of calls.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "release" / "install_with_retry.sh"

FAKE_UV = """#!/usr/bin/env bash
count=$(cat "$FAKE_UV_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$FAKE_UV_COUNT"
if [ "$count" -le "$FAKE_UV_FAILURES" ]; then
  printf '%s\\n' "$FAKE_UV_DIAGNOSTIC" >&2
  exit 1
fi
echo "installed"
exit 0
"""

PLAIN = (
    "  x No solution found when resolving dependencies:\n"
    "  `-> Because there is no version of zeocore==0.55.4 and you require "
    "zeocore==0.55.4, we can conclude that your requirements are unsatisfiable."
)
EXTRAS = (
    "  x No solution found when resolving dependencies:\n"
    "  `-> Because there is no version of zeocore[runtime-host]==0.55.4 and you "
    "require zeocore[runtime-host]==0.55.4, we can conclude that your "
    "requirements are unsatisfiable."
)
OTHER_VERSION = PLAIN.replace("0.55.4", "0.55.40")
OTHER_ERROR = "error: Failed to download distribution due to network timeout"


def _run(
    tmp_path: Path,
    *,
    spec: str,
    diagnostic: str,
    failures: int,
    deadline_offset: int = 600,
    max_attempts: int = 40,
) -> tuple[subprocess.CompletedProcess[str], int]:
    uv = tmp_path / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    count = tmp_path / "count"
    env = {
        **os.environ,
        "UV": str(uv),
        "SLEEP_SECONDS": "0",
        "MAX_ATTEMPTS": str(max_attempts),
        "RETRY_DEADLINE": str(int(time.time()) + deadline_offset),
        "FAKE_UV_COUNT": str(count),
        "FAKE_UV_FAILURES": str(failures),
        "FAKE_UV_DIAGNOSTIC": diagnostic,
    }
    result = subprocess.run(  # noqa: S603 - the repository's own script
        ["bash", str(SCRIPT), "/tmp/venv/bin/python", spec],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    calls = int(count.read_text()) if count.exists() else 0
    return result, calls


@pytest.mark.parametrize(
    ("spec", "diagnostic"),
    [("zeocore==0.55.4", PLAIN), ("zeocore[runtime-host]==0.55.4", EXTRAS)],
)
def test_it_retries_the_lag_diagnostic_of_each_requirement_form(
    tmp_path: Path, spec: str, diagnostic: str
) -> None:
    result, calls = _run(tmp_path, spec=spec, diagnostic=diagnostic, failures=3)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == 4


def test_the_extras_form_may_be_reported_in_its_plain_spelling(
    tmp_path: Path,
) -> None:
    result, calls = _run(
        tmp_path, spec="zeocore[runtime-host]==0.55.4", diagnostic=PLAIN, failures=2
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == 3


def test_another_version_is_not_index_lag(tmp_path: Path) -> None:
    # "no version of zeocore==0.55.40" must not match a wait for 0.55.4.
    result, calls = _run(
        tmp_path, spec="zeocore==0.55.4", diagnostic=OTHER_VERSION, failures=5
    )
    assert result.returncode == 1
    assert calls == 1


def test_any_other_failure_ends_at_once(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, spec="zeocore==0.55.4", diagnostic=OTHER_ERROR, failures=5
    )
    assert result.returncode == 1
    assert calls == 1
    assert "other than index lag" in result.stdout


def test_the_elapsed_time_budget_ends_the_retries(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        spec="zeocore==0.55.4",
        diagnostic=PLAIN,
        failures=50,
        deadline_offset=-1,
    )
    assert result.returncode == 1
    assert calls == 1
    assert "retry budget" in result.stdout


def test_the_number_of_calls_is_bounded(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, spec="zeocore==0.55.4", diagnostic=PLAIN, failures=50, max_attempts=3
    )
    assert result.returncode == 1
    assert calls == 3


def test_the_deadline_is_required(tmp_path: Path) -> None:
    uv = tmp_path / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "RETRY_DEADLINE"}
    env["UV"] = str(uv)
    result = subprocess.run(  # noqa: S603 - the repository's own script
        ["bash", str(SCRIPT), "/tmp/venv/bin/python", "zeocore==0.55.4"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0

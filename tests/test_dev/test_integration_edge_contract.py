"""zeocore-SOW-36 slice 0: the contract that keeps zeocore from importing the
integrations that move is real, not vacuous.

import-linter skips any source/forbidden pair where one contains the other, so
a contract written with the bare root package as its source checks nothing and
passes. These tests run the real linter on mutated copies of ``.importlinter``
and require the mutations to fail.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".importlinter"
CONTRACT = "sow36-core-does-not-import-moving-integrations"


def _lint(config: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "importlinter.ini"
    path.write_text(config)
    exe = Path(sys.executable).parent / "lint-imports"
    cmd = [str(exe)] if exe.exists() else [sys.executable, "-m", "importlinter", "lint"]
    return subprocess.run(  # noqa: S603 - our own linter on our own config
        [*cmd, "--config", str(path), "--contract", CONTRACT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _section(text: str) -> str:
    start = text.index(f"[importlinter:contract:{CONTRACT}]")
    nxt = text.find("\n[importlinter:contract:", start + 1)
    return text[start : nxt if nxt != -1 else len(text)]


def _ignore_lines(section: str) -> list[str]:
    block = section.split("ignore_imports =", 1)[1]
    return [ln for ln in block.splitlines() if "->" in ln]


def test_the_real_config_keeps_the_contract(tmp_path: Path) -> None:
    result = _lint(CONFIG.read_text(), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_sources_are_wildcards_never_the_bare_root() -> None:
    sources = re.search(
        r"source_modules =\n((?:    .+\n)+)", _section(CONFIG.read_text())
    )
    assert sources is not None
    assert [s.strip() for s in sources.group(1).splitlines()] == [
        "zeo_core.*",
        "zeo_core.integrations.*",
    ]


def test_a_listed_edge_that_is_removed_from_the_list_fails(tmp_path: Path) -> None:
    text = CONFIG.read_text()
    line = _ignore_lines(_section(text))[0]
    result = _lint(text.replace(line + "\n", "", 1), tmp_path)
    assert result.returncode != 0
    assert "BROKEN" in result.stdout


def test_a_listed_edge_that_no_longer_exists_fails(tmp_path: Path) -> None:
    text = CONFIG.read_text()
    first = _ignore_lines(_section(text))[0]
    stale = "    zeo_core.cli.release -> zeo_core.integrations.notion.upsert"
    result = _lint(text.replace(first, first + "\n" + stale, 1), tmp_path)
    assert result.returncode != 0


def test_a_bare_root_source_would_check_nothing(tmp_path: Path) -> None:
    """The reason for the wildcard rule: with the root as source, the contract
    passes even after every listed edge is removed."""
    text = CONFIG.read_text()
    section = _section(text)
    vacuous = re.sub(
        r"ignore_imports =.*",
        "",
        section.replace(
            "    zeo_core.*\n    zeo_core.integrations.*\n", "    zeo_core\n"
        ),
        flags=re.S,
    )
    result = _lint(text.replace(section, vacuous), tmp_path)
    assert result.returncode == 0


def test_the_stale_edge_is_reported_by_the_linter_not_by_a_parse_error(
    tmp_path: Path,
) -> None:
    text = CONFIG.read_text()
    first = _ignore_lines(_section(text))[0]
    stale = "    zeo_core.cli.release -> zeo_core.integrations.notion.upsert"
    result = _lint(text.replace(first, first + "\n" + stale, 1), tmp_path)
    assert "No matches for ignored import" in result.stdout + result.stderr


def test_the_root_contract_also_forbids_a_submodule_of_a_moving_integration() -> None:
    text = CONFIG.read_text()
    start = text.index("[importlinter:contract:sow36-package-roots-do-not")
    section = text[start:]
    assert "zeo_core.integrations.github.**" in section


def test_the_old_app_tokens_path_is_the_moved_module() -> None:
    from zeo_core.integrations.github import app_tokens as old
    from zeo_core.release import app_tokens as new

    assert old.Access is new.Access
    assert old.InstallationTokens is new.InstallationTokens
    assert old._b64url is new._b64url  # a private helper still resolves
    try:
        old.no_such_name  # noqa: B018
    except AttributeError:
        pass
    else:
        raise AssertionError("an unknown name did not raise")


def _grepban(src: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own make target
        ["make", "-s", "integration-edge-strings", f"PKG_SRC={src}"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_grep_ban_fails_on_a_planted_string_and_on_a_missing_tree(
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "a.py").write_text('X = "zeo_core.release.app_tokens"\n')
    assert _grepban(clean).returncode == 0
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "a.py").write_text('X = "zeo_core.integrations.hubspot.client"\n')
    result = _grepban(planted)
    assert result.returncode != 0
    assert "names a module of an integration that moves" in result.stdout
    missing = _grepban(tmp_path / "nowhere")
    assert missing.returncode != 0

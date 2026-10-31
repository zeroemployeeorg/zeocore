"""Public text holds no internal id.

This repository is public. A fixture, a doc or a source comment must not carry a
team's seat name, a work-order id, a message id, a machine name or a pasted internal
note: use fake data. The patterns refuse the ids themselves, so a real note cannot
slip in by being reworded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

#: A new fixture folder with data copied from real work is checked by adding it here.
FIXTURE_DIRS = (Path(__file__).parent / "fixtures" / "intervention_cards",)

#: Files written for one feature whose prose is checked for the weaker rules.
FEATURE_FILES = (
    ROOT / "src" / "zeo_core" / "contracts" / "ui" / "intervention_cards.py",
    ROOT / "src" / "zeo_core" / "cli" / "intervention_card_types.py",
    ROOT / "docs" / "reference" / "intervention-cards.md",
    ROOT / "changelog.d" / "intervention-cards-s1.md",
    ROOT / "changelog.d" / "message-approval-cards.md",
)

#: Never in a fixture and never in feature prose.
_EVERYWHERE = re.compile(
    r"\bSOW[- ]?[0-9]"  # a work-order id
    r"|\b(?:msg|ans)[-_](?!fake)[A-Za-z0-9]"  # a mailbox message id
    r"|\bArchitect\b|\bMac mini\b|\bENG-UK\b|\bmatormini\b",
    re.IGNORECASE,
)
#: Never in a fixture: the names of the real seats and streams.
_FIXTURE_ONLY = re.compile(
    r"\bzeo(?:app|agenda|connect|council|reviewer|integrations|controlling|messenger)\b"
    r"|\bmessenger\b"
    r"|\bzeo-rt(?:-[a-z]+)?\b|\bzeocore\b",
    re.IGNORECASE,
)


def _fixture_files() -> list[Path]:
    return sorted(p for d in FIXTURE_DIRS for p in d.rglob("*") if p.is_file())


def _hits(pattern: re.Pattern[str], text: str) -> list[str]:
    return sorted({m.group(0) for m in pattern.finditer(text)})


def test_the_checked_folders_are_not_empty() -> None:
    assert len(_fixture_files()) > 200
    assert all(p.is_file() for p in FEATURE_FILES)


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_a_fixture_holds_no_internal_id(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert _hits(_EVERYWHERE, text) == []
    assert _hits(_FIXTURE_ONLY, text) == []


@pytest.mark.parametrize("path", FEATURE_FILES, ids=lambda p: p.name)
def test_feature_prose_holds_no_internal_id(path: Path) -> None:
    assert _hits(_EVERYWHERE, path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "text",
    [
        "a note for the Architect",
        "tracked as SOW-99",
        "see msg-realid1",
        "authored on the Mac mini",
        "ans_0123abcd",
        "asked of zeoapp",
    ],
)
def test_the_patterns_catch_what_they_are_for(text: str) -> None:
    assert _hits(_EVERYWHERE, text) or _hits(_FIXTURE_ONLY, text)


def test_a_fake_message_id_is_allowed() -> None:
    assert _hits(_EVERYWHERE, "msg-fake-seat-a-ckpt") == []

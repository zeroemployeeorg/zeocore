"""The ``calendar_view`` payload contract: the commitment calendar.

Every fixture is fake data. A ``valid`` fixture is a ``DataRef`` plus the
``widget.upsert`` that points at it; a ``negative`` fixture carries the payload and
the verdict (``accepted`` or ``refused``) it must get, and why.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # no stubs; a dev dependency
import pytest
from pydantic import ValidationError

from zeo_core.cli.intervention_card_types import render_typescript
from zeo_core.cli.schemas import SCHEMAS, rendered_file
from zeo_core.contracts.ui.calendar_views import CalendarView
from zeo_core.contracts.ui.widgets import COMPONENTS, REGISTRY

ROOT = Path(__file__).parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "calendar_view"
COMMITTED = ROOT / "contracts" / "zeocore-v1"
VALID = sorted((FIXTURES / "valid").glob("*.dataref.json"))
NEGATIVE = sorted((FIXTURES / "negative").glob("*.json"))
NAME = "ui.calendar-view"
SCHEMA = json.loads((COMMITTED / f"{NAME}.schema.json").read_text(encoding="utf-8"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_the_fixture_sets_are_not_empty() -> None:
    assert len(VALID) == 6
    assert len(NEGATIVE) == 89


def test_the_component_is_registered_in_core_s_profile() -> None:
    assert "calendar_view" in COMPONENTS
    assert REGISTRY["calendar_view"] is CalendarView
    assert NAME in SCHEMAS


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name.split(".")[0])
def test_a_valid_view_is_accepted_and_round_trips(path: Path) -> None:
    payload = _load(path)["payload"]
    view = CalendarView.model_validate(payload)
    again = json.loads(view.model_dump_json(exclude_none=True))
    assert CalendarView.model_validate(again) == view


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name.split(".")[0])
def test_each_valid_upsert_names_the_component_and_its_data(path: Path) -> None:
    upsert = _load(path.with_name(path.name.replace(".dataref", ".upsert")))
    assert upsert["component"] == "calendar_view"
    assert upsert["data_ref"] == _load(path)["id"]


@pytest.mark.parametrize("path", NEGATIVE, ids=lambda p: p.stem)
def test_a_fixture_gets_the_verdict_it_states(path: Path) -> None:
    fixture = _load(path)
    assert fixture["why"]
    if fixture["expect"] == "accepted":
        CalendarView.model_validate(fixture["payload"])
        return
    assert fixture["expect"] == "refused"
    with pytest.raises(ValidationError):
        CalendarView.model_validate(fixture["payload"])


def test_two_pages_of_one_snapshot_name_the_same_snapshot() -> None:
    one = _load(FIXTURES / "valid" / "calendar-page-1.dataref.json")["payload"]
    two = _load(FIXTURES / "valid" / "calendar-page-2.dataref.json")["payload"]
    assert one["report"]["snapshot_digest"] == two["report"]["snapshot_digest"]
    assert one["paging"]["next_cursor"] == two["paging"]["cursor"]
    assert two["paging"]["offset"] == one["paging"]["returned_rows"]
    assert "next_cursor" not in two["paging"]


def test_the_committed_schema_matches_the_model() -> None:
    path = COMMITTED / f"{NAME}.schema.json"
    assert path.read_text(encoding="utf-8") == rendered_file(NAME)


def test_the_committed_typescript_types_match_the_schema() -> None:
    path = COMMITTED / f"{NAME}.d.ts"
    assert path.read_text(encoding="utf-8") == render_typescript(NAME)


def test_the_committed_schema_has_no_null_anywhere() -> None:
    text = json.dumps(SCHEMA)
    assert '"null"' not in text
    assert '"default": null' not in text


def _every_object(node: object, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    found: list[tuple[Any, ...]] = []
    if isinstance(node, dict):
        found.append(path)
        for key, value in node.items():
            found.extend(_every_object(value, (*path, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_every_object(value, (*path, index)))
    return found


def _at(node: dict[str, Any], path: tuple[Any, ...]) -> dict[str, Any]:
    for step in path:
        node = node[step]
    return node


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name.split(".")[0])
def test_the_committed_schema_accepts_a_valid_view(path: Path) -> None:
    validator = jsonschema.Draft202012Validator(SCHEMA)
    assert list(validator.iter_errors(_load(path)["payload"])) == []


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name.split(".")[0])
def test_the_committed_schema_refuses_a_null_and_an_unknown_key(path: Path) -> None:
    validator = jsonschema.Draft202012Validator(SCHEMA)
    payload = _load(path)["payload"]
    for where in _every_object(payload):
        for key in _at(payload, where):
            changed = copy.deepcopy(payload)
            _at(changed, where)[key] = None
            assert not validator.is_valid(changed), (where, key)
        extra = copy.deepcopy(payload)
        _at(extra, where)["unknown_key"] = 1
        assert not validator.is_valid(extra), where


def test_a_cut_is_never_silent() -> None:
    # A page that does not hold every eligible row names the next cursor; a page
    # that claims to be the last cannot leave rows out.
    page = _load(FIXTURES / "valid" / "calendar-page-1.dataref.json")["payload"]
    page["paging"].pop("next_cursor")
    with pytest.raises(ValidationError):
        CalendarView.model_validate(page)


@pytest.mark.parametrize(
    ("times", "accepted"),
    [
        (["2026-10-29T23:55:00Z", "2026-10-29T23:55:00.1Z"], True),
        (["2026-10-29T23:55:00.1Z", "2026-10-29T23:55:00Z"], False),
        (["2026-10-30T00:30:00+01:00", "2026-10-29T23:55:00Z"], True),
        (["2026-10-29T23:55:00Z", "2026-10-30T00:30:00+01:00"], False),
        (["2026-10-30T00:55:00+01:00", "2026-10-29T23:55:00Z"], True),
        (["2026-10-29T23:55:00Z", "2026-10-30T00:55:00+01:00"], True),
    ],
)
def test_revisions_are_ordered_by_instant_not_by_string(
    times: list[str], accepted: bool
) -> None:
    payload = _load(FIXTURES / "valid" / "calendar-today.dataref.json")["payload"]
    item = payload["days"][0]["items"][0]
    item["revisions"] = [dict(item["revisions"][0], at_utc=t) for t in times]
    if accepted:
        CalendarView.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            CalendarView.model_validate(payload)

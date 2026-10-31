"""The ``intervention_card`` payload contract.

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
from pydantic import TypeAdapter, ValidationError

from zeo_core.cli.intervention_card_types import render_typescript
from zeo_core.contracts.ui import intervention_cards as cards
from zeo_core.contracts.ui import widgets
from zeo_core.contracts.ui.intervention_cards import InterventionCardPayload

FIXTURES = Path(__file__).parent / "fixtures" / "intervention_cards"
VALID = sorted((FIXTURES / "valid").glob("*.dataref.json"))
NEGATIVE = sorted((FIXTURES / "negative").glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_the_fixture_sets_are_not_empty() -> None:
    assert len(VALID) == 27
    assert len(NEGATIVE) == 310


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name.split(".")[0])
def test_a_valid_card_is_accepted_and_round_trips(path: Path) -> None:
    payload = _load(path)["payload"]
    card = InterventionCardPayload.model_validate(payload)
    again = json.loads(card.model_dump_json(exclude_none=True, by_alias=True))
    assert InterventionCardPayload.model_validate(again) == card
    assert all(v is not None for v in again.values())  # absent, never null


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name.split(".")[0])
def test_each_valid_upsert_names_the_component_and_its_data(path: Path) -> None:
    upsert = _load(path.with_name(path.name.replace(".dataref", ".upsert")))
    assert upsert["component"] == "intervention_card"
    assert upsert["data_ref"] == _load(path)["id"]


@pytest.mark.parametrize("path", NEGATIVE, ids=lambda p: p.stem)
def test_a_fixture_gets_the_verdict_it_states(path: Path) -> None:
    fixture = _load(path)
    assert fixture["why"]
    if fixture["expect"] == "accepted":
        InterventionCardPayload.model_validate(fixture["payload"])
        return
    assert fixture["expect"] == "refused"
    with pytest.raises(ValidationError):
        InterventionCardPayload.model_validate(fixture["payload"])


def _decision() -> dict[str, Any]:
    return copy.deepcopy(_load(FIXTURES / "valid" / "decision.dataref.json")["payload"])


def test_an_unknown_key_is_refused() -> None:
    payload = _decision()
    payload["note"] = "extra"
    with pytest.raises(ValidationError):
        InterventionCardPayload.model_validate(payload)


def test_null_is_not_absent() -> None:
    payload = _decision()
    payload["intervention_ref"] = None
    with pytest.raises(ValidationError):
        InterventionCardPayload.model_validate(payload)


def test_an_unknown_situation_is_refused() -> None:
    payload = _decision()
    payload["situation"] = "calendar_view"
    with pytest.raises(ValidationError):
        InterventionCardPayload.model_validate(payload)


def test_the_payload_is_immutable() -> None:
    card = InterventionCardPayload.model_validate(_decision())
    with pytest.raises(ValidationError):
        card.root = card.root  # type: ignore[misc]


@pytest.mark.parametrize(
    "text",
    ["fine", "a" * 200, "", "a" * 201, "<b>x</b>", "x‮y", "x\ny", "x​z"],
)
def test_the_text_rule_agrees_with_widgets(text: str) -> None:
    ours, theirs = TypeAdapter(cards.Text), TypeAdapter(widgets.Text)

    def accepted(adapter: TypeAdapter[str]) -> bool:
        try:
            adapter.validate_python(text)
        except ValidationError:
            return False
        return True

    assert accepted(ours) == accepted(theirs)


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-30T09:56:00Z",
        "2026-10-30T09:56:00.123456+01:00",
        "2026-10-30T24:00:00Z",
        "2026-13-30T09:56:00Z",
        "2026-02-30T09:56:00Z",
        "2026-10-30 09:56:00Z",
        "2026-10-30T09:56:00",
    ],
)
def test_the_date_rule_agrees_with_widgets(value: str) -> None:
    ours, theirs = TypeAdapter(cards.DateTime), TypeAdapter(widgets.DateTime)

    def accepted(adapter: TypeAdapter[str]) -> bool:
        try:
            adapter.validate_python(value)
        except ValidationError:
            return False
        return True

    assert accepted(ours) == accepted(theirs)


def test_a_project_card_offers_only_the_three_interventions() -> None:
    assert cards.CHECKPOINT_ACTIONS == (
        "request_checkpoint",
        "request_unblock",
        "explain_blocker",
    )


def test_the_committed_typescript_types_match_the_schema() -> None:
    # On drift, write render_typescript() to
    # contracts/zeocore-v1/ui.intervention-card.d.ts and review the diff.
    path = (
        Path(__file__).parents[2]
        / "contracts"
        / "zeocore-v1"
        / "ui.intervention-card.d.ts"
    )
    assert path.read_text(encoding="utf-8") == render_typescript()


def test_the_typescript_names_every_situation_and_marks_optional_keys() -> None:
    text = render_typescript()
    union = "export type InterventionCard = "
    named = text.split(union, 1)[1].split(";", 1)[0].split(" | ")
    assert sorted(named) == sorted(
        [
            "WorkLocation",
            "Checkpoint",
            "Blocker",
            "Decision",
            "RequestStatus",
            "Outcome",
            "ProjectCheckpoint",
            "SourceItem",
            "Message",
            "Approval",
        ]
    )
    assert "  work_ref?: WorkRef;" in text
    assert "null" not in text


COMMITTED = Path(__file__).parents[2] / "contracts" / "zeocore-v1"
SCHEMA = json.loads(
    (COMMITTED / "ui.intervention-card.schema.json").read_text(encoding="utf-8")
)


def _every_object(node: object, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """The path of every JSON object in ``node``."""
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


def test_the_committed_schema_is_valid_json_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_the_committed_schema_has_no_null_anywhere() -> None:
    # The models refuse an explicit null, so the schema must not offer one.
    text = json.dumps(SCHEMA)
    assert '"null"' not in text
    assert '"default": null' not in text


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name.split(".")[0])
def test_the_committed_schema_accepts_a_valid_fixture(path: Path) -> None:
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


@pytest.mark.parametrize(
    "path",
    [p for p in NEGATIVE if "null" in p.stem or "unknown-key" in p.stem],
    ids=lambda p: p.stem,
)
def test_the_committed_schema_refuses_the_null_and_unknown_key_negatives(
    path: Path,
) -> None:
    fixture = _load(path)
    assert fixture["expect"] == "refused"
    assert not jsonschema.Draft202012Validator(SCHEMA).is_valid(fixture["payload"])


def test_no_published_pattern_escapes_a_quote() -> None:
    # An escaped quote is valid in Python and invalid in an ECMAScript ``u`` regex.
    patterns: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            patterns.extend(
                v for k, v in node.items() if k == "pattern" and isinstance(v, str)
            )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(InterventionCardPayload.model_json_schema())
    assert patterns
    assert not [p for p in patterns if '\\"' in p or "\\'" in p]

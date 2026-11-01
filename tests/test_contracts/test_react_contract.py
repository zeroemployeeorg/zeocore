"""The browser contract is generated from the models, so it cannot drift from them."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from zeo_core.contracts.ui.intervention_cards import InterventionCardPayload

ROOT = Path(__file__).parents[2]
VALID = ROOT / "tests" / "test_contracts" / "fixtures" / "intervention_cards" / "valid"


def _card(name: str) -> dict[str, Any]:
    text = (VALID / f"{name}.dataref.json").read_text(encoding="utf-8")
    payload: dict[str, Any] = json.loads(text)["payload"]
    return payload


@pytest.mark.parametrize(
    "name", ["message-text-reply", "message-choice-reply", "approval-pending"]
)
def test_a_card_is_closed_and_absent_only(name: str) -> None:
    original = _card(name)
    for key in original:
        value = copy.deepcopy(original)
        value[key] = None
        with pytest.raises(ValidationError):
            InterventionCardPayload.model_validate(value)
    with pytest.raises(ValidationError):
        InterventionCardPayload.model_validate({**original, "execute": "anything"})


def test_the_generated_browser_contract_has_no_drift() -> None:
    script = ROOT / "scripts" / "generate_react_contract.py"
    spec = importlib.util.spec_from_file_location("generate_react_contract", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, expected in module.artifacts().items():
        assert path.read_text(encoding="utf-8") == expected, path

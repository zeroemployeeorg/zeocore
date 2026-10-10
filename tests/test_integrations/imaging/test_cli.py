"""``zeo-image``: one JSON call in, one JSON answer out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_integrations.imaging.images import SVG, png
from zeo_core.integrations.imaging import (
    BaseImageRequest,
    CreditBalance,
    GeneratedImage,
    ImagingError,
    ImagingService,
)
from zeo_core.integrations.imaging.__main__ import run
from zeo_core.integrations.imaging.models import sha256_hex


class _Recraft:
    def __init__(self, error: ImagingError | None = None) -> None:
        self.requests: list[BaseImageRequest] = []
        self.error = error

    def run(self, request: BaseImageRequest) -> GeneratedImage:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return GeneratedImage(
            content=SVG,
            media_type="image/svg+xml",
            sha256=sha256_hex(SVG),
            provider="recraft",
            operation="recraft.image.vectorize",
            profile="hosted",
            execution_id="exe_1",
        )

    def credits(self) -> CreditBalance:
        return CreditBalance(credits=12)


def _call(command: object, backend: _Recraft) -> tuple[int, dict[str, object]]:
    return run(
        json.dumps(command), service_factory=lambda: ImagingService(recraft=backend)
    )


def test_a_vectorize_call_writes_the_file_and_answers_its_facts(tmp_path: Path) -> None:
    source = tmp_path / "duck.png"
    source.write_bytes(png(64, 64))
    output = tmp_path / "duck.svg"
    backend = _Recraft()
    status, answer = _call(
        {
            "request": {"kind": "recraft.vectorize", "input": {"path": str(source)}},
            "output": str(output),
        },
        backend,
    )
    assert status == 0
    assert output.read_bytes() == SVG
    assert answer["ok"] is True
    assert answer["path"] == str(output)
    assert answer["sha256"] == sha256_hex(SVG)
    assert answer["execution_id"] == "exe_1"
    assert "content" not in answer
    assert backend.requests[0].input_images()[0].content == png(64, 64)


def test_credits(tmp_path: Path) -> None:
    assert _call({"credits": True}, _Recraft()) == (
        0,
        {"ok": True, "provider": "recraft", "credits": 12.0},
    )


@pytest.mark.parametrize(
    "command",
    [
        [],
        {"request": {"kind": "recraft.vectorize"}, "output": "x.svg"},
        {
            "request": {"kind": "recraft.vectorize", "input": {"path": "/missing.png"}},
            "output": "x",
        },
        {"request": {"kind": "recraft.vectorize", "input": "duck.png"}, "output": "x"},
        {"request": {"kind": "nope"}, "output": "x"},
        {
            "request": {"kind": "gemini.generate", "prompt": "x"},
            "output": "/missing/dir/x.png",
        },
        {"request": {"kind": "gemini.generate", "prompt": "x"}},
    ],
)
def test_an_invalid_command_exits_2_and_calls_nothing(command: object) -> None:
    backend = _Recraft()
    status, answer = _call(command, backend)
    assert status == 2
    assert answer["outcome"] == "invalid_request"
    assert backend.requests == []


def test_an_imaging_error_exits_3_with_its_outcome(tmp_path: Path) -> None:
    status, answer = _call(
        {
            "request": {"kind": "recraft.generate", "prompt": "a duck"},
            "output": str(tmp_path / "duck.png"),
        },
        _Recraft(ImagingError("budget_exhausted", "raise the budget")),
    )
    assert status == 3
    assert answer == {
        "ok": False,
        "outcome": "budget_exhausted",
        "message": "raise the budget",
        "retry": "same_request",
        "request_key": None,
        "approval_url": None,
    }
    assert not (tmp_path / "duck.png").exists()


def test_an_unconfigured_provider_is_refused(tmp_path: Path) -> None:
    status, answer = _call(
        {
            "request": {"kind": "gemini.generate", "prompt": "a duck"},
            "output": str(tmp_path / "duck.png"),
        },
        _Recraft(),
    )
    assert (status, answer["outcome"]) == (3, "refused")

"""No YouTube test may reach the network: a real byte transfer fails the test."""

from __future__ import annotations

import pytest

from zeo_core.integrations.google.youtube.transfer import HttpxByteHttp


@pytest.fixture(autouse=True)
def _no_real_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a test tried to send bytes over the network")

    monkeypatch.setattr(HttpxByteHttp, "put", refuse)

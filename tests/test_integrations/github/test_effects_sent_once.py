"""A GitHub effect is sent once; only reads are retried; a merge is head-pinned."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from zeo_core.core.errors import ZeoApiError
from zeo_core.integrations.github.operations.pull_requests import merge_pull_request
from zeo_core.integrations.github.utils.api import make_request

API = "https://api.github.com"
HEAD = "0123456789abcdef0123456789abcdef01234567"


def _response(status: int, body: dict[str, Any] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = b"{}" if body is None else json.dumps(body).encode()
    response.headers["X-RateLimit-Remaining"] = "100"
    response.url = API
    return response


def _session(*outcomes: object) -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.request.side_effect = list(outcomes)
    return session


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "failure",
    [
        _response(502),
        requests.exceptions.ConnectionError("dropped"),
        requests.exceptions.Timeout("slow"),
    ],
)
def test_an_effect_is_sent_once_even_when_its_outcome_is_unknown(
    method: str, failure: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    session = _session(failure, _response(201))
    with pytest.raises(ZeoApiError):
        make_request(session, method, "/repos/o/r/pulls", API, max_retries=3)
    assert session.request.call_count == 1


def test_a_read_is_still_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    session = _session(
        requests.exceptions.ConnectionError("dropped"), _response(502), _response(200)
    )
    response = make_request(session, "GET", "/repos/o/r", API, max_retries=3)
    assert response.status_code == 200
    assert session.request.call_count == 3


def test_a_merge_sends_the_head_sha_once() -> None:
    session = _session(_response(200, {"merged": True}))
    assert merge_pull_request(session, "o/r", 7, API, sha=HEAD) is True
    (call,) = session.request.call_args_list
    assert call.args[:2] == ("PUT", f"{API}/repos/o/r/pulls/7/merge")
    assert call.kwargs["json"] == {"merge_method": "merge", "sha": HEAD}


def test_a_merge_after_a_server_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    session = _session(_response(502), _response(200, {"merged": True}))
    with pytest.raises(ZeoApiError):
        merge_pull_request(session, "o/r", 7, API, sha=HEAD)
    assert session.request.call_count == 1


@pytest.mark.parametrize("sha", ["", "abc123", HEAD.upper(), HEAD + "0", "main"])
def test_a_merge_without_a_full_head_sha_is_refused_before_sending(sha: str) -> None:
    session = _session()
    with pytest.raises(ValueError, match="40-hex"):
        merge_pull_request(session, "o/r", 7, API, sha=sha)
    session.request.assert_not_called()


@pytest.mark.parametrize("method", ["squash", "rebase"])
def test_only_the_merge_method_is_allowed(method: str) -> None:
    session = _session()
    with pytest.raises(ValueError, match="merge method"):
        merge_pull_request(session, "o/r", 7, API, sha=HEAD, merge_method=method)  # type: ignore[arg-type]
    session.request.assert_not_called()

"""``zeocore llm``: exact provider bytes on stdin, the Broker's answer out."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import httpx
import pytest

from tests.test_integrations.hosted.test_transport import (
    NOW,
    response,
    session,
    transport,
)
from tests.test_integrations.llms.test_hosted_chat import BODY, OPENAI_ANSWER, Broker
from zeo_core.cli import client
from zeo_core.integrations.hosted import InMemorySecureSessionStore
from zeo_core.integrations.hosted.client import HostedOperationRequest


class ClosingBroker(Broker):
    def close(self) -> None:
        pass


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> ClosingBroker:
    fake = ClosingBroker()
    monkeypatch.setattr(
        client, "make_store", lambda profile: InMemorySecureSessionStore()
    )
    monkeypatch.setattr(client, "make_transport", lambda store: fake)
    return fake


ARGS = ["openai.chat.completions.create", "--connection", "con_openai_12345678"]


def test_the_stdin_bytes_are_sent_exactly(broker: ClosingBroker) -> None:
    status, answer = client.llm(ARGS, BODY)
    assert status == 0
    assert broker.sent() == BODY
    assert base64.b64decode(answer["provider_body_base64"]) == OPENAI_ANSWER
    assert answer["request_sha256"] == hashlib.sha256(BODY).hexdigest()
    assert "provider_body" not in answer


def test_out_writes_the_exact_answer_bytes(
    broker: ClosingBroker, tmp_path: Path
) -> None:
    out = tmp_path / "answer.json"
    status, answer = client.llm([*ARGS, "--out", str(out)], BODY)
    assert status == 0
    assert out.read_bytes() == OPENAI_ANSWER
    assert "provider_body_base64" not in answer


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ({"status": "ambiguous", "execution_id": "e"}, 13),
        (
            {
                "status": "ambiguous",
                "execution_id": "e",
                "receipt": {"in_flight": True},
            },
            11,
        ),
        ({"status": "refused", "execution_id": "e"}, 20),
    ],
)
def test_outcomes_have_their_exits(
    broker: ClosingBroker, reply: dict[str, object], status: int
) -> None:
    broker.reply = reply
    got, answer = client.llm(ARGS, BODY)
    assert got == status
    assert answer["request_key"]


@pytest.mark.parametrize("stdin", [b"not json", b'{"a":1,"a":2}', b""])
def test_input_that_is_not_strict_json_is_invalid(
    broker: ClosingBroker, stdin: bytes
) -> None:
    assert client.llm(ARGS, stdin)[0] == 2
    assert broker.requests == []


def test_llm_operations_may_carry_up_to_1_mib_and_wait_200_s() -> None:
    seen: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(http_request)
        return response(
            200, {"status": "refused", "execution_id": "e"}, request=http_request
        )

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    big = {"body_base64": "A" * 900_000}
    hosted.invoke(
        HostedOperationRequest(
            connection_id="con_openai_12345678",
            operation_id="openai.chat.completions.create",
            arguments=big,
            idempotency_key="k",
        )
    )
    assert seen[0].extensions["timeout"]["read"] == 200.0
    from zeo_core.integrations.hosted.client import HostedClientError

    with pytest.raises(HostedClientError, match="limit"):
        hosted.invoke(
            HostedOperationRequest(
                connection_id="con_google_12345678",
                operation_id="google.drive.file.download",
                arguments=big,
                idempotency_key="k",
            )
        )

"""LLM chat through a faked ZEOconnect (proposed billed LLM chat, draft 3)."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest

from zeo_core.contracts.connections import NormalizedError, NormalizedErrorCode
from zeo_core.integrations.hosted.client import (
    HostedArtifactDescriptor,
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedSessionError,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
)
from zeo_core.integrations.hosted.services import HostedServiceBinding
from zeo_core.integrations.llms.hosted import (
    HostedChatOnce,
    HostedLLMChat,
    HostedLLMError,
    request_key,
)
from zeo_core.integrations.llms.models import ChatMessage, LLMOptions

BINDING = HostedServiceBinding(connection_id="con_openai_12345678")
# Key order and number formatting a re-serializer would change.
BODY = (
    b'{"model":"gpt-5.5-2026-04-23","store":false,"max_completion_tokens":64,'
    b'"temperature":1.0E0,"messages":[{"role":"user","content":"hi"}]}'
)
OPENAI_ANSWER = json.dumps(
    {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
).encode()


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Broker:
    def __init__(self, answer: bytes = OPENAI_ANSWER) -> None:
        self.answer = answer
        self.requests: list[HostedOperationRequest] = []
        self.reply: dict[str, Any] | Exception | None = None
        self.receipt: dict[str, Any] = {}
        self.as_artifact = False
        self.stored: dict[str, bytes] = {}

    def sent(self, index: int = -1) -> bytes:
        return base64.b64decode(self.requests[index].arguments["body_base64"])  # type: ignore[arg-type]

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        if self.reply is not None:
            return HostedOperationResponse.model_validate(self.reply)
        receipt = {
            "request_sha256": _sha(self.sent()),
            "provider_body_sha256": _sha(self.answer),
            "terminal": "complete",
            "model_requested": "gpt-5.5-2026-04-23",
            "model_reported": "gpt-5.5-2026-04-23",
            "provider_response_id": "chatcmpl-1",
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            "cost": {"unit": "usd_micros", "reserved": 900, "settled": 30},
            **self.receipt,
        }
        if self.as_artifact:
            descriptor = HostedArtifactDescriptor(
                artifact_id="art_llm_12345678",
                content_sha256="sha256:" + _sha(self.answer),
                size_bytes=len(self.answer),
                media_type="application/json",
                filename="response.json",
            )
            self.stored[descriptor.artifact_id] = self.answer
            return HostedOperationResponse(
                status="confirmed",
                execution_id="exe_1",
                artifact=descriptor,
                receipt=receipt,
            )
        return HostedOperationResponse(
            status="confirmed",
            execution_id="exe_1",
            result={
                "provider_status": 200,
                "provider_body_base64": base64.b64encode(self.answer).decode(),
            },
            receipt=receipt,
        )

    def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes:
        return self.stored[artifact_id]


def _chat(broker: Broker) -> HostedLLMChat:
    return HostedLLMChat(
        client=HostedConnectionClient(transport=broker), binding=BINDING
    )


# -- exact bytes ---------------------------------------------------------------


def test_the_exact_bytes_go_and_the_exact_bytes_come_back() -> None:
    broker = Broker()
    response = _chat(broker).send("openai.chat.completions.create", BODY)
    assert broker.sent() == BODY
    (request,) = broker.requests
    assert request.operation_id == "openai.chat.completions.create"
    assert set(request.arguments) == {"body_base64"}
    assert response.provider_body == OPENAI_ANSWER
    assert response.request_sha256 == _sha(BODY)
    assert response.provider_body_sha256 == _sha(OPENAI_ANSWER)
    assert response.provider_status == 200
    assert response.model_reported == "gpt-5.5-2026-04-23"
    assert response.usage == {"prompt_tokens": 5, "completion_tokens": 2}
    assert response.terminal == "complete"
    assert response.replayed is False


def test_a_large_answer_comes_back_through_the_artifact() -> None:
    broker = Broker(answer=json.dumps({"x": "y" * 2_000_000}).encode())
    broker.as_artifact = True
    response = _chat(broker).send("openai.chat.completions.create", BODY)
    assert response.provider_body == broker.answer
    assert response.provider_status is None


def test_the_key_is_the_bytes_and_the_occurrence() -> None:
    broker = Broker()
    chat = _chat(broker)
    chat.send("openai.chat.completions.create", BODY)
    chat.send("openai.chat.completions.create", BODY)
    chat.send("openai.chat.completions.create", BODY, occurrence="2")
    chat.send("openai.chat.completions.create", BODY.replace(b"hi", b"ho"))
    keys = [request.idempotency_key for request in broker.requests]
    assert (
        keys[0] == keys[1] == request_key("openai.chat.completions.create", BODY, "1")
    )
    assert len(set(keys)) == 3


@pytest.mark.parametrize(
    ("receipt", "match"),
    [
        ({"request_sha256": "0" * 64}, "request digest"),
        ({"provider_body_sha256": "0" * 64}, "body digest"),
        # Missing digests fail closed, never pass as verified.
        ({"request_sha256": None}, "request digest"),
        ({"provider_body_sha256": None}, "body digest"),
    ],
)
def test_digests_that_do_not_match_are_never_accepted(
    receipt: dict[str, Any], match: str
) -> None:
    broker = Broker()
    broker.receipt = receipt
    with pytest.raises(HostedLLMError, match=match) as caught:
        _chat(broker).send("openai.chat.completions.create", BODY)
    assert caught.value.outcome == "invalid_response"


@pytest.mark.parametrize(
    "body", [b"", b"not json", b"[1]", b"{" + b'"a":"' + b"x" * (800 * 1024) + b'"}']
)
def test_an_unusable_body_is_refused_before_sending(body: bytes) -> None:
    broker = Broker()
    with pytest.raises(ValueError):
        _chat(broker).send("openai.chat.completions.create", body)
    assert broker.requests == []


def test_replay_and_missing_usage_are_reported() -> None:
    broker = Broker()
    broker.receipt = {"replayed": True, "usage": None, "usage_missing": True}
    response = _chat(broker).send("openai.chat.completions.create", BODY)
    assert (response.replayed, response.usage, response.usage_missing) == (
        True,
        None,
        True,
    )


# -- outcomes ------------------------------------------------------------------


def _failed(
    code: NormalizedErrorCode, message: str = "no", status: str = "failed_safe"
) -> dict[str, Any]:
    return {
        "status": status,
        "execution_id": "exe_x",
        "normalized_error": NormalizedError(code=code, message=message).model_dump(
            mode="json"
        ),
    }


@pytest.mark.parametrize(
    ("reply", "outcome", "retry"),
    [
        ({"status": "ambiguous", "execution_id": "e"}, "ambiguous", "same_request"),
        (
            {
                "status": "ambiguous",
                "execution_id": "e",
                "receipt": {"in_flight": True},
            },
            "in_flight",
            "same_request",
        ),
        (
            _failed(NormalizedErrorCode.BUDGET_EXHAUSTED, status="refused"),
            "budget_exhausted",
            "same_request",
        ),
        (_failed(NormalizedErrorCode.RATE_LIMITED), "unavailable", "new_occurrence"),
        (
            _failed(NormalizedErrorCode.PROVIDER_UNAVAILABLE),
            "unavailable",
            "new_occurrence",
        ),
        (
            _failed(NormalizedErrorCode.STOPPED, "stopped:dispatch:openai"),
            "stopped",
            "same_request",
        ),
        (_failed(NormalizedErrorCode.REQUEST_REFUSED), "refused", "new_occurrence"),
        ({"status": "refused", "execution_id": "e"}, "refused", "none"),
    ],
)
def test_each_answer_has_its_outcome_and_is_sent_once(
    reply: dict[str, Any], outcome: str, retry: str
) -> None:
    broker = Broker()
    broker.reply = reply
    with pytest.raises(HostedLLMError) as caught:
        _chat(broker).send("openai.chat.completions.create", BODY)
    assert (caught.value.outcome, caught.value.retry) == (outcome, retry)
    assert caught.value.request_key == request_key(
        "openai.chat.completions.create", BODY, "1"
    )
    assert len(broker.requests) == 1


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (HostedUnreachableError(), "ambiguous"),
        (HostedUnreachableError(may_have_arrived=False), "unavailable"),
        (HostedUnavailableError(), "unavailable"),
        (HostedStoppedError(control="dispatch", scope="global"), "stopped"),
        (HostedSessionError("paired device session is expired"), "not_paired"),
        (HostedClientError("hosted request was refused"), "refused"),
    ],
)
def test_transport_failures_have_their_outcomes(error: Exception, outcome: str) -> None:
    broker = Broker()
    broker.reply = error
    with pytest.raises(HostedLLMError) as caught:
        _chat(broker).send("openai.chat.completions.create", BODY)
    assert caught.value.outcome == outcome


# -- chat_once -----------------------------------------------------------------


def _once(broker: Broker) -> HostedChatOnce:
    return HostedChatOnce(
        _chat(broker),
        operation="openai.chat.completions.create",
        model="gpt-5.5-2026-04-23",
    )


MESSAGES = [
    ChatMessage(role="system", content="be brief"),
    ChatMessage(role="user", content="hi"),
]


def test_chat_once_builds_an_openai_body_with_store_false() -> None:
    broker = Broker()
    result = _once(broker).chat_once(
        MESSAGES,
        LLMOptions(
            max_tokens=64, temperature=0.2, response_format={"type": "json_object"}
        ),
        occurrence="record-1-draft",
    )
    assert result.success and result.content == "hello"
    assert json.loads(broker.sent()) == {
        "model": "gpt-5.5-2026-04-23",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        "max_completion_tokens": 64,
        "store": False,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }


def test_a_resume_with_the_same_occurrence_is_the_same_request() -> None:
    broker = Broker()
    once = _once(broker)
    for _ in range(2):
        once.chat_once(MESSAGES, LLMOptions(max_tokens=64), occurrence="record-1-draft")
    assert broker.requests[0].idempotency_key == broker.requests[1].idempotency_key
    assert once.last_response is not None and once.last_response.execution_id == "exe_1"


def test_chat_once_builds_an_anthropic_body() -> None:
    broker = Broker(
        answer=json.dumps(
            {
                "content": [
                    {"type": "text", "text": "hel"},
                    {"type": "text", "text": "lo"},
                ]
            }
        ).encode()
    )
    once = HostedChatOnce(
        _chat(broker), operation="anthropic.messages.create", model="claude-x-20261001"
    )
    result = once.chat_once(MESSAGES, LLMOptions(max_tokens=100, stop=["END"]))
    assert result.content == "hello"
    assert json.loads(broker.sent()) == {
        "model": "claude-x-20261001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.7,
        "system": "be brief",
        "stop_sequences": ["END"],
    }


@pytest.mark.parametrize(
    "options",
    [
        LLMOptions(),
        LLMOptions(max_tokens=10, frequency_penalty=0.5),
        LLMOptions(max_tokens=10, stop=["x"]),
        LLMOptions(max_tokens=10, stream=True),
        LLMOptions(max_tokens=10, model="gpt-other"),
    ],
)
def test_what_the_contract_can_not_carry_is_refused_never_dropped(
    options: LLMOptions,
) -> None:
    broker = Broker()
    with pytest.raises(ValueError):
        _once(broker).chat_once(MESSAGES, options)
    assert broker.requests == []


def test_two_system_messages_and_tool_messages_are_refused() -> None:
    broker = Broker()
    with pytest.raises(ValueError, match="one system"):
        HostedChatOnce(
            _chat(broker), operation="anthropic.messages.create", model="m"
        ).chat_once(
            [*MESSAGES, ChatMessage(role="system", content="more")],
            LLMOptions(max_tokens=5),
        )
    with pytest.raises(ValueError, match="text"):
        _once(broker).chat_once(
            [ChatMessage(role="tool", content="x")], LLMOptions(max_tokens=5)
        )
    with pytest.raises(ValueError, match="streaming"):
        _once(broker).chat_once(MESSAGES, LLMOptions(max_tokens=5), callback=print)
    assert broker.requests == []


def test_ambiguous_and_in_flight_stay_distinct_through_chat_once() -> None:
    broker = Broker()
    broker.reply = {
        "status": "ambiguous",
        "execution_id": "e",
        "receipt": {"in_flight": True},
    }
    with pytest.raises(HostedLLMError) as caught:
        _once(broker).chat_once(MESSAGES, LLMOptions(max_tokens=5), occurrence="r1")
    assert caught.value.outcome == "in_flight"

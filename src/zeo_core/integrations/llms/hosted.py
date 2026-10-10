"""LLM chat through ZEOconnect: the provider key stays in Broker custody.

Wire: the proposed billed LLM chat contract, draft 3 (org 46619c2d). The
caller's provider request travels as exact bytes, never re-serialized, under
an idempotency key derived from those bytes and an occurrence label, so the
same request is an exact replay of the stored outcome and never bills twice.

Two layers:

- ``HostedLLMChat.send``: exact provider bytes in, exact provider bytes out,
  with the Broker's receipt. For callers that build provider bodies
  themselves (Rasa's successor adapter, ``zeocore llm``).
- ``HostedChatOnce``: zeocore's ``OneAttemptLLMProviderProtocol`` over it,
  building an OpenAI Chat Completions or Anthropic Messages body from
  ``ChatMessage`` and ``LLMOptions`` (Newsroom's injected client).

Streaming (``:stream``) is not in this client yet.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from collections.abc import Callable, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from zeo_core.contracts.connections import NormalizedErrorCode
from zeo_core.integrations.core.results import IntegrationResult
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedOperationStatus,
    HostedSessionError,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
    is_outage,
    stop_of,
)
from zeo_core.integrations.hosted.services import HostedServiceBinding
from zeo_core.integrations.llms.models import ChatMessage, LLMOptions, RoleType

LLMOperation = Literal[
    "openai.responses.create",
    "openai.chat.completions.create",
    "anthropic.messages.create",
    "nebius.chat.completions.create",
]
#: base64 grows a body by 4/3, and the invoke envelope must stay within the
#: contract's 1 MiB; this leaves room for the envelope.
MAX_BODY_BYTES: Final = 760 * 1024
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_OCCURRENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class HostedLLMError(RuntimeError):
    """Why no answer came back, in terms a caller can act on.

    ``outcome``: ``refused``, ``budget_exhausted``, ``stopped``,
    ``unavailable``, ``ambiguous`` (the call may have run and been billed; the
    same request replays its stored outcome), ``in_flight`` (the first call is
    still running), ``not_paired``, ``invalid_response``.

    ``retry``: ``same_request`` (asking again is safe and never bills twice),
    ``new_occurrence`` (the failure is recorded; another attempt is a new call
    that may bill) or ``none`` (the request has to change).
    """

    def __init__(
        self,
        outcome: str,
        message: str,
        *,
        retry: str,
        request_key: str | None = None,
        execution_id: str | None = None,
    ) -> None:
        self.outcome = outcome
        self.retry = retry
        self.request_key = request_key
        self.execution_id = execution_id
        super().__init__(message)


class HostedLLMResponse(BaseModel):
    """The provider's exact answer and the Broker's facts about the call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    request_key: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_status: int | None = None
    provider_body: bytes = Field(repr=False)
    provider_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str
    replayed: bool = False
    #: complete, incomplete, provider_error, no_terminal, malformed, or None.
    terminal: str | None = None
    model_requested: str | None = None
    model_reported: str | None = None
    provider_response_id: str | None = None
    usage: dict[str, JsonValue] | None = None
    usage_missing: bool = False
    cost: dict[str, JsonValue] | None = None

    def parsed(self) -> Any:  # noqa: ANN401 -- the provider's own JSON
        """The provider body, parsed."""
        return json.loads(self.provider_body)


def request_key(operation: str, body: bytes, occurrence: str) -> str:
    """The same exact bytes and occurrence always give the same key."""
    material = f"{operation}\n{hashlib.sha256(body).hexdigest()}\n{occurrence}"
    return "zl-" + hashlib.sha256(material.encode()).hexdigest()


class HostedLLMChat:
    """One provider connection (openai, anthropic or nebius)."""

    def __init__(
        self, *, client: HostedConnectionClient, binding: HostedServiceBinding
    ) -> None:
        self._client = client
        self._binding = binding

    def send(
        self, operation: LLMOperation, body: bytes, *, occurrence: str = "1"
    ) -> HostedLLMResponse:
        """Send exactly ``body`` once. It must already be acceptable as is:
        the Broker never rewrites it, and neither does this client."""
        if not _OCCURRENCE.fullmatch(occurrence):
            raise ValueError("occurrence is 1 to 128 safe characters")
        if not 0 < len(body) <= MAX_BODY_BYTES:
            raise ValueError(
                "the provider body is empty or over the 1 MiB invoke bound"
            )
        try:
            parsed = json.loads(body)
        except ValueError:
            raise ValueError("the provider body is not JSON") from None
        if not isinstance(parsed, dict):
            raise ValueError("the provider body is a JSON object")
        key = request_key(operation, body, occurrence)
        sha = hashlib.sha256(body).hexdigest()
        try:
            response = self._invoke(operation, body, key)
            return self._answer(operation, response, key, sha)
        except HostedLLMError as error:
            error.request_key = error.request_key or key
            raise

    def _invoke(self, operation: str, body: bytes, key: str) -> HostedOperationResponse:
        try:
            return self._client.invoke(
                HostedOperationRequest(
                    connection_id=self._binding.connection_id,
                    connector_revision=self._binding.connector_revision,
                    operation_id=operation,
                    arguments={"body_base64": base64.b64encode(body).decode()},
                    idempotency_key=key,
                )
            )
        except HostedStoppedError as error:
            raise HostedLLMError("stopped", str(error), retry="same_request") from None
        except HostedUnreachableError as error:
            if error.may_have_arrived:
                raise HostedLLMError(
                    "ambiguous",
                    "ZEOconnect did not answer; the same request replays its outcome",
                    retry="same_request",
                ) from None
            raise HostedLLMError(
                "unavailable", str(error), retry="same_request"
            ) from None
        except HostedUnavailableError as error:
            raise HostedLLMError(
                "unavailable", str(error), retry="same_request"
            ) from None
        except HostedSessionError:
            raise HostedLLMError(
                "not_paired",
                "this device is not paired with ZEOconnect; run zeocore login",
                retry="same_request",
            ) from None
        except HostedClientError as error:
            raise HostedLLMError("refused", str(error), retry="none") from None

    def _answer(
        self, operation: str, response: HostedOperationResponse, key: str, sha: str
    ) -> HostedLLMResponse:
        receipt = dict(response.receipt or {})
        _raise_unless_confirmed(response)
        provider_status: int | None = None
        if response.artifact is not None:
            if response.artifact.size_bytes > MAX_RESPONSE_BYTES:
                raise HostedLLMError(
                    "invalid_response",
                    "the stored response is over 16 MiB",
                    retry="none",
                )
            try:
                body = self._client.download_artifact(response.artifact)
            except HostedClientError as error:
                raise HostedLLMError(
                    "unavailable", str(error), retry="same_request"
                ) from None
        else:
            result = response.result
            encoded = (
                result.get("provider_body_base64") if isinstance(result, dict) else None
            )
            if not isinstance(result, dict) or not isinstance(encoded, str):
                raise HostedLLMError(
                    "invalid_response",
                    "the answer carried no provider body",
                    retry="none",
                )
            try:
                body = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise HostedLLMError(
                    "invalid_response", "the provider body is not base64", retry="none"
                ) from None
            status = result.get("provider_status")
            provider_status = status if isinstance(status, int) else None
        body_sha = hashlib.sha256(body).hexdigest()
        # Both digests are part of the receipt (draft 3 M1, M3). A missing one
        # is never read as verified: it fails closed.
        if receipt.get("provider_body_sha256") != body_sha:
            raise HostedLLMError(
                "invalid_response",
                "the provider body digest is missing or does not match",
                retry="none",
            )
        if receipt.get("request_sha256") != sha:
            # The Broker sent other bytes than ours, or didn't say: never accept.
            raise HostedLLMError(
                "invalid_response",
                "ZEOconnect's request digest is missing or does not match",
                retry="none",
            )
        return HostedLLMResponse(
            operation=operation,
            request_key=key,
            request_sha256=sha,
            provider_status=provider_status
            if provider_status is not None
            else _int(receipt.get("provider_status")),
            provider_body=body,
            provider_body_sha256=body_sha,
            execution_id=response.execution_id,
            replayed=receipt.get("replayed") is True,
            terminal=_text(receipt.get("terminal")),
            model_requested=_text(receipt.get("model_requested")),
            model_reported=_text(receipt.get("model_reported")),
            provider_response_id=_text(receipt.get("provider_response_id")),
            usage=receipt["usage"] if isinstance(receipt.get("usage"), dict) else None,
            usage_missing=receipt.get("usage_missing") is True,
            cost=receipt["cost"] if isinstance(receipt.get("cost"), dict) else None,
        )


def _raise_unless_confirmed(response: HostedOperationResponse) -> None:
    status = response.status
    execution = response.execution_id
    if status is HostedOperationStatus.CONFIRMED:
        return
    if status is HostedOperationStatus.AMBIGUOUS:
        if (response.receipt or {}).get("in_flight") is True:
            raise HostedLLMError(
                "in_flight",
                "the first call for this request is still running; ask again later",
                retry="same_request",
                execution_id=execution,
            )
        raise HostedLLMError(
            "ambiguous",
            "the call may have run and been billed; the same request replays its"
            " outcome",
            retry="same_request",
            execution_id=execution,
        )
    if status is HostedOperationStatus.APPROVAL_REQUIRED:
        raise HostedLLMError(
            "refused",
            "billed chat does not take per-call approval; check the connection",
            retry="none",
            execution_id=execution,
        )
    if stop_of(response) is not None:
        raise HostedLLMError(
            "stopped",
            str(stop_of(response)),
            retry="same_request",
            execution_id=execution,
        )
    error = response.normalized_error
    code = error.code if error is not None else None
    if is_outage(response) or code is NormalizedErrorCode.RATE_LIMITED:
        raise HostedLLMError(
            "unavailable",
            "the provider or a control was unavailable; try again as a new occurrence",
            retry="new_occurrence",
            execution_id=execution,
        )
    if code is NormalizedErrorCode.BUDGET_EXHAUSTED:
        raise HostedLLMError(
            "budget_exhausted",
            "the connection's budget can't cover this call; raise it in ZEOconnect",
            retry="same_request",
            execution_id=execution,
        )
    raise HostedLLMError(
        "refused",
        "the chat call was refused; nothing was produced",
        retry="none" if status is HostedOperationStatus.REFUSED else "new_occurrence",
        execution_id=execution,
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 512 else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# -- The OneAttemptLLMProviderProtocol adapter ----------------------------------


class HostedChatOnce:
    """``chat_once`` through the Broker, for OpenAI Chat Completions or
    Anthropic Messages.

    The body is built from the messages and options only as the provider
    defines it. Anything the hosted contract can't carry (tools, functions,
    stop sequences on OpenAI, penalties, more than one system message) is
    refused, never dropped. ``max_tokens`` is required: it bounds the
    reservation. Failures raise ``HostedLLMError``, so ``ambiguous`` and
    ``in_flight`` stay distinct; ``last_response`` keeps the receipt.
    """

    def __init__(
        self,
        chat: HostedLLMChat,
        *,
        operation: Literal[
            "openai.chat.completions.create", "anthropic.messages.create"
        ],
        model: str,
    ) -> None:
        self._chat = chat
        self._operation = operation
        self._model = model
        self.last_response: HostedLLMResponse | None = None

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, Any]],
        options: LLMOptions | None = None,
        callback: Callable[[str], None] | None = None,
    ) -> IntegrationResult[str]:
        return self.chat_once(messages, options, callback)

    def chat_once(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, Any]],
        options: LLMOptions | None = None,
        callback: Callable[[str], None] | None = None,
        *,
        occurrence: str | None = None,
    ) -> IntegrationResult[str]:
        """One call. Pass a stable ``occurrence`` (a record id) so that a resume
        after a crash replays the stored outcome instead of billing again."""
        if callback is not None:
            raise ValueError("streaming is not available through the hosted client yet")
        normalized = [
            item if isinstance(item, ChatMessage) else ChatMessage.from_dict(item)
            for item in messages
        ]
        body = (
            _openai_body(normalized, options or LLMOptions(), self._model)
            if self._operation == "openai.chat.completions.create"
            else _anthropic_body(normalized, options or LLMOptions(), self._model)
        )
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        response = self._chat.send(
            self._operation,
            encoded,
            occurrence=occurrence or f"once-{uuid.uuid4()}",
        )
        self.last_response = response
        return IntegrationResult.success_result(
            content=_text_of(self._operation, response.parsed()),
            message=response.terminal,
        )

    def count_tokens(
        self, messages: Sequence[ChatMessage] | Sequence[dict[str, Any]]
    ) -> IntegrationResult[int]:
        return IntegrationResult.error_result(
            "token counting is not available through the hosted client"
        )


def _refuse_unsupported(options: LLMOptions, *, stop_allowed: bool) -> None:
    unsupported = {
        "functions": options.functions,
        "tools": options.tools,
        "frequency_penalty": options.frequency_penalty or None,
        "presence_penalty": options.presence_penalty or None,
        "stop": None if stop_allowed else options.stop,
        "stream": options.stream or None,
    }
    named = sorted(name for name, value in unsupported.items() if value)
    if named:
        raise ValueError("the hosted chat contract can't carry: " + ", ".join(named))
    if options.max_tokens is None or options.max_tokens < 1:
        raise ValueError("max_tokens is required: it bounds the call's reservation")


def _role(message: ChatMessage) -> str:
    role = (
        message.role.value if isinstance(message.role, RoleType) else str(message.role)
    )
    if role not in ("system", "user", "assistant"):
        raise ValueError(
            f"the hosted chat contract carries text messages only, not {role}"
        )
    if message.content is None or message.function_call or message.tool_calls:
        raise ValueError("the hosted chat contract carries text content only")
    return role


def _openai_body(
    messages: list[ChatMessage], options: LLMOptions, model: str
) -> dict[str, Any]:
    _refuse_unsupported(options, stop_allowed=False)
    _same_model(options, model)
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": _role(item), "content": item.content} for item in messages
        ],
        "max_completion_tokens": options.max_tokens,
        "store": False,
        "temperature": options.temperature,
    }
    if options.top_p != 1.0:
        body["top_p"] = options.top_p
    if options.seed is not None:
        body["seed"] = options.seed
    if options.response_format is not None:
        body["response_format"] = options.response_format
    return body


def _anthropic_body(
    messages: list[ChatMessage], options: LLMOptions, model: str
) -> dict[str, Any]:
    _refuse_unsupported(options, stop_allowed=True)
    _same_model(options, model)
    if options.response_format is not None or options.seed is not None:
        raise ValueError(
            "the hosted Anthropic contract carries no response_format or seed"
        )
    system = [item for item in messages if _role(item) == "system"]
    turns = [item for item in messages if _role(item) != "system"]
    if len(system) > 1:
        raise ValueError("one system message at most; it is never merged for you")
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": options.max_tokens,
        "messages": [{"role": _role(item), "content": item.content} for item in turns],
        "temperature": options.temperature,
    }
    if system:
        body["system"] = system[0].content
    if options.top_p != 1.0:
        body["top_p"] = options.top_p
    if options.stop:
        body["stop_sequences"] = list(options.stop)
    return body


def _same_model(options: LLMOptions, model: str) -> None:
    if options.model is not None and options.model != model:
        raise ValueError("this client sends only its connection's model; no swaps")


def _text_of(operation: str, payload: object) -> str:
    try:
        if operation == "anthropic.messages.create":
            blocks = payload["content"]  # type: ignore[index]
            return "".join(
                block["text"] for block in blocks if block.get("type") == "text"
            )
        return str(payload["choices"][0]["message"]["content"])  # type: ignore[index]
    except KeyError, IndexError, TypeError:
        raise HostedLLMError(
            "invalid_response", "the provider answer has no text", retry="none"
        ) from None


__all__ = [
    "MAX_BODY_BYTES",
    "HostedChatOnce",
    "HostedLLMChat",
    "HostedLLMError",
    "HostedLLMResponse",
    "LLMOperation",
    "request_key",
]

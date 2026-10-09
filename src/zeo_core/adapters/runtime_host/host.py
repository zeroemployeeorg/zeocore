"""One supervised attempt, using the existing typed capability invoker."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, JsonValue

from zeo_core.adapters.runtime_host.canonical import (
    InvalidRequestError,
    ProtocolError,
    canonical_bytes,
    digest,
    parse_json,
)
from zeo_core.adapters.runtime_host.catalogue import (
    CandidateCatalogue,
    Generation,
    installed_factory,
    validate_schema,
)
from zeo_core.adapters.runtime_host.channel import RuntimeChannel
from zeo_core.contracts import CapabilityOutcome, CapabilityResult
from zeo_core.contracts.runtime import (
    EXIT_CODES,
    AttemptBinding,
    EffectRequest,
    HostResult,
    InvocationRequest,
    LaunchContext,
    RuntimeReply,
)
from zeo_core.core.managed_execution import enter_managed_execution
from zeo_core.tools import CapabilityRegistry, ToolContext
from zeo_core.tools.invoke import BoundCapability, invoke_async


class EffectPort:
    """No credentials or provider dispatch; Runtime owns consumption and replay."""

    def __init__(self, channel: RuntimeChannel) -> None:
        self._channel = channel
        self._lock = threading.Lock()
        self._requests: dict[str, str] = {}
        self.unknown = False
        self.confirmed = False
        self.pending: RuntimeReply | None = None

    def request(self, request: EffectRequest) -> dict[str, JsonValue]:
        with self._lock:
            return self._request(request)

    def _request(self, request: EffectRequest) -> dict[str, JsonValue]:
        if self.pending is not None or self.unknown:
            raise ProtocolError("effect observation requires Runtime recovery")
        fingerprint = digest(request.model_dump(mode="json"))
        prior = self._requests.get(request.logical_effect_id)
        if prior is not None and prior != fingerprint:
            raise ProtocolError("logical effect ID reused with changed input")
        if (
            prior is None
            and len(self._requests) >= self._channel.context.total_dispatch_budget
        ):
            raise ProtocolError("managed effect budget exhausted")
        self._requests[request.logical_effect_id] = fingerprint
        # Once handed to Runtime, a lost reply is ambiguous, even on a read.
        self.unknown = True
        reply = self._channel.call("effect.request", request.model_dump(mode="json"))
        if reply.state != "allowed":
            self.pending = reply
            # A waiting/refused/cancelled reply means this request was not
            # consumed. Unknown prior effects require needs_reconciliation.
            self.unknown = reply.state == "needs_reconciliation"
            raise ProtocolError("effect not completed")
        if (
            reply.data.get("logical_effect_id") != request.logical_effect_id
            or reply.data.get("effect_request_digest") != fingerprint
            or not isinstance(reply.data.get("receipt_id"), str)
            or not reply.data["receipt_id"]
            or not isinstance(reply.data.get("authorization_id"), str)
            or not reply.data["authorization_id"]
            or reply.data.get("effect_disposition") != "confirmed"
        ):
            raise ProtocolError("effect receipt binding missing")
        self.unknown = False
        self.confirmed = True
        return reply.data


def _pending(reply: RuntimeReply, effects: EffectPort) -> HostResult:
    state = reply.state
    if state == "allowed":
        raise ProtocolError("allowed is not an operation result")
    if state == "needs_reconciliation":
        effects.unknown = True
    return HostResult(
        protocol_version=1,
        binding=reply.binding,
        state=state,
        effect_disposition="unknown"
        if effects.unknown
        else ("confirmed" if effects.confirmed else "none"),
        error_code="ZEO_HOST_REFUSED" if state == "refused" else None,
    )


def prepare_request(capability: BoundCapability, raw: InvocationRequest) -> BaseModel:
    """Validate the wire shape before Pydantic defaults and business validators."""
    try:
        validate_schema(capability.definition.request_schema).validate(raw.arguments)
        return capability.request_model.model_validate(raw.arguments)
    except Exception as exc:
        raise InvalidRequestError("business request failed validation") from exc


class ManagedHost:
    def __init__(self, context: LaunchContext, channel: RuntimeChannel) -> None:
        if channel.context != context:
            raise ProtocolError("host/channel context mismatch")
        self.context = context
        self.channel = channel
        self._used = False

    def invoke(
        self,
        request: InvocationRequest,
        *,
        factory: Callable[[], CapabilityRegistry] | None = None,
    ) -> HostResult:
        """The CLI and Python host API use this same admission path.

        An injected factory is for an already allowlisted in-process provider;
        it cannot change the admitted manifest or bypass Runtime redemption.
        """
        context = self.context
        effects = EffectPort(self.channel)
        try:
            if self._used:
                raise ProtocolError("host attempt already used")
            self._used = True
            enter_managed_execution()
            if (
                request.capability_id != context.attempt.capability_id
                or request.capability_id not in context.admitted_capabilities
                or context.attempt.manifest_digest != context.provider.manifest_digest
            ):
                return HostResult(
                    protocol_version=1,
                    binding=context.attempt,
                    state="refused",
                    effect_disposition="none",
                    error_code="ZEO_HOST_SCOPE",
                )
            if time.time() * 1000 >= context.deadline_unix_ms:
                return HostResult(
                    protocol_version=1,
                    binding=context.attempt,
                    state="timed_out",
                    effect_disposition="none",
                    error_code="ZEO_HOST_DEADLINE",
                )
            bootstrap = self.channel.bootstrap()
            if bootstrap.state != "allowed":
                return _pending(bootstrap, effects)
            catalogue = CandidateCatalogue()
            generation = catalogue.activate(
                context.provider, factory or installed_factory(context.provider)
            )
            try:
                return self._execute(generation, request, effects)
            finally:
                catalogue.dispose(generation)
        except InvalidRequestError:
            return HostResult(
                protocol_version=1,
                binding=context.attempt,
                state="invalid_request",
                effect_disposition="none",
                error_code="ZEO_HOST_INVALID_REQUEST",
            )
        except Exception:
            # Never emit exception messages/tracebacks: providers may embed secrets.
            return HostResult(
                protocol_version=1,
                binding=context.attempt,
                state="needs_reconciliation" if effects.unknown else "protocol_error",
                effect_disposition="unknown"
                if effects.unknown
                else ("confirmed" if effects.confirmed else "none"),
                error_code="ZEO_HOST_PROTOCOL",
            )

    def _execute(
        self, generation: Generation, request: InvocationRequest, effects: EffectPort
    ) -> HostResult:
        context = self.context
        capability = generation.capabilities.get(request.capability_id)
        if capability is None:
            raise ProtocolError("exact capability identity absent")
        validated = prepare_request(capability, request)
        arguments = validated.model_dump(mode="json")
        if (
            digest({"capability_id": request.capability_id, "arguments": arguments})
            != context.attempt.request_digest
        ):
            return HostResult(
                protocol_version=1,
                binding=context.attempt,
                state="refused",
                effect_disposition="none",
                error_code="ZEO_HOST_REQUEST_BINDING",
            )
        if (
            "organization_id" in arguments
            and arguments["organization_id"] != context.attempt.organization_id
        ):
            return HostResult(
                protocol_version=1,
                binding=context.attempt,
                state="refused",
                effect_disposition="none",
                error_code="ZEO_HOST_ORGANIZATION",
            )
        # The first host only injects the governed effect port. Missing
        # services refuse rather than creating ambient integrations.
        services: dict[str, Any] = {"runtime.effects": effects}
        requirements = capability.definition.requirements
        missing = (
            requirements.services - set(context.services)
            or requirements.services - services.keys()
            or requirements.credentials - set(context.credentials)
            or requirements.binaries - set(context.binaries)
            or requirements.network.hosts - set(context.network_hosts)
            or (requirements.network.required and not context.network_allowed)
            or requirements.filesystem.read
            or requirements.filesystem.write
        )
        if missing:
            return HostResult(
                protocol_version=1,
                binding=context.attempt,
                state="unavailable",
                effect_disposition="none",
                error_code="ZEO_HOST_REQUIREMENTS",
            )
        ctx = ToolContext(
            run_id=context.attempt.operation_id,
            tool_name=capability.definition.id.name,
            tool_version=capability.definition.id.version,
            logger=logging.getLogger("zeo.host"),
            fs=None,
            work_dir=context.workspace,
            output_dir=context.workspace,
            services=services,
            metadata={
                "managed_execution": True,
                "attempt_id": context.attempt.attempt_id,
            },
        )
        # A second live check after provider import/validation catches
        # revocation between bootstrap and actual invocation.
        admitted = self.channel.call("invoke.admit", {"arguments": arguments})
        if admitted.state != "allowed":
            return _pending(admitted, effects)
        result = asyncio.run(invoke_async(capability, validated, ctx))
        if effects.pending:
            return _pending(effects.pending, effects)
        if effects.unknown:
            return HostResult(
                protocol_version=1,
                binding=context.attempt,
                state="needs_reconciliation",
                effect_disposition="unknown",
            )
        return self._finish(capability, result, effects)

    def _finish(
        self,
        capability: BoundCapability,
        result: CapabilityResult[Any],
        effects: EffectPort,
    ) -> HostResult:
        context = self.context
        if result.outcome != CapabilityOutcome.success:
            state = (
                "unavailable"
                if result.outcome == CapabilityOutcome.unavailable
                else "failed"
            )
            if result.outcome == CapabilityOutcome.cancelled:
                state = "cancelled"
            return HostResult.model_validate(
                {
                    "protocol_version": 1,
                    "binding": context.attempt,
                    "state": state,
                    "effect_disposition": "confirmed" if effects.confirmed else "none",
                    "error_code": result.machine_message
                    or "ZEO_HOST_CAPABILITY_FAILED",
                }
            )
        data = (
            result.data.model_dump(mode="json")
            if isinstance(result.data, BaseModel)
            else result.data
        )
        validate_schema(capability.definition.response_schema).validate(data)
        # Runtime rechecks fence/deadline and owns immutable artifact
        # publication. A local successful return is only a candidate.
        published = self.channel.call(
            "result.publish", {"data": data, "data_digest": digest(data)}
        )
        if published.state != "allowed":
            return _pending(published, effects)
        refs = published.data.get("artifact_refs")
        if (
            published.data.get("data_digest") != digest(data)
            or not isinstance(refs, list)
            or not refs
            or not all(isinstance(ref, str) and ref for ref in refs)
        ):
            raise ProtocolError("Runtime did not accept a bound artifact")
        return HostResult.model_validate_json(
            canonical_bytes(
                {
                    "protocol_version": 1,
                    "binding": context.attempt.model_dump(mode="json"),
                    "state": "succeeded",
                    "effect_disposition": "confirmed" if effects.confirmed else "none",
                    "artifact_refs": refs,
                }
            )
        )


def parse_result(raw: bytes, exit_code: int, expected: AttemptBinding) -> HostResult:
    """Every exit code is parsed; protocol disagreement never becomes a retry."""
    parse_json(raw)
    result = HostResult.model_validate_json(raw)
    if result.binding != expected or EXIT_CODES[result.state] != exit_code:
        raise ProtocolError(
            "result identity or exit/state disagreement; retain durable evidence"
        )
    return result


__all__ = [
    "EffectPort",
    "ManagedHost",
    "parse_result",
    "prepare_request",
]

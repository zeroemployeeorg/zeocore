"""Nano Banana and Recraft through ZEOconnect: the provider key stays in custody.

Wire: proposed Broker contract 1.3.0 (ZEOCORE-SOW-12 §3, as changed by
ZEOconnect). Inputs are uploaded first, then one billed operation runs under
an idempotency key derived from the request, so asking for the same image
again is an exact replay of the stored outcome and never bills twice.
"""

from __future__ import annotations

import uuid

from pydantic import ValidationError

from zeo_core.contracts.connections import NormalizedErrorCode
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedOperationStatus,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
    is_outage,
    stop_of,
)
from zeo_core.integrations.hosted.services import HostedServiceBinding

from .models import (
    CREDITS_OPERATION,
    OPERATIONS,
    BaseImageRequest,
    Cost,
    CreditBalance,
    GeminiGenerate,
    GeneratedImage,
    ImageInput,
    ImagingError,
    check_output,
    provider_of,
    sha256_hex,
)

GEMINI_OPERATIONS = frozenset({OPERATIONS["gemini.generate"]})
RECRAFT_OPERATIONS = frozenset(
    {
        *(op for kind, op in OPERATIONS.items() if kind.startswith("recraft.")),
        CREDITS_OPERATION,
    }
)


class _HostedImages:
    """One provider connection. Produced images can feed the next call directly."""

    _provider: str
    _operations: frozenset[str]

    def __init__(
        self, *, client: HostedConnectionClient, binding: HostedServiceBinding
    ) -> None:
        self._client = client
        self._binding = binding
        # Bytes this connection already holds, by sha256: what it produced in
        # this process. Ids never cross connections, so this map is per proxy.
        self._held: dict[str, str] = {}

    def run(self, request: BaseImageRequest) -> GeneratedImage:
        if provider_of(request) != self._provider:
            raise ValueError(f"not a {self._provider} image request")
        try:
            return self._run(request)
        except ImagingError as error:
            error.request_key = error.request_key or request.idempotency_key()
            if error.outcome == "input_unavailable":
                # A held id may have expired; the next attempt uploads again.
                self._held.clear()
            raise

    def _run(self, request: BaseImageRequest) -> GeneratedImage:
        operation = OPERATIONS[request.kind]
        arguments = request.arguments()
        ids = [self._input_id(item) for item in request.input_images()]
        if isinstance(request, GeminiGenerate):
            arguments["input_artifact_ids"] = ids
        elif ids:
            arguments["input_artifact_id"] = ids[0]
        response = self._invoke(operation, arguments, request.idempotency_key())
        return self._image(operation, response).model_copy(
            update={
                "request_key": request.idempotency_key(),
                "inputs": tuple(item.provenance() for item in request.input_images()),
            }
        )

    def _input_id(self, item: ImageInput) -> str:
        held = self._held.get(item.sha256)
        if held is not None:
            return held
        try:
            artifact = self._client.upload_artifact(
                connection_id=self._binding.connection_id,
                content=item.content,
                media_type=item.media_type,
            )
        except HostedUnavailableError:
            raise ImagingError(
                "unavailable", "ZEOconnect could not take the input image"
            ) from None
        except HostedClientError as error:
            raise ImagingError("refused", f"input upload failed: {error}") from None
        return artifact.artifact_id

    def _invoke(
        self, operation: str, arguments: dict[str, object], key: str
    ) -> HostedOperationResponse:
        try:
            return self._client.invoke(
                HostedOperationRequest(
                    connection_id=self._binding.connection_id,
                    connector_revision=self._binding.connector_revision,
                    operation_id=operation,
                    arguments=arguments,
                    idempotency_key=key,
                )
            )
        except HostedStoppedError as error:
            raise ImagingError("stopped", str(error)) from None
        except HostedUnreachableError as error:
            if error.may_have_arrived:
                # The same request is an exact replay, so asking again is safe
                # and never bills twice.
                raise ImagingError(
                    "ambiguous",
                    "ZEOconnect did not answer; the same request replays the"
                    " stored outcome",
                ) from None
            raise ImagingError("unavailable", str(error)) from None
        except HostedUnavailableError as error:
            raise ImagingError("unavailable", str(error)) from None
        except HostedClientError as error:
            raise ImagingError("refused", str(error), retry="none") from None

    def _image(
        self, operation: str, response: HostedOperationResponse
    ) -> GeneratedImage:
        _raise_unless_confirmed(response)
        if response.artifact is None:
            expired = response.result
            if isinstance(expired, dict) and expired.get("artifact_expired") is True:
                # A replay after the Broker dropped the bytes (contract 1.3.0
                # §6): the call succeeded once and is never regenerated.
                digest = expired.get("content_sha256")
                raise ImagingError(
                    "artifact_expired",
                    "this image was made earlier, but ZEOconnect no longer holds"
                    " its bytes; a new image needs a new occurrence",
                    retry="new_occurrence",
                    content_sha256=digest if isinstance(digest, str) else None,
                )
            raise ImagingError("invalid_response", "the answer carried no image")
        try:
            content = self._client.download_artifact(response.artifact)
        except HostedClientError as error:
            raise ImagingError("unavailable", str(error)) from None
        media_type = check_output(content, response.artifact.media_type)
        self._held[sha256_hex(content)] = response.artifact.artifact_id
        receipt = response.receipt or {}
        return GeneratedImage(
            content=content,
            media_type=media_type,
            sha256=sha256_hex(content),
            provider=self._provider,
            operation=operation,
            profile="hosted",
            model=_text(receipt.get("model")),
            provider_image_id=_text(receipt.get("provider_image_id")),
            cost=_cost(receipt.get("cost")),
            execution_id=response.execution_id,
            replayed=receipt.get("replayed") is True,
        )


class HostedGeminiImages(_HostedImages):
    """Nano Banana through one ZEOconnect Gemini connection."""

    _provider = "gemini"
    _operations = GEMINI_OPERATIONS


class HostedRecraftImages(_HostedImages):
    """Recraft through one ZEOconnect Recraft connection."""

    _provider = "recraft"
    _operations = RECRAFT_OPERATIONS

    def credits(self) -> CreditBalance:
        """A read: it bills nothing, so each call takes a fresh key."""
        response = self._invoke(CREDITS_OPERATION, {}, f"zi-read-{uuid.uuid4()}")
        _raise_unless_confirmed(response)
        result = response.result
        balance = result.get("credits") if isinstance(result, dict) else None
        if isinstance(balance, bool) or not isinstance(balance, int | float):
            raise ImagingError("invalid_response", "the answer carried no balance")
        return CreditBalance(credits=balance)


def _raise_unless_confirmed(response: HostedOperationResponse) -> None:
    status = response.status
    if status is HostedOperationStatus.CONFIRMED:
        return
    if status is HostedOperationStatus.APPROVAL_REQUIRED:
        raise ImagingError(
            "approval_required",
            "approve this call in ZEOconnect, then ask again",
            approval_url=str(response.approval_url),
        )
    if status is HostedOperationStatus.AMBIGUOUS:
        if (response.receipt or {}).get("in_flight") is True:
            raise ImagingError(
                "ambiguous",
                "the first call for this request is still running; ask again later",
            )
        raise ImagingError(
            "ambiguous",
            "the call may have run and been billed; the same request replays"
            " the stored outcome",
        )
    stop = stop_of(response)
    if stop is not None:
        raise ImagingError("stopped", str(stop))
    error = response.normalized_error
    code = error.code if error is not None else None
    if is_outage(response) or code is NormalizedErrorCode.RATE_LIMITED:
        # Recorded against the key: the same request replays this failure.
        raise ImagingError(
            "unavailable",
            "the provider or a control was unavailable; try again as a new occurrence",
            retry="new_occurrence",
        )
    if code is NormalizedErrorCode.BUDGET_EXHAUSTED:
        raise ImagingError(
            "budget_exhausted",
            "the connection's budget can't cover this call; raise it in ZEOconnect",
        )
    if (
        code is NormalizedErrorCode.REQUEST_REFUSED
        and error is not None
        and error.message == "storage_capacity_exceeded"
    ):
        # Refused before any reservation, and not recorded (1.3.0 §5.1).
        raise ImagingError(
            "unavailable", "ZEOconnect is out of image storage; try again later"
        )
    if code is NormalizedErrorCode.INPUT_ARTIFACT_UNAVAILABLE:
        raise ImagingError(
            "input_unavailable", "an input image expired or belongs elsewhere"
        )
    raise ImagingError(
        "refused", "the image call was refused; nothing was produced", retry="none"
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 512 else None


def _cost(value: object) -> Cost | None:
    if not isinstance(value, dict):
        return None
    try:
        return Cost.model_validate(value)
    except ValidationError:
        return None


__all__ = [
    "GEMINI_OPERATIONS",
    "RECRAFT_OPERATIONS",
    "HostedGeminiImages",
    "HostedRecraftImages",
]

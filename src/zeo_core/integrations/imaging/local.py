"""Nano Banana and Recraft with the caller's own keys: the local profile.

The same requests and results as the hosted profile. There is no Broker, so
there is no replay: a repeated call is a new call and may bill again, and a
timeout cannot be resolved. Calls are never retried automatically.

Offline contract only: built from the providers' published APIs and
DuckTyper's working calls, not yet run against a live account here.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from pydantic import SecretStr

from .models import (
    CREDITS_OPERATION,
    MAX_IMAGE_BYTES,
    OPERATIONS,
    BaseImageRequest,
    Cost,
    CreditBalance,
    GeminiGenerate,
    GeneratedImage,
    ImagingError,
    RecraftGenerate,
    RecraftImageToImage,
    RecraftRemoveBackground,
    RecraftVectorize,
    check_output,
    provider_of,
    sha256_hex,
    sniff_media_type,
)

GEMINI_ORIGIN = "https://generativelanguage.googleapis.com"
GEMINI_PATH = "/v1beta/interactions"
RECRAFT_ORIGIN = "https://external.api.recraft.ai"
_MAX_RESPONSE_BYTES = 3 * MAX_IMAGE_BYTES  # base64 plus the JSON around it
_TIMEOUT = httpx.Timeout(180.0, connect=15.0)


@contextmanager
def _keyed(request: BaseImageRequest) -> Iterator[None]:
    """Every error names the request it is about."""
    try:
        yield
    except ImagingError as error:
        error.request_key = error.request_key or request.idempotency_key()
        raise


def _client(origin: str, http_client: httpx.Client | None) -> httpx.Client:
    return http_client or httpx.Client(
        base_url=origin, timeout=_TIMEOUT, follow_redirects=False, trust_env=False
    )


def _send(client: httpx.Client, request: httpx.Request) -> dict[str, Any]:
    """One attempt. Anything that may have reached the provider is ambiguous."""
    try:
        response = client.send(request)
    except httpx.TimeoutException:
        raise ImagingError(
            "ambiguous",
            "the provider did not answer in time; it may have billed",
            retry="new_occurrence",
        ) from None
    except httpx.TransportError:
        raise ImagingError("unavailable", "the provider could not be reached") from None
    status = response.status_code
    if response.is_redirect:
        raise ImagingError("invalid_response", "the provider redirected")
    if status == 429:
        raise ImagingError("unavailable", "the provider is rate limiting this key")
    if status >= 500:
        raise ImagingError(
            "ambiguous",
            f"the provider failed with HTTP {status}; it may have billed",
            retry="new_occurrence",
        )
    if status >= 400:
        # The body can quote the prompt or the key's project; it is dropped.
        raise ImagingError("refused", f"the provider refused with HTTP {status}")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise ImagingError("invalid_response", "the provider answer is too large")
    try:
        payload = response.json()
    except ValueError:
        raise ImagingError(
            "invalid_response", "the provider answer is not JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ImagingError("invalid_response", "the provider answer is not an object")
    return payload


def _decode(data: object) -> bytes:
    if not isinstance(data, str):
        raise ImagingError("invalid_response", "the provider answer has no image")
    try:
        return base64.b64decode(data, validate=True)
    except ValueError:
        raise ImagingError("invalid_response", "the image is not base64") from None


def _key(value: SecretStr | str | None, variable: str) -> SecretStr:
    if value is None:
        value = os.environ.get(variable)
    if isinstance(value, str):
        value = SecretStr(value.strip())
    if value is None or not value.get_secret_value():
        raise ImagingError("refused", f"{variable} is not set for the local profile")
    return value


class LocalGeminiImages:
    """Nano Banana via the Interactions API, keyed by ``GEMINI_API_KEY``.

    The shape DuckTyper runs live: text plus reference images in, one image
    out in the requested media type. An answer in another type is refused,
    never relabelled.
    """

    def __init__(
        self,
        api_key: SecretStr | str | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._key = _key(api_key, "GEMINI_API_KEY")
        self._client = _client(GEMINI_ORIGIN, http_client)

    def run(self, request: BaseImageRequest) -> GeneratedImage:
        if not isinstance(request, GeminiGenerate):
            raise ValueError("not a gemini image request")
        with _keyed(request):
            return self._run(request)

    def _run(self, request: GeminiGenerate) -> GeneratedImage:
        content: list[dict[str, object]] = [{"type": "text", "text": request.prompt}]
        content.extend(
            {
                "type": "image",
                "mime_type": item.media_type,
                "data": base64.b64encode(item.content).decode(),
            }
            for item in request.inputs
        )
        body = {
            "model": request.model,
            "input": content,
            "response_format": {
                "type": "image",
                "mime_type": request.output_media_type,
                "aspect_ratio": request.aspect_ratio,
                "image_size": request.image_size,
            },
        }
        payload = _send(
            self._client,
            self._client.build_request(
                "POST",
                GEMINI_PATH,
                json=body,
                headers={"x-goog-api-key": self._key.get_secret_value()},
            ),
        )
        image = _gemini_image(payload)
        content_bytes = _decode(image.get("data"))
        return GeneratedImage(
            content=content_bytes,
            media_type=check_output(content_bytes, request.output_media_type),
            sha256=sha256_hex(content_bytes),
            provider="gemini",
            operation=OPERATIONS[request.kind],
            profile="local",
            model=request.model,
            request_key=request.idempotency_key(),
        )


def _gemini_image(payload: object) -> dict[str, Any]:
    """The first image the model produced: model output steps first, then any."""
    if isinstance(payload, dict):
        for step in payload.get("steps") or ():
            if isinstance(step, dict) and step.get("type") == "model_output":
                for item in step.get("content") or ():
                    if isinstance(item, dict) and item.get("type") == "image":
                        return item
    found = _find_image(payload, depth=0)
    if found is None:
        raise ImagingError(
            "refused", "the model returned no image (it may have declined the prompt)"
        )
    return found


def _find_image(value: object, *, depth: int) -> dict[str, Any] | None:
    if depth > 8:
        return None
    if isinstance(value, dict):
        media = value.get("mime_type") or value.get("mimeType")
        if isinstance(media, str) and media.startswith("image/") and "data" in value:
            return value
        children: Any = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = _find_image(child, depth=depth + 1)
        if found is not None:
            return found
    return None


class LocalRecraftImages:
    """Recraft's v1 API, keyed by ``RECRAFT_API_KEY``. Images come back inline."""

    def __init__(
        self,
        api_key: SecretStr | str | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._key = _key(api_key, "RECRAFT_API_KEY")
        self._client = _client(RECRAFT_ORIGIN, http_client)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self._key.get_secret_value()}

    def run(self, request: BaseImageRequest) -> GeneratedImage:
        if provider_of(request) != "recraft":
            raise ValueError("not a recraft image request")
        with _keyed(request):
            return self._run(request)

    def _run(self, request: BaseImageRequest) -> GeneratedImage:
        build = self._client.build_request
        if isinstance(request, RecraftGenerate):
            body = {**request.arguments(), "n": 1, "response_format": "b64_json"}
            http = build(
                "POST", "/v1/images/generations", json=body, headers=self._headers()
            )
        elif isinstance(request, RecraftImageToImage):
            fields = {**request.arguments(), "n": 1, "response_format": "b64_json"}
            http = build(
                "POST",
                "/v1/images/imageToImage",
                data={name: str(value) for name, value in fields.items()},
                files={
                    "image": ("image", request.input.content, request.input.media_type)
                },
                headers=self._headers(),
            )
        elif isinstance(request, RecraftRemoveBackground | RecraftVectorize):
            path = (
                "/v1/images/removeBackground"
                if isinstance(request, RecraftRemoveBackground)
                else "/v1/images/vectorize"
            )
            http = build(
                "POST",
                path,
                data={"response_format": "b64_json"},
                files={
                    "file": ("image", request.input.content, request.input.media_type)
                },
                headers=self._headers(),
            )
        else:
            raise ValueError("not a recraft image request")
        payload = _send(self._client, http)
        image = _recraft_image(payload)
        content = _decode(image.get("b64_json"))
        media_type = (
            "image/svg+xml"
            if isinstance(request, RecraftVectorize) or _looks_like_svg(content)
            else sniff_media_type(content) or "image/png"
        )
        image_id = image.get("image_id")
        return GeneratedImage(
            content=content,
            media_type=check_output(content, media_type),
            sha256=sha256_hex(content),
            provider="recraft",
            operation=OPERATIONS[request.kind],
            profile="local",
            model=getattr(request, "model", None),
            provider_image_id=image_id if isinstance(image_id, str) else None,
            cost=_credits(payload.get("credits")),
            request_key=request.idempotency_key(),
        )

    def credits(self) -> CreditBalance:
        payload = _send(
            self._client,
            self._client.build_request("GET", "/v1/users/me", headers=self._headers()),
        )
        balance = payload.get("credits")
        if isinstance(balance, bool) or not isinstance(balance, int | float):
            raise ImagingError("invalid_response", "the answer carried no balance")
        return CreditBalance(credits=balance)


def _recraft_image(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    image = payload.get("image")
    if isinstance(image, dict):
        return image
    raise ImagingError("invalid_response", "the provider answer has no image")


def _looks_like_svg(content: bytes) -> bool:
    head = content.lstrip()[:256].lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _credits(value: object) -> Cost | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return Cost(unit="recraft_credits", settled=float(value))


__all__ = ["CREDITS_OPERATION", "LocalGeminiImages", "LocalRecraftImages"]

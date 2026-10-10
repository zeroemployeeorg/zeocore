"""``zeocore image``: one image call as JSON, for callers outside Python.

Reads one JSON object on stdin and writes one JSON object on stdout, under
the ``zeocore`` command's rules (``zeo_core.cli``). Image bytes never go to
stdout: the image is written to ``output`` and the answer names it.

    {"request": {"kind": "recraft.vectorize", "input": {"path": "duck.png"}},
     "output": "duck.svg"}
    {"credits": true}

Input images are given as ``{"path": ...}``; the bytes are read and checked
here. The profile is ``ZEOCORE_CONNECTION_PROFILE`` (``local`` or ``hosted``);
hosted uses this device's ZEOconnect grant (``zeocore login``, chosen by
``ZEOCORE_PROFILE``), the origin in ``ZEOCONNECT_URL``, and the connection
ids in ``ZEOCORE_IMAGING_GEMINI_CONNECTION`` /
``ZEOCORE_IMAGING_RECRAFT_CONNECTION``.

Exit status is the ``zeocore`` family (see ``EXIT_FOR``): 0 done, 2 invalid
request, 10 approval, 11 waiting, 12 not paired, 13 ambiguous, 20 held or
refused. ``outcome`` and ``retry`` say more.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import GeneratedImage, ImageInput, ImageRequest, ImagingError
from .service import ImagingService, build_imaging

#: Each outcome's exit status, in the zeocore family (zeo_core.cli).
EXIT_FOR: dict[str, int] = {
    "approval_required": 10,
    "in_flight": 11,
    "unavailable": 11,
    "input_unavailable": 11,
    "not_paired": 12,
    "ambiguous": 13,
}
EXIT_HELD = 20

_REQUEST: TypeAdapter[Any] = TypeAdapter(ImageRequest)


def _inputs(request: dict[str, Any]) -> dict[str, Any]:
    def load(item: object) -> ImageInput:
        if not isinstance(item, dict) or not {"path"} <= set(item) <= {"path", "role"}:
            raise ValueError('each input image is {"path": ..., "role"?: ...}')
        role = item.get("role")
        if role is not None and not isinstance(role, str):
            raise ValueError("an input role is a string")
        return ImageInput.from_path(str(item["path"]), role=role)

    request = dict(request)
    if "input" in request:
        request["input"] = load(request["input"])
    if "inputs" in request:
        if not isinstance(request["inputs"], list):
            raise ValueError("inputs is a list")
        request["inputs"] = tuple(load(item) for item in request["inputs"])
    return request


def _hosted_service() -> ImagingService:
    from zeo_core.integrations.hosted.client import HostedConnectionClient
    from zeo_core.integrations.hosted.pairing import KeychainSecureSessionStore
    from zeo_core.integrations.hosted.transport import (
        ZEOCONNECT_PRODUCTION_ORIGIN,
        ZEOconnectHTTPTransport,
    )

    transport = ZEOconnectHTTPTransport(
        session_store=KeychainSecureSessionStore(
            profile=os.getenv("ZEOCORE_PROFILE") or None
        ),
        base_url=os.getenv("ZEOCONNECT_URL", ZEOCONNECT_PRODUCTION_ORIGIN),
        allow_development_origin=os.getenv("ZEOCONNECT_DEVELOPMENT") == "1",
    )
    return build_imaging(
        profile="hosted", hosted_client=HostedConnectionClient(transport=transport)
    )


def _summary(image: GeneratedImage, path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "path": str(path),
        **image.model_dump(mode="json", exclude={"content"}),
    }


def run(
    command: object, *, service_factory: Callable[[], ImagingService] | None = None
) -> tuple[int, dict[str, Any]]:
    """Run one parsed command; ``zeocore image`` parses stdin strictly first."""
    try:
        if not isinstance(command, dict):
            raise ValueError("the command is a JSON object")
        if command.get("credits") is True and set(command) == {"credits"}:
            request = None
        else:
            if set(command) != {"request", "output"}:
                raise ValueError('the command is {"request": ..., "output": ...}')
            request = _REQUEST.validate_python(_inputs(command["request"]))
            output = Path(str(command["output"]))
            if not output.parent.is_dir():
                raise ValueError("the output directory does not exist")
    except (ValueError, ValidationError, OSError) as error:
        message = (
            "the request does not match the image contract"
            if isinstance(error, ValidationError)
            else str(error)
        )
        return 2, {"ok": False, "outcome": "invalid_request", "message": message}
    factory = service_factory or (
        _hosted_service
        if os.getenv("ZEOCORE_CONNECTION_PROFILE") == "hosted"
        else lambda: build_imaging(profile="local")
    )
    try:
        service = factory()
        if request is None:
            return 0, {"ok": True, **service.credits().model_dump(mode="json")}
        image = service.run(request)
        return 0, _summary(image, image.save(output))
    except ImagingError as error:
        return EXIT_FOR.get(error.outcome, EXIT_HELD), {
            "ok": False,
            "content_sha256": error.content_sha256,
            "outcome": error.outcome,
            "message": str(error),
            "retry": error.retry,
            "request_key": error.request_key,
            "approval_url": error.approval_url,
        }

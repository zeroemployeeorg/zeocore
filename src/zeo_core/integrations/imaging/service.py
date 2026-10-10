"""One entry point over Nano Banana and Recraft, in either profile."""

from __future__ import annotations

import os
from typing import Protocol

from zeo_core.integrations.hosted.client import HostedConnectionClient
from zeo_core.integrations.hosted.services import HostedServiceBinding

from .models import (
    BaseImageRequest,
    CreditBalance,
    GeneratedImage,
    ImagingError,
    provider_of,
)


class ImageBackend(Protocol):
    """One provider, local or hosted."""

    def run(self, request: BaseImageRequest) -> GeneratedImage: ...


class RecraftBackend(ImageBackend, Protocol):
    def credits(self) -> CreditBalance: ...


class ImagingService:
    """``run`` takes any image request and sends it to its provider.

    A provider left unconfigured refuses its requests; the other still works.
    """

    def __init__(
        self,
        *,
        gemini: ImageBackend | None = None,
        recraft: RecraftBackend | None = None,
    ) -> None:
        self._gemini = gemini
        self._recraft = recraft

    def run(self, request: BaseImageRequest) -> GeneratedImage:
        backend = self._gemini if provider_of(request) == "gemini" else self._recraft
        if backend is None:
            raise ImagingError(
                "refused", f"{provider_of(request)} is not configured for imaging"
            )
        return backend.run(request)

    def credits(self) -> CreditBalance:
        if self._recraft is None:
            raise ImagingError("refused", "recraft is not configured for imaging")
        return self._recraft.credits()


def build_imaging(
    *,
    profile: str | None = None,
    hosted_client: HostedConnectionClient | None = None,
    gemini_connection: str | None = None,
    recraft_connection: str | None = None,
) -> ImagingService:
    """Compose the imaging service for ``profile`` (``ZEOCORE_CONNECTION_PROFILE``).

    Hosted: each provider is a ZEOconnect connection id, given here or as
    ``ZEOCORE_IMAGING_GEMINI_CONNECTION`` / ``ZEOCORE_IMAGING_RECRAFT_CONNECTION``.
    Local: each provider whose key is set (``GEMINI_API_KEY``,
    ``RECRAFT_API_KEY``) is configured. Nothing is contacted here.
    """

    selected = profile or os.getenv("ZEOCORE_CONNECTION_PROFILE", "local")
    if selected == "hosted":
        from .hosted import HostedGeminiImages, HostedRecraftImages

        if hosted_client is None:
            raise ValueError("the hosted imaging profile needs a hosted client")
        gemini_id = gemini_connection or os.getenv("ZEOCORE_IMAGING_GEMINI_CONNECTION")
        recraft_id = recraft_connection or os.getenv(
            "ZEOCORE_IMAGING_RECRAFT_CONNECTION"
        )
        return ImagingService(
            gemini=HostedGeminiImages(
                client=hosted_client,
                binding=HostedServiceBinding(connection_id=gemini_id),
            )
            if gemini_id
            else None,
            recraft=HostedRecraftImages(
                client=hosted_client,
                binding=HostedServiceBinding(connection_id=recraft_id),
            )
            if recraft_id
            else None,
        )
    if selected != "local":
        raise ValueError("imaging profile must be 'local' or 'hosted'")
    from .local import LocalGeminiImages, LocalRecraftImages

    return ImagingService(
        gemini=LocalGeminiImages() if os.getenv("GEMINI_API_KEY") else None,
        recraft=LocalRecraftImages() if os.getenv("RECRAFT_API_KEY") else None,
    )


__all__ = ["ImageBackend", "ImagingService", "RecraftBackend", "build_imaging"]

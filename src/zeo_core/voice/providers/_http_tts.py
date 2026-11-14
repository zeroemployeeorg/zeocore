# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/voicerouter/providers/_http_tts.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""Shared base for vendors that synthesize over HTTP rather than a websocket.

Several major vendors do not hold a websocket open: you POST the text and read audio
off the response body. `synthesize()` only has to be an async iterator of audio, so
three adapters would otherwise repeat the same session handling, error mapping and
transcoding.

Subclasses supply three things: where to post, what to post, and what the audio coming
back looks like. The transport is httpx (already a Core dependency), not aiohttp
(deviation D9); a caller may inject its own `httpx.AsyncClient`, which is how the tests
use `httpx.MockTransport`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from zeo_core.voice._log import get_logger
from zeo_core.voice.audio import PcmStreamConverter, to_rasa_audio
from zeo_core.voice.engine import RasaAudioBytes, TTSEngine, TTSError

logger = get_logger(__name__)


def require_api_key(provider: str, api_key: str | None) -> str:
    """The key, or the error that tells the router "not configured here"."""
    if not api_key:
        raise ValueError(f"{provider} is not configured here: no api_key was supplied")
    return api_key


class HttpStreamingTTS(TTSEngine):
    """A TTS engine whose transport is one HTTP request per utterance."""

    #: Sample rate the vendor is asked to produce, before transcoding.
    source_sample_rate: int = 24000
    #: True when the vendor returns a RIFF/WAVE blob rather than raw PCM.
    returns_wav: bool = False

    def __init__(
        self,
        *args: Any,  # noqa: ANN401 - forwarded to the engine base
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        super().__init__(*args, **kwargs)
        self._api_key = require_api_key(self.name(), api_key)
        self._client = client
        self._owns_client = client is None

    @classmethod
    def name(cls) -> str:
        raise NotImplementedError

    def request(self, text: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (url, headers, json_body) for one utterance."""
        raise NotImplementedError

    async def connect(self, config: Any = None) -> None:  # noqa: ANN401
        """Open a pooled client so TCP and TLS are reused across utterances."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
            self._owns_client = True

    async def close_connection(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def synthesize(
        self,
        text: str,
        config: Any = None,  # noqa: ANN401
    ) -> AsyncIterator[RasaAudioBytes]:
        if self._client is None:
            await self.connect()
        client = self._client
        if client is None:
            raise TTSError(f"{self.name()} has no HTTP client")
        url, headers, body = self.request(text)
        try:
            async with client.stream(
                "POST", url, json=body, headers=headers
            ) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode(errors="replace")[:300]
                    raise TTSError(
                        f"{self.name()} returned HTTP {response.status_code}: {detail}"
                    )
                if self.returns_wav:
                    # A RIFF header only makes sense on the whole blob, so this shape
                    # cannot be yielded chunk by chunk.
                    raw = await response.aread()
                    yield to_rasa_audio(
                        raw, self.source_sample_rate, self.audio_format, is_wav=True
                    )
                    return
                # One converter per utterance: it carries the partial frame and the
                # resampler state across chunk boundaries.
                converter = PcmStreamConverter(
                    self.source_sample_rate, self.audio_format
                )
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    converted = converter.feed(chunk)
                    if converted:
                        yield RasaAudioBytes(converted, self.audio_format)
        except httpx.HTTPError as exc:
            raise TTSError(f"{self.name()} request failed: {exc}") from exc

    async def send_text_chunk(self, text: str) -> None:
        raise NotImplementedError(
            f"{self.name()} synthesizes a whole utterance per request; "
            "use synthesize()."
        )

    async def signal_text_done(self) -> None:
        return None

    def engine_bytes_to_rasa_audio_bytes(self, chunk: bytes) -> RasaAudioBytes:
        return to_rasa_audio(chunk, self.source_sample_rate, self.audio_format)

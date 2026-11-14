# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/voicerouter/providers/_local_asr.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""Turning an offline transcriber into a streaming Rasa ASR engine.

Rasa's `ASREngine` is websocket-shaped: open a socket, push audio, read events
as they arrive. Open-source ASR is almost never shaped like that. Whisper and
its descendants are *batch* — you hand them a complete utterance and get one
transcript back. There is no socket and nothing to stream.

This base closes that gap. It buffers incoming audio, decides where the caller
stopped talking, and transcribes that segment off the event loop:

    audio in ──▶ ring buffer ──▶ endpointing ──▶ transcribe(pcm) ──▶ NewTranscript

Endpointing is the interesting part and the reason this is not trivial. A cloud
ASR decides turn boundaries for you; a local model does not, so something has to
say "they have stopped". This uses short-time energy against an adaptive noise
floor, which is small, dependency-free and good enough for the telephony case it
is aimed at. It is deliberately not a neural VAD: that would be another model to
download and another thing to be wrong, and the failure mode here is a slightly
late turn rather than a wrong transcript.

Subclasses implement one method: `transcribe(pcm16, sample_rate) -> str`.
"""

from __future__ import annotations

import asyncio
import audioop
import time
from collections.abc import AsyncIterator
from typing import Any

from zeo_core.voice._log import get_logger
from zeo_core.voice.engine import (
    ASREngine,
    ASREngineConfig,
    ASREvent,
    AudioEncoding,
    NewTranscript,
    RasaAudioBytes,
)

logger = get_logger(__name__)

_SENTINEL = object()


def _pcm16(chunk: RasaAudioBytes) -> bytes:
    """The chunk as 16-bit linear PCM (the framework type has this as a method)."""
    fmt = chunk.format
    if fmt.encoding == AudioEncoding.MULAW:
        return audioop.ulaw2lin(chunk.data, 2)
    if fmt.encoding == AudioEncoding.LINEAR and fmt.bit_depth == 16:
        return chunk.data if len(chunk.data) % 2 == 0 else chunk.data + b"\x00"
    raise ValueError(f"Unsupported audio format: {fmt}")


class LocalASRConfig(ASREngineConfig):
    """Knobs shared by every buffered local engine."""

    #: Model identifier, meaning whatever the subclass says it means.
    #: `model_id`, not `model`: ASREngineConfig keeps a deprecated top-level
    #: `model` and rejects a config that sets both it and `language_map`.
    model_id: str | None = None
    device: str | None = None
    #: Silence, in milliseconds, that ends a turn.
    endpoint_silence_ms: int | None = None
    #: Speech shorter than this is treated as noise and dropped.
    min_speech_ms: int | None = None
    #: Hard cap so one long monologue cannot grow the buffer without limit.
    max_segment_ms: int | None = None
    #: Energy above (noise_floor * this) counts as speech.
    speech_threshold: float | None = None


class LocalBufferedASR(ASREngine[LocalASRConfig]):
    """Buffers audio, endpoints it, and transcribes each segment locally."""

    # Local models need no credentials. That is the point, and it is why the
    # router never skips these for a missing key.
    required_env_vars = ()

    #: Rate the model wants. Audio is resampled to this before transcription.
    target_sample_rate: int = 16000

    @classmethod
    def name(cls) -> str:
        raise NotImplementedError

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._model: Any = None
        self._events: asyncio.Queue = asyncio.Queue()
        self._buffer = bytearray()
        self._noise_floor: float = 0.0
        self._silence_ms = 0.0
        self._speech_ms = 0.0
        self._in_speech = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- subclass hooks -----------------------------------------------------

    def load_model(self) -> Any:
        """Construct the model. Called once, on a worker thread."""
        raise NotImplementedError

    def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        """Transcribe one complete segment. Called on a worker thread."""
        raise NotImplementedError

    # ---- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Load the model up front, off the loop.

        A first-use load would put several seconds of model initialisation
        inside the caller's first sentence, which is exactly where it is least
        affordable.
        """
        self._loop = asyncio.get_running_loop()
        if self._model is None:
            self._model = await asyncio.to_thread(self.load_model)
            logger.info(
                f"{self.name()}.loaded",
                model=self.config.model_id,
                target_sample_rate=self.target_sample_rate,
            )
        self._reset_segment()

    async def close_connection(self) -> None:
        # The model stays resident: reloading per call would cost seconds of
        # deafness. Nothing here holds a socket.
        self._buffer.clear()

    async def open_websocket_connection(self) -> Any:  # pragma: no cover
        raise NotImplementedError(
            f"{self.name()} is a local model, not a websocket service."
        )

    # ---- endpointing --------------------------------------------------------

    def _reset_segment(self) -> None:
        self._buffer.clear()
        self._silence_ms = 0.0
        self._speech_ms = 0.0
        self._in_speech = False

    @staticmethod
    def _rms(pcm: bytes) -> float:
        return audioop.rms(pcm, 2) if pcm else 0.0

    def _is_speech(self, energy: float) -> bool:
        """Adaptive threshold, so a noisy line does not read as endless speech.

        The floor tracks the quietest recent audio rather than being a constant:
        a hardcoded threshold that works in an office fails on a car speakerphone
        and vice versa.
        """
        threshold_multiplier = self.config.speech_threshold or 3.0
        if self._noise_floor == 0.0:
            self._noise_floor = max(energy, 1.0)
        if energy < self._noise_floor:
            # Fall fast towards quiet, so the floor tracks a room going silent.
            self._noise_floor = 0.9 * self._noise_floor + 0.1 * energy
        else:
            # Rise slowly, so speech itself does not drag the floor up with it.
            self._noise_floor = 0.999 * self._noise_floor + 0.001 * energy
        return energy > max(self._noise_floor * threshold_multiplier, 150.0)

    # ---- audio in -----------------------------------------------------------

    def rasa_audio_bytes_to_engine_bytes(self, chunk: RasaAudioBytes) -> bytes:
        return chunk.data

    async def send_audio_chunks(self, chunk: RasaAudioBytes) -> None:
        """Accumulate audio and decide whether the turn has ended."""
        pcm = _pcm16(chunk)
        if self.audio_format.encoding == AudioEncoding.MULAW:
            source_rate = self.audio_format.sample_rate
        else:
            source_rate = self.audio_format.sample_rate
        if source_rate != self.target_sample_rate:
            pcm, _ = audioop.ratecv(
                pcm, 2, 1, source_rate, self.target_sample_rate, None
            )

        duration_ms = (len(pcm) / 2) / self.target_sample_rate * 1000.0
        energy = self._rms(pcm)
        speaking = self._is_speech(energy)

        if speaking:
            if not self._in_speech:
                self._in_speech = True
            self._speech_ms += duration_ms
            self._silence_ms = 0.0
            self._buffer.extend(pcm)
        elif self._in_speech:
            self._silence_ms += duration_ms
            # Keep trailing silence: clipping a word's final consonant is a
            # common and avoidable source of wrong transcripts.
            self._buffer.extend(pcm)

        max_ms = self.config.max_segment_ms or 20000
        end_ms = self.config.endpoint_silence_ms or 700
        segment_ms = (len(self._buffer) / 2) / self.target_sample_rate * 1000.0

        if self._in_speech and (self._silence_ms >= end_ms or segment_ms >= max_ms):
            await self._flush_segment()

    async def _flush_segment(self) -> None:
        min_ms = self.config.min_speech_ms or 250
        if self._speech_ms < min_ms:
            logger.debug(f"{self.name()}.segment_dropped", speech_ms=self._speech_ms)
            self._reset_segment()
            return

        pcm = bytes(self._buffer)
        self._reset_segment()
        started = time.monotonic()
        try:
            text = await asyncio.to_thread(
                self.transcribe, pcm, self.target_sample_rate
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the router
            logger.warning(f"{self.name()}.transcribe_failed", error=str(exc))
            await self._events.put(exc)
            return
        text = (text or "").strip()
        # Info, not debug: the transcription time is the local model's share
        # of the caller's wait, and the case-build harness reads it.
        logger.info(
            f"{self.name()}.transcribed",
            seconds=round((len(pcm) / 2) / self.target_sample_rate, 2),
            took=round(time.monotonic() - started, 3),
            chars=len(text),
        )
        if text:
            await self._events.put(NewTranscript(text))

    async def signal_audio_done(self) -> None:
        """Flush whatever is buffered; the caller has stopped for good."""
        if self._in_speech:
            await self._flush_segment()
        await self._events.put(_SENTINEL)

    async def send_keep_alive(self) -> None:
        return None

    async def stream_asr_events(self) -> AsyncIterator[ASREvent]:
        while True:
            item = await self._events.get()
            if item is _SENTINEL:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    async def set_language(self, rasa_language: str) -> bool:
        self._set_current_language_config(rasa_language)
        return True

    def engine_event_to_asr_event(self, e: Any) -> ASREvent | None:  # pragma: no cover
        return e if isinstance(e, ASREvent) else None

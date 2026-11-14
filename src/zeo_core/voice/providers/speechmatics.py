# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/voicerouter/providers/speechmatics.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""Speechmatics realtime speech-to-text.

    asr:
      name: speechmatics
      language_map: { en: { language: en } }

Only the ASR half is carried (deviation D11); the upstream module also held a TTS class.
The `websockets` package is not a Core dependency: it is imported when the socket is
opened, and a missing one makes the engine "not configured here". The API key is a
constructor argument (deviation D5).
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping
from typing import Any

from zeo_core.voice._log import get_logger
from zeo_core.voice.engine import (
    ASREngine,
    ASREngineConfig,
    ASREvent,
    ASRLanguageMapEntry,
    AudioEncoding,
    AudioFormat,
    NewTranscript,
    RasaAudioBytes,
    UserIsSpeaking,
)
from zeo_core.voice.providers._http_tts import require_api_key

logger = get_logger(__name__)

DEFAULT_ASR_ENDPOINT = "wss://eu.rt.speechmatics.com/v2"

_ENCODING_MAP = {
    AudioEncoding.LINEAR: "pcm_s16le",
    AudioEncoding.MULAW: "mulaw",
}


class SpeechmaticsASRConfig(ASREngineConfig):
    endpoint: str | None = None
    operating_point: str | None = None
    max_delay: float | None = None
    enable_partials: bool | None = None
    #: Seconds of silence (0 to 2) after which Speechmatics sends `EndOfUtterance`.
    #: When set, final segments are held and joined into one transcript per
    #: utterance. Unset keeps the original behaviour: every final segment is its own
    #: transcript, which a voice channel treats as a whole user turn.
    end_of_utterance_silence_trigger: float | None = None
    #: Speechmatics custom dictionary, sent as `additional_vocab`: a list of
    #: `{"content": "Brightmart", "sounds_like": ["bright mart"]}` entries (or plain
    #: strings) for names the base model does not know.
    additional_vocab: list[Any] | None = None


class SpeechmaticsASR(ASREngine[SpeechmaticsASRConfig]):
    """Realtime transcription over one websocket.

    Two protocol details that cost real time to discover:

    * Configuration is **not** in the query string. The socket opens unconfigured and
      the first message must be `StartRecognition`; sending audio first gets a
      connection that transcribes nothing, silently.
    * `EndOfStream` must carry `last_seq_no`, the count of audio messages sent, so the
      count is tracked here rather than inferred.

    Speechmatics finalizes speech in segments, often a word or two at a time, and a
    voice channel treats every `NewTranscript` as a complete user turn. Set
    `end_of_utterance_silence_trigger` to have Speechmatics mark the end of each
    utterance; the segments are then buffered and sent as one transcript.
    """

    def __init__(
        self,
        rasa_language: str,
        format: AudioFormat,
        config: SpeechmaticsASRConfig | None = None,
        additional_languages: list[str] | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        super().__init__(rasa_language, format, config, additional_languages)
        self._api_key = require_api_key(self.name(), api_key)
        self._seq_no = 0
        self._segments: list[str] = []

    @classmethod
    def name(cls) -> str:
        return "speechmatics"

    @property
    def _utterance_mode(self) -> bool:
        trigger = self.config.end_of_utterance_silence_trigger
        return trigger is not None and trigger > 0

    def _start_recognition_message(self) -> dict[str, Any]:
        encoding = _ENCODING_MAP.get(self.audio_format.encoding)
        if encoding is None:
            raise ValueError(
                f"Speechmatics cannot accept audio encoding "
                f"{self.audio_format.encoding!r}."
            )
        # `current_language_config` is the resolved state, not the map entry written in
        # the config: the engine-side code is `engine_language_key`.
        language = (
            self.current_language_config.engine_language_key
            or self.current_language_config.rasa_language_key
            or "en"
        )
        transcription_config: dict[str, Any] = {
            "language": language,
            "enable_partials": bool(self.config.enable_partials),
            "max_delay": float(self.config.max_delay or 1.0),
            "operating_point": self.config.operating_point,
        }
        if self.config.additional_vocab:
            transcription_config["additional_vocab"] = [
                {"content": entry} if isinstance(entry, str) else dict(entry)
                for entry in self.config.additional_vocab
            ]
        if self._utterance_mode:
            transcription_config["conversation_config"] = {
                "end_of_utterance_silence_trigger": float(
                    self.config.end_of_utterance_silence_trigger or 0
                ),
            }
        return {
            "message": "StartRecognition",
            "audio_format": {
                "type": "raw",
                "encoding": encoding,
                "sample_rate": self.audio_format.sample_rate,
            },
            "transcription_config": transcription_config,
        }

    async def open_websocket_connection(self) -> Any:  # noqa: ANN401
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus

        endpoint = self.config.endpoint or DEFAULT_ASR_ENDPOINT
        try:
            socket = await connect(
                endpoint,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except InvalidStatus as exc:
            status_code = exc.response.status_code
            logger.error(
                "speechmatics.connection.failed",
                status_code=status_code,
                error=(
                    "check your Speechmatics API key"
                    if status_code == 401
                    else "connection to Speechmatics failed"
                ),
                endpoint=endpoint,
            )
            raise
        self._seq_no = 0
        self._segments = []
        await socket.send(json.dumps(self._start_recognition_message()))
        return socket

    async def signal_audio_done(self) -> None:
        if self.asr_socket is None:
            raise AttributeError("Websocket not connected.")
        await self.asr_socket.send(
            json.dumps({"message": "EndOfStream", "last_seq_no": self._seq_no})
        )

    def rasa_audio_bytes_to_engine_bytes(self, chunk: RasaAudioBytes) -> bytes:
        # The one hook called exactly once per chunk actually sent, which makes it the
        # only correct place to count for last_seq_no.
        self._seq_no += 1
        return chunk.data

    def engine_event_to_asr_event(self, e: str | bytes) -> ASREvent | None:
        try:
            message = json.loads(e)
        except (TypeError, ValueError):
            return None
        kind = message.get("message")
        if kind == "AddPartialTranscript":
            text = message.get("metadata", {}).get("transcript", "").strip()
            # Empty partials arrive during silence; forwarding them reads as the user
            # speaking.
            return UserIsSpeaking(text) if text else None
        if kind == "AddTranscript":
            text = message.get("metadata", {}).get("transcript", "").strip()
            if not text:
                return None
            if not self._utterance_mode:
                return NewTranscript(text)
            # Hold the segment until Speechmatics says the utterance ended.
            self._segments.append(text)
            return UserIsSpeaking(" ".join(self._segments))
        if kind == "EndOfUtterance":
            if not self._segments:
                return None
            text, self._segments = " ".join(self._segments), []
            return NewTranscript(text)
        if kind == "Error":
            logger.error(
                "speechmatics.error",
                type=message.get("type"),
                reason=message.get("reason"),
            )
        return None

    @classmethod
    def from_config_dict(
        cls,
        config: Mapping[str, Any] | None,
        format: AudioFormat,
        rasa_language: str,
        additional_languages: list[str] | None = None,
        *,
        api_key: str | None = None,
    ) -> SpeechmaticsASR:
        if importlib.util.find_spec("websockets") is None:
            raise ModuleNotFoundError(
                "websockets is not installed, so speechmatics is not configured "
                "here. Install with: uv pip install websockets"
            )
        return cls(
            rasa_language=rasa_language,
            format=format,
            config=SpeechmaticsASRConfig.model_validate(config or {}),
            additional_languages=additional_languages,
            api_key=api_key,
        )

    @staticmethod
    def get_default_config(rasa_language: str) -> SpeechmaticsASRConfig:
        return SpeechmaticsASRConfig(
            endpoint=DEFAULT_ASR_ENDPOINT,
            operating_point="enhanced",
            max_delay=1.0,
            enable_partials=True,
            language_map={rasa_language: ASRLanguageMapEntry(language=rasa_language)},
        )

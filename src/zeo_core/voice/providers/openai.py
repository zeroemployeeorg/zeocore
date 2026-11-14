# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/voicerouter/providers/openai.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""OpenAI text-to-speech over one HTTP request per utterance.

    tts:
      name: openai
      model_id: gpt-4o-mini-tts
      language_map: { en: { voice: alloy } }

The API key is a constructor argument (deviation D5): Core reads no environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zeo_core.voice.engine import AudioFormat, TTSEngineConfig, TTSLanguageMapEntry
from zeo_core.voice.providers._http_tts import HttpStreamingTTS

DEFAULT_ENDPOINT = "https://api.openai.com/v1/audio/speech"


class OpenAITTSConfig(TTSEngineConfig):
    endpoint: str | None = None
    # `model_id`, not `model`: the engine config keeps a top-level `model` field in the
    # framework this mirrors, and the same name is kept so a config moves unchanged.
    model_id: str | None = None
    #: Free-text style prompt; the gpt-4o-mini-tts family accepts one.
    instructions: str | None = None
    speed: float | None = None


class OpenAITTS(HttpStreamingTTS):
    source_sample_rate = 24000
    returns_wav = False

    @classmethod
    def name(cls) -> str:
        return "openai"

    def request(self, text: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self.config.model_id or "gpt-4o-mini-tts",
            "voice": self.current_language_config.voice or "alloy",
            "input": text,
            "response_format": "pcm",
        }
        if self.config.instructions:
            body["instructions"] = self.config.instructions
        if self.config.speed is not None:
            body["speed"] = self.config.speed
        return (
            self.config.endpoint or DEFAULT_ENDPOINT,
            {"Authorization": f"Bearer {self._api_key}"},
            body,
        )

    @staticmethod
    def get_default_config(rasa_language: str) -> OpenAITTSConfig:
        return OpenAITTSConfig(
            endpoint=DEFAULT_ENDPOINT,
            model_id="gpt-4o-mini-tts",
            timeout=30,
            language_map={rasa_language: TTSLanguageMapEntry(voice="alloy")},
        )

    @classmethod
    def from_config_dict(
        cls,
        config: Mapping[str, Any] | None,
        format: AudioFormat,
        rasa_language: str,
        additional_languages: list[str] | None = None,
        *,
        api_key: str | None = None,
    ) -> OpenAITTS:
        return cls(
            rasa_language=rasa_language,
            format=format,
            config=OpenAITTSConfig.model_validate(config or {}),
            additional_languages=additional_languages,
            api_key=api_key,
        )

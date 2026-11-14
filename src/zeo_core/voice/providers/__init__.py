# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/voicerouter/providers/__init__.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""Vendor engines for the voice router, on Core's standalone engine types.

    TTS   zeo_core.voice.providers.openai.OpenAITTS
    ASR   zeo_core.voice.providers.speechmatics.SpeechmaticsASR
          zeo_core.voice.providers.whisper.FasterWhisperASR   (local, open source)

Each is a standalone engine (deviation D1): it imports no framework. Core reads no
credential (D5): the API key is a constructor argument, supplied by whoever builds the
engine. A vendor SDK or socket library is imported inside the engine, never at module
import, and is not a dependency of Core; a missing one makes the engine "not
configured here", which the router treats like a missing key.
"""

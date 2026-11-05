# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/voicerouter/__init__.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""Vendor-agnostic ASR/TTS routing: standalone engine types and the router.

Engines are built only through a caller-supplied `EngineFactory` (required, with no
name or dotted-path loading and no environment fallback).
"""

from zeo_core.voice.health import HealthRegistry, ProviderHealth
from zeo_core.voice.routed_asr import RoutedASR
from zeo_core.voice.routed_tts import RoutedTTS

__all__ = ["RoutedASR", "RoutedTTS", "HealthRegistry", "ProviderHealth"]

# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/tests/test_shared_profile.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""Speech profile identity, credential refusal and truncated-TTS handling."""

from __future__ import annotations

import asyncio
import unittest

from zeo_core.voice import RoutedTTS
from zeo_core.voice.base import BuiltProvider, ProviderSpec, RouterPolicy
from zeo_core.voice.engine import RasaAudioBytes, TTSError
from zeo_core.voice.profile import VoiceProfile

# T3: a neutral, credential-free profile; the upstream test read a tutorial JSON file that is
# outside the inventory. Provider names are keys for an injected factory, never import paths.
PROFILE = {
    "schema_version": 1,
    "id": "offline-test",
    "revision": "r1",
    "language": "en",
    "sample_rate": 24000,
    "asr": {
        "providers": [
            {
                "name": "fake-asr",
                "label": "fake-asr",
                "language_map": {"en": {"language": "en-US"}},
            }
        ]
    },
    "tts": {
        "providers": [
            {
                "name": "fake-tts",
                "label": "fake-tts",
                "language_map": {"en": {"language": "en-US", "voice": "alex"}},
            }
        ]
    },
}


class ProfileTests(unittest.TestCase):
    def test_truncated_tts_is_not_replayed_or_reported_as_success(self):
        async def check():
            class Broken:
                closed = False

                async def connect(self):
                    pass

                async def close_connection(self):
                    self.closed = True

                async def synthesize(self, text):
                    yield RasaAudioBytes(
                        b"\0\0",
                        format=VoiceProfile.model_validate(PROFILE).audio_format,
                    )
                    raise RuntimeError("offline-failure")

            provider = Broken()
            router = RoutedTTS(
                [BuiltProvider(ProviderSpec("broken", "broken", {}), provider)],
                RouterPolicy(
                    health_scope="call", same_provider_retries=0, raise_on_failure=True
                ),
            )
            emitted = []
            with self.assertRaises(TTSError):
                async for chunk in router.synthesize("hello"):
                    emitted.append(chunk.data)
            self.assertEqual(emitted, [b"\0\0"])
            self.assertTrue(provider.closed)

        asyncio.run(check())

    def test_identity_changes_with_vendor_settings_not_json_order(self):
        p = VoiceProfile.model_validate(PROFILE)
        d = p.model_dump()
        self.assertEqual(
            p.fingerprint,
            VoiceProfile.model_validate(dict(reversed(list(d.items())))).fingerprint,
        )
        d["tts"]["providers"][0]["language_map"]["en"]["voice"] = "theo"
        self.assertNotEqual(p.fingerprint, VoiceProfile.model_validate(d).fingerprint)

    def test_credentials_and_schema_rejected_before_requests(self):
        d = VoiceProfile.model_validate(PROFILE).model_dump()
        d["tts"]["providers"][0]["api_key"] = "must-not-be-saved"
        with self.assertRaises(ValueError):
            VoiceProfile.model_validate(d)
        d.pop("tts")
        with self.assertRaises(ValueError):
            VoiceProfile.model_validate(d)

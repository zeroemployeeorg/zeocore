"""One control per deviation D1 to D7 from the upstream voice router.

Each class names the deviation it pins. These are Core-written tests, held to Core's
own lint and type rules.
"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from unittest import mock

from zeo_core.voice import _provider_registry, base, contract, engine
from zeo_core.voice._log import get_logger
from zeo_core.voice._provider_registry import factory_from_map, require_factory
from zeo_core.voice.engine import (
    L16_24KHZ,
    AudioBytes,
    AudioFormat,
    RasaAudioBytes,
    TTSEngine,
    TTSEngineConfig,
    TTSLanguageMapEntry,
)
from zeo_core.voice.profile import VoiceProfile
from zeo_core.voice.routed_asr import RoutedASR


class _Tripwire(dict[str, str]):
    """An environment that fails the test on any read."""

    def _fail(self) -> None:
        raise AssertionError("Core read the environment")

    def get(self, *args: object, **kwargs: object) -> Any:  # noqa: ANN401
        self._fail()

    def __getitem__(self, key: str) -> str:
        self._fail()
        return ""

    def __contains__(self, key: object) -> bool:
        self._fail()
        return False

    def __iter__(self) -> Iterator[str]:
        self._fail()
        return iter(())


class _FakeEngine:
    def __init__(self, name: str) -> None:
        self.name = name


def _builders() -> dict[str, dict[str, Any]]:
    def build(
        config: Mapping[str, Any],
        audio_format: AudioFormat,
        language: str,
        additional: list[str] | None,
    ) -> _FakeEngine:
        return _FakeEngine(str(config.get("label", "fake")))

    return {"asr": {"fake-asr": build}, "tts": {"fake-tts": build}}


PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "id": "deviation-test",
    "revision": "r1",
    "language": "en",
    "sample_rate": 24000,
    "asr": {"providers": [{"name": "fake-asr", "label": "a"}]},
    "tts": {"providers": [{"name": "fake-tts", "label": "t"}]},
}


class TestD1StandaloneTypes(unittest.TestCase):
    def test_the_types_are_defined_here_and_no_switch_remains(self) -> None:
        self.assertEqual(AudioFormat.__module__, "zeo_core.voice.engine")
        self.assertEqual(RasaAudioBytes.__module__, "zeo_core.voice.engine")
        self.assertFalse(hasattr(engine, "RASA_AVAILABLE"))
        self.assertFalse(hasattr(engine, "find_spec"))


class TestD2NeutralAlias(unittest.TestCase):
    def test_audio_bytes_is_the_same_class_as_rasa_audio_bytes(self) -> None:
        self.assertIs(AudioBytes, RasaAudioBytes)
        chunk = AudioBytes(b"\x00\x00", L16_24KHZ)
        self.assertIsInstance(chunk, RasaAudioBytes)


class TestD3LoggingShim(unittest.TestCase):
    def test_output_form_is_event_then_key_value_pairs(self) -> None:
        logger = get_logger("zeo_core.voice.test")
        with self.assertLogs("zeo_core.voice.test", level="DEBUG") as captured:
            logger.info("voicerouter.asr.ready", providers=["a"], skipped=[])
            logger.warning("voicerouter.plain")
            logger.error("voicerouter.err", error="boom")
            logger.debug("voicerouter.dbg", n=1)
        self.assertEqual(
            captured.output,
            [
                "INFO:zeo_core.voice.test:"
                "voicerouter.asr.ready providers=['a'] skipped=[]",
                "WARNING:zeo_core.voice.test:voicerouter.plain",
                "ERROR:zeo_core.voice.test:voicerouter.err error='boom'",
                "DEBUG:zeo_core.voice.test:voicerouter.dbg n=1",
            ],
        )

    def test_a_level_below_the_logger_is_not_rendered(self) -> None:
        logger = get_logger("zeo_core.voice.quiet")
        with self.assertLogs("zeo_core.voice.quiet", level="WARNING") as captured:
            logger.info("dropped", x=1)
            logger.warning("kept")
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelno, logging.WARNING)


class TestD4RequiredFactory(unittest.TestCase):
    def test_no_factory_refuses(self) -> None:
        with self.assertRaises(TypeError):
            require_factory(None)
        profile = VoiceProfile.model_validate(PROFILE)
        with self.assertRaises(TypeError):
            profile.build_asr(None)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            RoutedASR.from_config_dict(
                PROFILE["asr"],
                L16_24KHZ,
                "en",
                factory=None,  # type: ignore[arg-type]
            )

    def test_a_name_is_a_key_never_an_import_path(self) -> None:
        factory = factory_from_map(_builders())
        with mock.patch("importlib.import_module") as imported:
            with self.assertRaises(ValueError):
                factory("asr", {"name": "os.path.join"}, L16_24KHZ, "en", None)
            with self.assertRaises(ValueError):
                factory("stt", {"name": "fake-asr"}, L16_24KHZ, "en", None)
        imported.assert_not_called()

    def test_a_supplied_factory_builds_the_routers(self) -> None:
        profile = VoiceProfile.model_validate(PROFILE)
        asr, tts = profile.engines(factory_from_map(_builders()))
        self.assertEqual(type(asr).__name__, "RoutedASR")
        self.assertEqual(type(tts).__name__, "RoutedTTS")
        self.assertFalse(hasattr(_provider_registry, "_PROVIDERS"))
        self.assertFalse(hasattr(engine, "asr_engine_from_config"))
        self.assertFalse(hasattr(engine, "tts_engine_from_config"))


class TestD5NoEnvironmentRead(unittest.TestCase):
    def test_core_reads_no_environment_variable_and_checks_no_key(self) -> None:
        class Needy(TTSEngine[TTSEngineConfig]):
            required_env_vars = ("SOME_VENDOR_KEY",)

            @classmethod
            def get_default_config(cls, language: str) -> TTSEngineConfig:
                return TTSEngineConfig(
                    language_map={"en": TTSLanguageMapEntry(language="en")}
                )

        with mock.patch.object(os, "environ", _Tripwire()):
            instance = Needy("en", L16_24KHZ)  # the old base raised without the key
            profile = VoiceProfile.model_validate(PROFILE)
            profile.engines(factory_from_map(_builders()))
        self.assertEqual(instance.required_env_vars, ("SOME_VENDOR_KEY",))
        self.assertFalse(hasattr(base, "env_present"))


class TestD6Audioop(unittest.TestCase):
    def test_audioop_resolves_under_its_stdlib_name(self) -> None:
        from zeo_core.voice import audio

        self.assertTrue(callable(audio.audioop.ratecv))


class TestD7ExplicitScannerRoot(unittest.TestCase):
    def test_the_root_is_required_and_nothing_is_imported_to_find_one(self) -> None:
        self.assertFalse(hasattr(contract, "_rasa_dir"))
        with self.assertRaises(TypeError):
            contract.scan("tts")  # type: ignore[call-arg]
        with tempfile.TemporaryDirectory() as empty:
            surface = contract.scan("tts", Path(empty))
        self.assertEqual(surface.size, 0)


if __name__ == "__main__":
    unittest.main()

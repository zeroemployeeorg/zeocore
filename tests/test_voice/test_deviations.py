"""One control per deviation D1 to D8 from the upstream voice router.

Each class names the deviation it pins. These are Core-written tests, held to Core's
own lint and type rules.
"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from collections.abc import AsyncIterator, Iterator, Mapping
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
from zeo_core.voice.routed_tts import RoutedTTS


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


class _FakeResponse:
    """A streamed response that fails where it is told to."""

    response_id = "fake-response"

    def __init__(self, fail_on: str | None) -> None:
        self._fail_on = fail_on

    async def write(self, text: str) -> None:
        if self._fail_on == "write":
            raise RuntimeError("write failed")

    async def finish(self) -> None:
        if self._fail_on == "finish":
            raise RuntimeError("finish failed")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._fail_on == "read":
            raise RuntimeError("read failed")
        yield b"audio"


class _CountingEngine:
    """A fake TTS engine that counts connects and closes."""

    streaming_input = True

    def __init__(
        self,
        *,
        fail_start: bool = False,
        fail_on: str | None = None,
        fail_language: bool = False,
        close_error: bool = False,
    ) -> None:
        self.fail_start = fail_start
        self.fail_on = fail_on
        self.fail_language = fail_language
        self.close_error = close_error
        self.connects = 0
        self.closes = 0

    async def connect(self, config: object = None) -> None:
        self.connects += 1

    async def close_connection(self) -> None:
        self.closes += 1
        if self.close_error:
            raise RuntimeError("close failed")

    async def start_response(self, streaming_config: object = None) -> _FakeResponse:
        if self.fail_start:
            raise RuntimeError("start failed")
        return _FakeResponse(self.fail_on)

    async def set_language(self, language: str) -> bool:
        if self.fail_language:
            raise RuntimeError("language failed")
        return True


def _two_provider_router(first: _CountingEngine, second: _CountingEngine) -> RoutedTTS:
    pending = [first, second]

    def build(
        config: Mapping[str, Any],
        audio_format: AudioFormat,
        language: str,
        additional: list[str] | None,
    ) -> _CountingEngine:
        return pending.pop(0)  # the providers are built in their configured order

    return RoutedTTS.from_config_dict(
        {
            "providers": [
                {"name": "fake-tts", "label": "a"},
                {"name": "fake-tts", "label": "b"},
            ],
            "policy": {"health_scope": "call"},
        },
        L16_24KHZ,
        "en",
        factory=factory_from_map({"tts": {"fake-tts": build}}),
    )


class TestD8FailedConnectionsAreClosed(unittest.IsolatedAsyncioTestCase):
    """D8 routed_tts.py, routed_asr.py: a provider that fails after it connected is
    closed, bounded, before the router stops tracking it, so leaving the router's
    context cannot leak its socket or reader task."""

    async def test_a_start_response_failure_closes_that_provider(self) -> None:
        first, second = _CountingEngine(fail_start=True), _CountingEngine()
        router = _two_provider_router(first, second)
        async with router:
            response = await router.start_response()
            self.assertEqual(response.response_id, "fake-response")
            self.assertEqual((first.closes, second.closes), (1, 0))
        self.assertEqual((first.closes, second.closes), (1, 1))
        self.assertEqual((first.connects, second.connects), (1, 1))

    async def test_a_close_error_does_not_mask_the_failover(self) -> None:
        first = _CountingEngine(fail_start=True, close_error=True)
        second = _CountingEngine()
        router = _two_provider_router(first, second)
        async with router:
            await router.start_response()
        self.assertEqual((first.closes, second.closes), (1, 1))

    async def test_a_streamed_failure_closes_the_provider(self) -> None:
        for where in ("write", "finish", "read"):
            with self.subTest(where=where):
                first, second = _CountingEngine(fail_on=where), _CountingEngine()
                router = _two_provider_router(first, second)
                async with router:
                    response = await router.start_response()
                    with self.assertRaises(RuntimeError):
                        if where == "write":
                            await response.write("text")
                        elif where == "finish":
                            await response.finish()
                        else:
                            async for _ in response:
                                pass
                    self.assertEqual(first.closes, 1)
                self.assertEqual(first.closes, 1)

    async def test_a_set_language_failure_closes_the_provider(self) -> None:
        first, second = _CountingEngine(fail_language=True), _CountingEngine()
        router = _two_provider_router(first, second)
        async with router:
            self.assertTrue(await router.set_language("en"))
            self.assertEqual(first.closes, 1)
        self.assertEqual(first.closes, 1)


class _CountingASR:
    """A fake ASR engine that counts connects and closes and refuses one language."""

    def __init__(self, *, refuse: type[Exception] | None = None) -> None:
        self.refuse = refuse
        self.connects = 0
        self.closes = 0

    async def connect(self, config: object = None) -> None:
        self.connects += 1

    async def close_connection(self) -> None:
        self.closes += 1

    async def set_language(self, language: str) -> bool:
        if self.refuse is not None:
            raise self.refuse("no provider language mapping")
        return True


class TestD8AsrSetLanguage(unittest.IsolatedAsyncioTestCase):
    """D8, ASR half: a language the engine refuses before touching its socket leaves
    the healthy connection open; a failure after the socket was touched closes it."""

    async def _router(self, engine_: _CountingASR) -> RoutedASR:
        def build(
            config: Mapping[str, Any],
            audio_format: AudioFormat,
            language: str,
            additional: list[str] | None,
        ) -> _CountingASR:
            return engine_

        return RoutedASR.from_config_dict(
            {"providers": [{"name": "fake-asr", "label": "a"}]},
            L16_24KHZ,
            "en",
            factory=factory_from_map({"asr": {"fake-asr": build}}),
        )

    async def test_an_unmapped_language_does_not_close_the_connection(self) -> None:
        engine_ = _CountingASR(refuse=ValueError)
        router = await self._router(engine_)
        async with router:
            self.assertFalse(await router.set_language("xx"))
            self.assertEqual(engine_.closes, 0)
        self.assertEqual(engine_.closes, 1)  # only on leaving the router

    async def test_a_failure_after_the_socket_closes_the_provider(self) -> None:
        engine_ = _CountingASR(refuse=RuntimeError)
        router = await self._router(engine_)
        async with router:
            self.assertFalse(await router.set_language("en"))
            self.assertEqual(engine_.closes, 1)

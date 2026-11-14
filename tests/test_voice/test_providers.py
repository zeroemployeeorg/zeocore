"""The vendor engines in zeo_core.voice.providers, on fakes (no network, key or SDK)."""

from __future__ import annotations

import asyncio
import importlib
import json
import struct
import subprocess  # noqa: S404 - runs this interpreter on a fixed snippet
import sys
import textwrap
import unittest
from collections.abc import Callable
from typing import Any

import httpx

from zeo_core.voice import RoutedTTS
from zeo_core.voice._provider_registry import factory_from_map
from zeo_core.voice.engine import (
    L16_16KHZ,
    L16_24KHZ,
    MULAW_8KHZ,
    ASRLanguageMapEntry,
    AudioEncoding,
    AudioFormat,
    NewTranscript,
    RasaAudioBytes,
    TTSError,
    UserIsSpeaking,
)
from zeo_core.voice.providers._local_asr import LocalASRConfig, LocalBufferedASR
from zeo_core.voice.providers.openai import OpenAITTS
from zeo_core.voice.providers.speechmatics import SpeechmaticsASR
from zeo_core.voice.providers.whisper import FasterWhisperASR

KEY = "test-key-not-a-secret"


def _tts(
    handler: Callable[[httpx.Request], httpx.Response],
    fmt: AudioFormat = L16_24KHZ,
) -> OpenAITTS:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAITTS(
        rasa_language="en",
        format=fmt,
        config=OpenAITTS.get_default_config("en"),
        api_key=KEY,
        client=client,
    )


async def _collect(engine: OpenAITTS, text: str) -> list[RasaAudioBytes]:
    return [chunk async for chunk in engine.synthesize(text)]


class TestOpenAITTS(unittest.TestCase):
    def test_a_request_carries_the_key_the_caller_supplied_and_the_voice(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers["authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=b"\x01\x00" * 480)

        chunks = asyncio.run(_collect(_tts(handler), "hello"))
        self.assertEqual(seen["auth"], f"Bearer {KEY}")
        self.assertEqual(seen["body"]["input"], "hello")
        self.assertEqual(seen["body"]["voice"], "alloy")
        self.assertEqual(seen["body"]["response_format"], "pcm")
        self.assertEqual(b"".join(c.data for c in chunks), b"\x01\x00" * 480)

    def test_audio_is_converted_to_the_requested_format(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"\x10\x00" * 2400)  # 100 ms at 24 kHz

        chunks = asyncio.run(_collect(_tts(handler, MULAW_8KHZ), "hello"))
        self.assertTrue(chunks)
        self.assertTrue(all(c.format.encoding == AudioEncoding.MULAW for c in chunks))
        # 100 ms at 8 kHz mu-law is about 800 bytes
        self.assertAlmostEqual(sum(len(c.data) for c in chunks), 800, delta=80)

    def test_an_http_error_is_a_tts_error_with_the_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="upstream broke")

        with self.assertRaises(TTSError) as caught:
            asyncio.run(_collect(_tts(handler), "hello"))
        self.assertIn("HTTP 500", str(caught.exception))

    def test_a_transport_failure_is_a_tts_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        with self.assertRaises(TTSError):
            asyncio.run(_collect(_tts(handler), "hello"))

    def test_no_key_means_not_configured_here(self) -> None:
        with self.assertRaises(ValueError) as caught:
            OpenAITTS.from_config_dict({}, L16_24KHZ, "en", None)
        self.assertIn("not configured here", str(caught.exception))

    def test_the_router_skips_a_provider_with_no_key(self) -> None:
        factory = factory_from_map(
            {
                "tts": {
                    "openai": lambda settings, fmt, lang, more: (
                        OpenAITTS.from_config_dict(settings, fmt, lang, more)
                    )
                }
            }
        )
        with self.assertRaises(ValueError) as caught:
            RoutedTTS.from_config_dict(
                {"providers": [{"name": "openai"}]},
                L16_24KHZ,
                "en",
                None,
                factory=factory,
            )
        self.assertIn("credentials not configured", str(caught.exception))


def _speechmatics(**config: object) -> SpeechmaticsASR:
    return SpeechmaticsASR(
        "en",
        L16_16KHZ,
        SpeechmaticsASR.get_default_config("en").model_copy(update=config),
        api_key=KEY,
    )


class TestSpeechmaticsASR(unittest.TestCase):
    def test_the_first_message_configures_the_recognition(self) -> None:
        message = _speechmatics()._start_recognition_message()
        self.assertEqual(message["message"], "StartRecognition")
        self.assertEqual(message["audio_format"]["encoding"], "pcm_s16le")
        self.assertEqual(message["audio_format"]["sample_rate"], 16000)
        self.assertEqual(message["transcription_config"]["language"], "en")

    def test_a_mulaw_format_is_sent_as_mulaw(self) -> None:
        engine = SpeechmaticsASR(
            "en", MULAW_8KHZ, SpeechmaticsASR.get_default_config("en"), api_key=KEY
        )
        self.assertEqual(
            engine._start_recognition_message()["audio_format"]["encoding"], "mulaw"
        )

    def test_events_are_read_from_vendor_messages(self) -> None:
        engine = _speechmatics()
        partial = engine.engine_event_to_asr_event(
            json.dumps(
                {"message": "AddPartialTranscript", "metadata": {"transcript": " hi "}}
            )
        )
        final = engine.engine_event_to_asr_event(
            json.dumps({"message": "AddTranscript", "metadata": {"transcript": "hi"}})
        )
        self.assertEqual(partial, UserIsSpeaking("hi"))
        self.assertEqual(final, NewTranscript("hi"))
        self.assertIsNone(engine.engine_event_to_asr_event("not json"))
        empty = json.dumps({"message": "AddPartialTranscript", "metadata": {}})
        self.assertIsNone(engine.engine_event_to_asr_event(empty))

    def test_utterance_mode_joins_segments_until_the_end_of_utterance(self) -> None:
        engine = _speechmatics(end_of_utterance_silence_trigger=0.5)
        for word in ("one", "two"):
            held = engine.engine_event_to_asr_event(
                json.dumps(
                    {"message": "AddTranscript", "metadata": {"transcript": word}}
                )
            )
            self.assertIsInstance(held, UserIsSpeaking)
        done = engine.engine_event_to_asr_event(
            json.dumps({"message": "EndOfUtterance"})
        )
        self.assertEqual(done, NewTranscript("one two"))
        self.assertIsNone(
            engine.engine_event_to_asr_event(json.dumps({"message": "EndOfUtterance"}))
        )

    def test_the_audio_message_count_is_what_end_of_stream_reports(self) -> None:
        engine = _speechmatics()
        chunk = RasaAudioBytes(b"\x00\x00" * 160, L16_16KHZ)
        for _ in range(3):
            engine.rasa_audio_bytes_to_engine_bytes(chunk)
        sent: list[str] = []

        class Socket:
            async def send(self, data: str) -> None:
                sent.append(data)

        engine.asr_socket = Socket()
        asyncio.run(engine.signal_audio_done())
        self.assertEqual(
            json.loads(sent[0]), {"message": "EndOfStream", "last_seq_no": 3}
        )

    def test_no_key_means_not_configured_here(self) -> None:
        with self.assertRaises(ValueError) as caught:
            SpeechmaticsASR("en", L16_16KHZ, api_key=None)
        self.assertIn("not configured here", str(caught.exception))

    def test_a_missing_websockets_package_means_not_configured_here(self) -> None:
        import importlib.util

        original = importlib.util.find_spec
        importlib.util.find_spec = lambda name, *a, **k: (
            None if name == "websockets" else original(name, *a, **k)
        )
        try:
            with self.assertRaises(ModuleNotFoundError) as caught:
                SpeechmaticsASR.from_config_dict({}, L16_16KHZ, "en", None, api_key=KEY)
        finally:
            importlib.util.find_spec = original
        self.assertIn("not configured here", str(caught.exception))


def _frame(amplitude: int, milliseconds: int = 50, rate: int = 16000) -> RasaAudioBytes:
    samples = rate * milliseconds // 1000
    pcm = struct.pack("<h", amplitude) * samples
    return RasaAudioBytes(pcm, AudioFormat(AudioEncoding.LINEAR, rate))


class _Local(LocalBufferedASR):
    transcript = "hello there"
    loaded = 0

    @classmethod
    def name(cls) -> str:
        return "fake-local"

    @staticmethod
    def get_default_config(rasa_language: str) -> LocalASRConfig:
        return LocalASRConfig(
            endpoint_silence_ms=700,
            min_speech_ms=250,
            max_segment_ms=20000,
            language_map={rasa_language: ASRLanguageMapEntry(language=rasa_language)},
        )

    def load_model(self) -> Any:  # noqa: ANN401
        type(self).loaded += 1
        return object()

    def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        if self.transcript == "boom":
            raise RuntimeError("model failed")
        return self.transcript


async def _speak(engine: _Local, frames: list[RasaAudioBytes]) -> list[Any]:
    async with engine:
        for frame in frames:
            await engine.send_audio_chunks(frame)
        await engine.signal_audio_done()
        return [event async for event in engine.stream_asr_events()]


class TestLocalBufferedASR(unittest.TestCase):
    def setUp(self) -> None:
        _Local.transcript = "hello there"
        _Local.loaded = 0

    def test_a_spoken_turn_followed_by_silence_becomes_one_transcript(self) -> None:
        frames = [_frame(10)] * 2 + [_frame(8000)] * 8 + [_frame(10)] * 16
        events = asyncio.run(_speak(_Local("en", L16_16KHZ), frames))
        self.assertEqual(events, [NewTranscript("hello there")])

    def test_speech_shorter_than_the_minimum_is_dropped_as_noise(self) -> None:
        frames = [_frame(10)] * 2 + [_frame(8000)] * 2 + [_frame(10)] * 16
        self.assertEqual(asyncio.run(_speak(_Local("en", L16_16KHZ), frames)), [])

    def test_trailing_speech_is_flushed_when_the_caller_stops(self) -> None:
        frames = [_frame(10)] * 2 + [_frame(8000)] * 8
        events = asyncio.run(_speak(_Local("en", L16_16KHZ), frames))
        self.assertEqual(events, [NewTranscript("hello there")])

    def test_a_failing_model_raises_to_the_router(self) -> None:
        _Local.transcript = "boom"
        frames = [_frame(10)] * 2 + [_frame(8000)] * 8 + [_frame(10)] * 16
        with self.assertRaises(RuntimeError):
            asyncio.run(_speak(_Local("en", L16_16KHZ), frames))

    def test_the_model_loads_once_on_connect(self) -> None:
        engine = _Local("en", L16_16KHZ)
        asyncio.run(_speak(engine, [_frame(10)]))
        self.assertEqual(_Local.loaded, 1)

    def test_mulaw_audio_is_decoded_before_endpointing(self) -> None:
        import audioop

        def mulaw(amplitude: int) -> RasaAudioBytes:
            pcm = struct.pack("<h", amplitude) * 400  # 50 ms at 8 kHz
            return RasaAudioBytes(audioop.lin2ulaw(pcm, 2), MULAW_8KHZ)

        frames = [mulaw(10)] * 2 + [mulaw(8000)] * 8 + [mulaw(10)] * 16
        events = asyncio.run(_speak(_Local("en", MULAW_8KHZ), frames))
        self.assertEqual(events, [NewTranscript("hello there")])


class TestFasterWhisper(unittest.TestCase):
    def test_a_missing_package_means_not_configured_here(self) -> None:
        import importlib.util

        original = importlib.util.find_spec
        importlib.util.find_spec = lambda name, *a, **k: (
            None if name == "faster_whisper" else original(name, *a, **k)
        )
        try:
            with self.assertRaises(ModuleNotFoundError) as caught:
                FasterWhisperASR.from_config_dict({}, L16_16KHZ, "en", None)
        finally:
            importlib.util.find_spec = original
        self.assertIn("not configured here", str(caught.exception))

    def test_the_defaults_are_a_small_cpu_model(self) -> None:
        config = FasterWhisperASR.get_default_config("en")
        self.assertEqual(config.model_id, "tiny.en")
        self.assertEqual(config.compute_type, "int8")


class TestImports(unittest.TestCase):
    def test_no_provider_module_loads_a_framework_or_vendor_sdk(self) -> None:
        names = ("openai", "speechmatics", "whisper", "_local_asr", "_http_tts")
        code = textwrap.dedent(
            f"""
            import importlib, sys
            for name in {names!r}:
                importlib.import_module("zeo_core.voice.providers." + name)
            forbidden = {{"rasa", "structlog", "aiohttp", "websockets", "numpy",
                          "faster_whisper", "openai", "botocore", "google"}}
            print(sorted(m for m in sys.modules if m.split(".")[0] in forbidden))
            """
        )
        done = subprocess.run(  # noqa: S603 - this interpreter, a fixed snippet
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "[]")

    def test_the_package_is_importable(self) -> None:
        self.assertTrue(importlib.import_module("zeo_core.voice.providers"))

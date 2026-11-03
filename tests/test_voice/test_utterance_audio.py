# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/tests/test_voicerouter.py
# Apache-2.0, see LICENSE and NOTICE
"""Utterance classes and audio conversion."""

from __future__ import annotations

import unittest

from zeo_core.voice.audio import PcmStreamConverter, strip_wav_header
from zeo_core.voice.utterance import UtterancePolicy


class TestUtteranceClassification(unittest.TestCase):
    def test_length_and_pattern_rules(self):
        p = UtterancePolicy.from_dict(
            {
                "filler": {
                    "max_chars": 32,
                    "patterns": [r"^(ok|got it|one moment)\b"],
                    "prefer": ["cheap"],
                }
            }
        )
        self.assertEqual(p.classify("One moment."), "filler")
        self.assertEqual(p.classify("Got it, checking."), "filler")
        self.assertEqual(
            p.classify("Transferring four hundred pounds to Sam."), "default"
        )

    def test_a_class_with_no_rule_is_rejected(self):
        # Otherwise a typo silently captures every utterance.
        with self.assertRaises(ValueError):
            UtterancePolicy.from_dict({"oops": {}})

    def test_unknown_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            UtterancePolicy.from_dict({"oops": {"maxchars": 10}})


class TestAudioConversion(unittest.TestCase):
    """Getting this wrong is audible, not raisable."""

    def _tone(self, seconds=1, rate=24000):
        import math
        import struct

        return b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * t / rate)))
            for t in range(rate * seconds)
        )

    def test_duration_is_preserved_across_odd_chunk_boundaries(self):
        from zeo_core.voice.engine import (
            L16_24KHZ,
            L16_48KHZ,
            MULAW_8KHZ,
        )

        pcm = self._tone()
        for fmt in (L16_24KHZ, MULAW_8KHZ, L16_48KHZ):
            conv = PcmStreamConverter(24000, fmt)
            # 1023 bytes splits a 2-byte frame on nearly every boundary.
            out = b"".join(
                conv.feed(pcm[i : i + 1023]) for i in range(0, len(pcm), 1023)
            )
            self.assertAlmostEqual(
                len(out) / fmt.bytes_per_second, 1.0, places=2, msg=fmt
            )

    def test_wav_header_is_located_not_assumed(self):
        import struct

        pcm = self._tone(rate=16000)[:32000]
        header = (
            b"RIFF"
            + struct.pack("<I", 36 + len(pcm))
            + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data"
            + struct.pack("<I", len(pcm))
        )
        body, rate = strip_wav_header(header + pcm)
        self.assertEqual(len(body), len(pcm))
        self.assertEqual(rate, 16000)

    def test_non_wav_passes_through(self):
        self.assertEqual(strip_wav_header(b"rawpcmbytes"), (b"rawpcmbytes", None))

# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/tests/test_voicerouter.py
# Apache-2.0, see src/zeo_core/voice/LICENSE and NOTICE
"""The routed engines cover the lifecycle surface (empty source root: floor only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zeo_core.voice import contract


class TestEngineContract(unittest.TestCase):
    """The router is not an engine subclass, so this is what keeps it honest."""

    def test_router_covers_everything_rasa_calls(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(contract.check(Path(empty), verbose=False), [])

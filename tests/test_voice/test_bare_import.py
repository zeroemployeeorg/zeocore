"""M1: a bare import of the voice package loads no framework or vendor SDK."""

from __future__ import annotations

import subprocess  # noqa: S404 - runs this interpreter on a fixed snippet
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

#: Top-level packages that must NOT be loaded by `import zeo_core.voice`.
FORBIDDEN = (
    "rasa",
    "zeo_integrations",
    "structlog",
    "aiohttp",
    "websockets",
    "botocore",
    "boto3",
    "google",
    "deepgram",
    "elevenlabs",
    "openai",
)


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this interpreter, a fixed snippet
        [sys.executable, "-I", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestBareImport(unittest.TestCase):
    def test_import_loads_no_framework_aggregate_or_vendor_sdk(self) -> None:
        done = _run(
            f"""
            import sys
            import zeo_core.voice
            bad = sorted(m for m in sys.modules if m.split(".")[0] in {FORBIDDEN!r})
            print(bad)
            """
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "[]")

    def test_an_installed_rasa_changes_nothing_and_is_never_imported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "rasa"
            package.mkdir()
            (package / "__init__.py").write_text(
                "raise AssertionError('zeo_core.voice must not import rasa')\n"
            )
            done = _run(
                f"""
                import sys
                sys.path.insert(0, {tmp!r})
                from zeo_core.voice import engine
                print(engine.AudioFormat.__module__, "rasa" in sys.modules)
                """
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["zeo_core.voice.engine", "False"])


if __name__ == "__main__":
    unittest.main()

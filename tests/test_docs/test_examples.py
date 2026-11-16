"""Smoke-test beginner examples that require only the base installation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


class ExampleCase(NamedTuple):
    """A credential-free example and its stable stdout contract."""

    script: str
    stdout: str
    extra_module: str | None = None


SAFE_EXAMPLES = (
    ExampleCase(
        "runtime_host_catalogue_v1.py",
        "Capability: demo.greet@1.0.0\n"
        "Catalogue entries: 1\n"
        'Normalized request: {"arguments":{"name":"World"},'
        '"capability_id":"demo.greet@1.0.0"}\n'
        "Request digest: sha256:"
        "d7370ae8be73acf22a73ad8eef6f22c80ad84b5571bfd029c4890f716dc1ea7d\n"
        "PREPARATION ONLY: no Runtime admission, handler invocation or provider call\n",
        "rfc8785",
    ),
    ExampleCase(
        "meeting_request_v1.py",
        "Operation: sheets.values.read\n"
        "Resource: sheets:example-sheet:Agenda!A1:C10\n"
        "Request digest: "
        "6bf86c1a8dc8716e377c458e68c33a0e7c78c38e75dbdcc9244027eba3f14b5c\n"
        "Changed range changes digest: True\n"
        "REQUEST ONLY: no authorization minted, Runtime contacted or provider called\n",
    ),
    ExampleCase(
        "environment_usage.py",
        "test: private state prepared; ambient key excluded\n"
        "production: private state prepared; ambient key excluded\n"
        "LOCAL: no provider request; temporary state removed on exit\n",
    ),
    ExampleCase(
        "gemini_request.py",
        "Operation: gemini.generate_reference_image\n"
        "Reference bytes verified: True\n"
        "REQUEST ONLY: no authorization minted; no provider called\n",
    ),
    ExampleCase(
        "zeoconnect_usage.py",
        "FAKE: selected Drive bytes verified; no credential or network\n",
    ),
    ExampleCase(
        "kit_usage.py",
        "Draft created through registered capability: 57\n"
        "Track the new send object: 58\n"
        "HTTP operations: POST, GET, GET, POST\n"
        "SIMULATED: no network, no live send, provider delivery unverified\n",
    ),
    ExampleCase(
        "hubspot_usage.py",
        "Draft created through registered capability: 123\n"
        "HTTP operations: POST, GET, PATCH, GET, POST\n"
        "SIMULATED: no network, no live send, provider delivery unverified\n",
    ),
    ExampleCase(
        "minimal_tool.py",
        "Tool initialized: word_count initialized\n"
        "Result: Word count completed\n"
        "Words: 8, Characters: 73\n",
    ),
    ExampleCase("capability_authoring.py", "Hello, World!\n"),
    ExampleCase(
        "capability_guards.py",
        "accepted: hello-world\n"
        "rejected outcome: guard_rejected\n"
        "rejected code: ZEO_CAP_GUARD_REJECTED\n"
        "rejected has data: False\n",
    ),
    ExampleCase(
        "tool_to_capability.py",
        "class run: 2\n"
        "invoke_sync: 2\n"
        "canonical id: demo.word_count@1.0.0\n"
        "registry hit: True\n",
    ),
    ExampleCase(
        "llm_tools_usage.py",
        "projected name: demo_greet_v1_0_0\n"
        "description: Greet a person by name.\n"
        "required: ['name']\n"
        "properties: ['name']\n"
        "refusal ok: False\n"
        "refusal reason: unsupported JSON Schema keyword 'not' cannot be preserved\n",
    ),
    ExampleCase(
        "notion_demo.py",
        "Notion API NotionClient(api_version='2026-03-11', "
        "timeout_ms=60000, max_retries=3)\n"
        "user.list: 1 item, has_more=False\n"
        "search: 1 item, has_more=False\n"
        "data_source.query: 1 item, has_more=False\n"
        "block.list_children: 1 item, has_more=False\n"
        "comment.list: 1 item, has_more=False\n"
        "SIMULATED: 5 current API calls, no network or credential\n",
    ),
    ExampleCase(
        "notion_governed_upsert.py",
        "operation: notion.page.upsert\n"
        "revision: notion.page-upsert@1\n"
        "marker bound: True\n"
        "source cited: True\n"
        "provider called: False\n",
    ),
)


@pytest.mark.parametrize("case", SAFE_EXAMPLES, ids=lambda case: case.script)
def test_beginner_example_runs_offline(case: ExampleCase, tmp_path: Path) -> None:
    """Run an allowlisted example without credentials or user configuration."""
    if case.extra_module:
        pytest.importorskip(case.extra_module)
    environment = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    completed = subprocess.run(  # noqa: S603 - static, repository-owned scripts
        [sys.executable, str(REPO_ROOT / "examples" / case.script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout == case.stdout

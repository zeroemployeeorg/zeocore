"""The Sheets-as-MCP example works end to end over an in-memory MCP client."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("mcp")

from zeo_core.core.registry import OperationRegistry  # noqa: E402


def _load_example() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "examples" / "sheets_mcp_usage.py"
    spec = importlib.util.spec_from_file_location("sheets_mcp_usage", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["sheets_mcp_usage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_append_then_read_through_mcp() -> None:
    from mcp import Client
    from mcp.types import TextContent

    from zeo_core.adapters.mcp import create_server, register_tool

    example = _load_example()
    sheets = example.InMemorySheets()
    registry = OperationRegistry()
    register_tool(example.SheetsReadTool(sheets), registry=registry)
    register_tool(example.SheetsAppendTool(sheets), registry=registry)
    server = create_server(name="t", registry=registry)

    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"sheets_read_range", "sheets_append_rows"}
        await client.call_tool(
            "sheets_append_rows",
            {"spreadsheet_id": "s", "range_a1": "A1", "rows": [["laptop-7", "ana"]]},
        )
        result = await client.call_tool(
            "sheets_read_range", {"spreadsheet_id": "s", "range_a1": "A1:B9"}
        )
        block = result.content[0]
        assert isinstance(block, TextContent) and "laptop-7" in block.text

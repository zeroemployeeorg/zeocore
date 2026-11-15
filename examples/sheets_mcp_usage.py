"""
Example: exposing Google Sheets read/append as MCP tools.

Requires the 'mcp' extra:

    uv pip install "zeocore[mcp]"

Two ordinary BaseZeoTool classes wrap the Sheets service. register_tool()
turns each into an MCP tool from its own run() type hint, exactly as in
examples/mcp_server_usage.py; nothing here is MCP-specific.

By default this runs offline against an in-memory stand-in, so you can see the
full MCP round trip with no Google account:

    uv run examples/sheets_mcp_usage.py

To serve the real Google Sheets integration to an MCP client over stdio,
build the tools with the zeo-integrations Google Sheets service (the package
`zeo-integrations`; its setup guide covers the OAuth files) and call
`zeo_core.adapters.mcp.run()` instead of the in-memory Client. The service
only needs `get_values` and `append_values` with the signatures of `SheetsLike`
below, so zeocore does not import or depend on it:

    from zeo_integrations.google.sheets import GoogleSheetsService

    service = GoogleSheetsService(
        client_secrets_file="config/google_client_secret.json",
        credentials_file="config/google_credentials.json",
    )
    service.initialize()
    register_tool(SheetsReadTool(service))
    register_tool(SheetsAppendTool(service))
    run(name="zeocore-sheets")
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from pydantic import BaseModel, Field

from zeo_core.adapters.mcp import create_server, register_tool
from zeo_core.contracts import CapabilityResult
from zeo_core.integrations.core import IntegrationResult
from zeo_core.tools import BaseZeoTool, ToolContext


class SheetsLike(Protocol):
    """The two Sheets service methods these tools call (as in zeo-integrations)."""

    def get_values(
        self, spreadsheet_id: str, range_a1: str
    ) -> IntegrationResult[dict[str, Any]]: ...

    def append_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        value_input_option: str = "USER_ENTERED",
    ) -> IntegrationResult[dict[str, Any]]: ...


class ReadRangeRequest(BaseModel):
    spreadsheet_id: str = Field(min_length=1)
    range_a1: str = Field(min_length=1, description='A1 range, e.g. "Assets!A1:F50"')


class AppendRowsRequest(BaseModel):
    spreadsheet_id: str = Field(min_length=1)
    range_a1: str = Field(min_length=1, description='Table range, e.g. "Assets!A1"')
    rows: list[list[str | int | float | bool]] = Field(min_length=1)


class SheetsResponse(BaseModel):
    result: dict[str, Any]


class SheetsReadTool(BaseZeoTool):
    """Read a range of cells from a Google Sheet."""

    name = "sheets_read_range"
    version = "1.0.0"

    def __init__(self, service: SheetsLike) -> None:
        super().__init__()
        self._service = service

    def run(
        self, request: ReadRangeRequest, ctx: ToolContext
    ) -> CapabilityResult[SheetsResponse]:
        out = self._service.get_values(request.spreadsheet_id, request.range_a1)
        if not out.success:
            return CapabilityResult.fail(
                msg=out.error or "Sheets read failed", code="sheets_read_failed"
            )
        return CapabilityResult.ok(
            data=SheetsResponse(result=out.content or {}), msg="Read"
        )


class SheetsAppendTool(BaseZeoTool):
    """Append rows after the last row of a Google Sheet table."""

    name = "sheets_append_rows"
    version = "1.0.0"

    def __init__(self, service: SheetsLike) -> None:
        super().__init__()
        self._service = service

    def run(
        self, request: AppendRowsRequest, ctx: ToolContext
    ) -> CapabilityResult[SheetsResponse]:
        out = self._service.append_values(
            request.spreadsheet_id,
            request.range_a1,
            [list(row) for row in request.rows],
        )
        if not out.success:
            return CapabilityResult.fail(
                msg=out.error or "Sheets append failed", code="sheets_append_failed"
            )
        return CapabilityResult.ok(
            data=SheetsResponse(result=out.content or {}), msg="Appended"
        )


class InMemorySheets:
    """Offline stand-in with the same two methods, for the example and tests."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = [["asset", "owner"]]

    def get_values(
        self, spreadsheet_id: str, range_a1: str
    ) -> IntegrationResult[dict[str, Any]]:
        return IntegrationResult.success_result(
            content={"range": range_a1, "majorDimension": "ROWS", "values": self.rows}
        )

    def append_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        value_input_option: str = "USER_ENTERED",
    ) -> IntegrationResult[dict[str, Any]]:
        self.rows.extend(values)
        return IntegrationResult.success_result(
            content={"updates": {"updatedRows": len(values)}}
        )


async def main() -> None:
    sheets = InMemorySheets()
    register_tool(SheetsReadTool(sheets))
    register_tool(SheetsAppendTool(sheets))
    server = create_server(name="zeocore-sheets-example")

    from mcp import Client
    from mcp.types import TextContent

    async with Client(server) as client:
        tools = await client.list_tools()
        print(f"MCP tools exposed: {[t.name for t in tools.tools]}")
        await client.call_tool(
            "sheets_append_rows",
            {
                "spreadsheet_id": "demo",
                "range_a1": "Assets!A1",
                "rows": [["laptop-7", "ana"]],
            },
        )
        result = await client.call_tool(
            "sheets_read_range", {"spreadsheet_id": "demo", "range_a1": "Assets!A1:B10"}
        )
        block = result.content[0]
        print(block.text if isinstance(block, TextContent) else repr(block))


if __name__ == "__main__":
    asyncio.run(main())

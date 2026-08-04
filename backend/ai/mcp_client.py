"""
MCP client for the SafeMAPS AI demo.

This module intentionally talks to the SafeMAPS MCP server over the MCP
streamable-HTTP protocol. The AI demo should not call routing/AQI functions
directly, because the portfolio value is showing a real MCP client + server.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPCallResult:
    tool: str
    arguments: dict[str, Any]
    result: Any
    duration_ms: int


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _decode_tool_content(content: list[Any]) -> Any:
    decoded = []
    for item in content:
        data = _model_dump(item)
        text = data.get("text") if isinstance(data, dict) else None
        if text:
            try:
                decoded.append(json.loads(text))
            except json.JSONDecodeError:
                decoded.append(text)
        else:
            decoded.append(data)
    if len(decoded) == 1:
        return decoded[0]
    return decoded


class SafeMapsMCPClient:
    def __init__(self, url: str, timeout_seconds: int = 30):
        self.url = url
        self.timeout_seconds = timeout_seconds

    async def list_tools(self) -> list[MCPTool]:
        async with streamablehttp_client(
            self.url,
            timeout=self.timeout_seconds,
            sse_read_timeout=self.timeout_seconds,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                return [
                    MCPTool(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=_model_dump(tool.inputSchema),
                    )
                    for tool in response.tools
                ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPCallResult:
        started = time.perf_counter()
        async with streamablehttp_client(
            self.url,
            timeout=self.timeout_seconds,
            sse_read_timeout=self.timeout_seconds,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(name, arguments)

        duration_ms = int((time.perf_counter() - started) * 1000)
        if getattr(response, "isError", False):
            result = {"error": _decode_tool_content(response.content)}
        else:
            result = _decode_tool_content(response.content)

        return MCPCallResult(
            tool=name,
            arguments=arguments,
            result=result,
            duration_ms=duration_ms,
        )


def tools_for_anthropic(tools: list[MCPTool]) -> list[dict[str, Any]]:
    """Convert discovered MCP tool schemas into Anthropic tool definitions."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema or {"type": "object", "properties": {}},
        }
        for tool in tools
    ]

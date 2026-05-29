"""MCP Client Adapter — schemas for tool call results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MCPToolResult:
    """Result from calling an MCP tool."""

    tool_name: str
    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None

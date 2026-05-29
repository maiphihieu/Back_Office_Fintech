#!/usr/bin/env python3
"""Run the Fintech Back-office MCP Server via stdio transport.

Usage:
    python scripts/run_mcp_server.py

The server communicates over stdin/stdout using the MCP protocol.
Connect any MCP client (e.g. Claude Desktop, LangChain MCP adapter)
to this process.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure src/ is on the path when running as a script
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# Load .env before importing anything that reads settings
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def main() -> None:
    """Start the MCP server on stdio transport."""
    from fintech_agent.mcp_server.server import mcp

    # run_stdio_async reads/writes MCP JSON-RPC via stdin/stdout
    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())

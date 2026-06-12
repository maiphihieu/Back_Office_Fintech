"""MCP Client Adapter — routes all tool calls through the MCP server.

Every tool call is sent to the MCP server over stdio using the real MCP
protocol (JSON-RPC 2.0). The server (mcp_server/server.py) enforces safety
guards, idempotency, and repository abstraction before delegating to handlers.

    LangGraph node → FintechMCPClient → ClientSession (stdio / JSON-RPC)
                   → server.py → handlers.py

The MCP server runs as a single long-lived subprocess. The first tool call
spins it up and performs the MCP handshake once; every subsequent call reuses
the same process and session, so there is no per-call process startup cost.

The session lives on a dedicated background event loop (its own thread), so
``call_tool_sync`` works both from plain sync code and from inside an async
context (e.g. FastAPI/uvicorn) without touching the caller's event loop.

The server command is configurable via env vars:
  - FINTECH_MCP_COMMAND: interpreter to run (default: current Python)
  - FINTECH_MCP_ARGS: server script + args (default: scripts/run_mcp_server.py)

Usage:
    from fintech_agent.mcp_client.client import get_mcp_client
    mcp = get_mcp_client()
    result = mcp.call_tool_sync("get_transaction", {"transaction_id": "TXN_001"})
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_logger = logging.getLogger(__name__)

_CALL_TIMEOUT = 30.0   # seconds per tool call
_START_TIMEOUT = 60.0  # seconds to bring up the server subprocess


class _PersistentSession:
    """A long-lived MCP session backed by a single server subprocess.

    The stdio transport and ClientSession are opened once on a private
    event loop running in a dedicated daemon thread, then kept alive. Tool
    calls are dispatched onto that loop via ``run_coroutine_threadsafe``.
    """

    def __init__(self, server_params: StdioServerParameters) -> None:
        self._server_params = server_params
        self._loop = asyncio.new_event_loop()
        self._session: ClientSession | None = None
        self._shutdown: asyncio.Event | None = None  # created on the loop
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._thread_main, name="mcp-session", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=_START_TIMEOUT):
            raise RuntimeError("MCP server did not start within timeout")
        if self._start_error is not None:
            raise self._start_error

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._manage())
        finally:
            self._loop.close()

    async def _manage(self) -> None:
        """Open the session, signal ready, then stay alive until shutdown.

        Opening and closing the transport happen in this single task so the
        underlying anyio cancel scopes are never crossed between tasks.
        """
        self._shutdown = asyncio.Event()
        try:
            async with stdio_client(self._server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._shutdown.wait()
        except BaseException as e:  # noqa: BLE001 — surface startup failures to caller
            self._start_error = e
            self._ready.set()

    def call(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch a tool call onto the session loop and wait for the result."""
        session = self._session
        if session is None:
            return {"error": "MCP session not initialized"}
        try:
            future = asyncio.run_coroutine_threadsafe(
                session.call_tool(tool_name, arguments), self._loop
            )
            result = future.result(timeout=_CALL_TIMEOUT)
        except TimeoutError:
            return {"error": f"MCP server timed out for tool: {tool_name}"}
        except Exception as e:
            _logger.exception("MCP call failed: %s", tool_name)
            return {"error": f"{tool_name} failed: {e}"}
        return self._parse_result(result, tool_name)

    @staticmethod
    def _parse_result(result: Any, tool_name: str) -> dict:
        """Unwrap a CallToolResult into the plain dict callers expect."""
        text = None
        content = getattr(result, "content", None) or []
        for block in content:
            block_text = getattr(block, "text", None)
            if block_text is not None:
                text = block_text
                break

        if getattr(result, "isError", False):
            return {"error": text or f"{tool_name} returned an error"}

        if text is None:
            # Fall back to structured content if no text block was returned
            structured = getattr(result, "structuredContent", None)
            return structured if isinstance(structured, dict) else {}

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": text}

    def close(self) -> None:
        """Signal the session to shut down and wait for the thread to exit."""
        if self._shutdown is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._shutdown.set)
        self._thread.join(timeout=10)


class FintechMCPClient:
    """MCP client adapter for fintech back-office tools.

    Routes every tool call to the MCP server through a persistent stdio
    ClientSession (JSON-RPC 2.0), reused across calls.
    """

    def __init__(self) -> None:
        self._session: _PersistentSession | None = None
        self._lock = threading.Lock()

    def _ensure_session(self) -> _PersistentSession:
        """Lazily start the persistent server session (thread-safe, once)."""
        if self._session is None:
            with self._lock:
                if self._session is None:
                    self._session = _PersistentSession(self._build_server_params())
        return self._session

    @staticmethod
    def _build_server_params() -> StdioServerParameters:
        command = os.environ.get("FINTECH_MCP_COMMAND", sys.executable)
        args = os.environ.get("FINTECH_MCP_ARGS", "scripts/run_mcp_server.py").split()
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        return StdioServerParameters(
            command=command,
            args=args,
            cwd=str(project_root),
            env=dict(os.environ),  # inherit full parent env (Supabase keys, etc.)
        )

    def call_tool_sync(self, tool_name: str, arguments: dict) -> dict:
        """Call an MCP tool synchronously (for sync LangGraph nodes).

        Returns the tool result as a dict; contains an "error" key on failure.
        """
        try:
            return self._ensure_session().call(tool_name, arguments)
        except Exception as e:
            _logger.exception("MCP call failed: %s", tool_name)
            return {"error": f"{tool_name} failed: {e}"}

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Async interface — runs the sync call off the caller's event loop."""
        return await asyncio.to_thread(self.call_tool_sync, tool_name, arguments)

    def close(self) -> None:
        """Tear down the persistent server session, if any."""
        if self._session is not None:
            self._session.close()
            self._session = None


# ═══════════════════════════════════════════════════════════════
#  Singleton / factory
# ═══════════════════════════════════════════════════════════════

_client: FintechMCPClient | None = None


def get_mcp_client() -> FintechMCPClient:
    """Get or create the singleton MCP client.

    All tool calls route through a persistent MCP server session over stdio.
    """
    global _client
    if _client is None:
        _logger.info("Creating FintechMCPClient (persistent stdio MCP session)")
        _client = FintechMCPClient()
    return _client


def reset_mcp_client() -> None:
    """Reset the singleton client (for testing). Closes the server session."""
    global _client
    if _client is not None:
        _client.close()
    _client = None

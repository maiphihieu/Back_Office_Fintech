"""MCP Client Adapter — abstracts tool calls through MCP boundary.

Provides a unified interface for LangGraph nodes to call tools.
All tool calls go through this adapter, which routes to the MCP server.

Supports two modes (controlled by FINTECH_TOOL_MODE env var):
  - "mcp" (default): Calls MCP server via subprocess stdio transport
  - "in_process": Calls MCP server handlers directly in-process
    (faster, same safety guarantees, ideal for dev/test)

Both modes go through the same MCP handler layer which enforces
safety guards, idempotency, and repository abstraction.

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
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


class FintechMCPClient:
    """MCP client adapter for fintech back-office tools.

    Routes tool calls to the MCP server handlers.
    Supports both async and sync calling conventions.
    """

    def __init__(self, mode: str = "in_process") -> None:
        """Initialize the MCP client.

        Args:
            mode: "mcp" for subprocess stdio, "in_process" for direct handler calls.
        """
        self._mode = mode
        self._handler_map: dict[str, Any] | None = None

    def _get_handler_map(self) -> dict[str, Any]:
        """Lazy-load the handler mapping."""
        if self._handler_map is None:
            from fintech_agent.mcp_server.handlers import (
                handle_create_customer_response_draft,
                handle_create_force_success_draft,
                handle_create_reconciliation_ticket_draft,
                handle_create_refund_request_draft,
                handle_create_request_documents_response_draft,
                handle_create_unlock_account_draft,
                handle_get_account_status,
                handle_get_bank_transfer_receipt,
                handle_get_fraud_case,
                handle_get_merchant_bank_account,
                handle_get_merchant_payout,
                handle_get_merchant_profile,
                handle_get_merchant_settlement_ledger,
                handle_get_reconciliation_status,
                handle_get_refund_status,
                handle_get_settlement_batch,
                handle_get_train_provider_status,
                handle_get_transaction,
                handle_get_user_by_email,
                handle_get_user_by_phone,
                handle_get_user_by_wallet_id,
                handle_get_utility_bill_status,
                handle_get_wallet_ledger,
            )

            self._handler_map = {
                "get_transaction": handle_get_transaction,
                "get_reconciliation_status": handle_get_reconciliation_status,
                "get_wallet_ledger": handle_get_wallet_ledger,
                "get_refund_status": handle_get_refund_status,
                "get_train_provider_status": handle_get_train_provider_status,
                "get_utility_bill_status": handle_get_utility_bill_status,
                "get_account_status": handle_get_account_status,
                "get_fraud_case": handle_get_fraud_case,
                "get_user_by_phone": handle_get_user_by_phone,
                "get_user_by_email": handle_get_user_by_email,
                "get_user_by_wallet_id": handle_get_user_by_wallet_id,
                "create_refund_request_draft": handle_create_refund_request_draft,
                "create_reconciliation_ticket_draft": handle_create_reconciliation_ticket_draft,
                "create_customer_response_draft": handle_create_customer_response_draft,
                "create_force_success_draft": handle_create_force_success_draft,
                "create_unlock_account_draft": handle_create_unlock_account_draft,
                "create_request_documents_response_draft": handle_create_request_documents_response_draft,
                # Merchant settlement read-only tools (Case 3)
                "get_merchant_profile": handle_get_merchant_profile,
                "get_merchant_bank_account": handle_get_merchant_bank_account,
                "get_settlement_batch": handle_get_settlement_batch,
                "get_merchant_settlement_ledger": handle_get_merchant_settlement_ledger,
                "get_merchant_payout": handle_get_merchant_payout,
                "get_bank_transfer_receipt": handle_get_bank_transfer_receipt,
            }
        return self._handler_map

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call an MCP tool asynchronously.

        Args:
            tool_name: Name of the tool to call (e.g. "get_transaction")
            arguments: Tool arguments as a dict

        Returns:
            Tool result as a dict. Contains "error" key on failure.
        """
        if self._mode == "in_process":
            return await self._call_in_process(tool_name, arguments)
        else:
            return await self._call_via_stdio(tool_name, arguments)

    def call_tool_sync(self, tool_name: str, arguments: dict) -> dict:
        """Call an MCP tool synchronously (for sync LangGraph nodes).

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as a dict

        Returns:
            Tool result as a dict. Contains "error" key on failure.
        """
        if self._mode == "in_process":
            return self._call_in_process_sync(tool_name, arguments)
        else:
            return self._call_via_stdio_sync(tool_name, arguments)

    # ─── In-process mode ────────────────────────────────────

    async def _call_in_process(self, tool_name: str, arguments: dict) -> dict:
        """Call handler directly in-process (async)."""
        handler_map = self._get_handler_map()
        handler = handler_map.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            result = await handler(**arguments)
            return result
        except Exception as e:
            _logger.exception("MCP in-process call failed: %s", tool_name)
            return {"error": f"{tool_name} failed: {e}"}

    def _call_in_process_sync(self, tool_name: str, arguments: dict) -> dict:
        """Call handler directly in-process (sync wrapper).

        Handles the case where we're already inside an event loop
        (e.g., inside FastAPI/uvicorn) by running in a separate thread.
        """
        handler_map = self._get_handler_map()
        handler = handler_map.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            # Try to get the running loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # We're inside an async context (FastAPI/uvicorn)
                # Run the coroutine in a new thread with its own event loop
                result: dict = {}
                exception: BaseException | None = None

                def _run_in_thread():
                    nonlocal result, exception
                    try:
                        result = asyncio.run(handler(**arguments))
                    except BaseException as e:
                        exception = e

                thread = threading.Thread(target=_run_in_thread)
                thread.start()
                thread.join(timeout=30)
                if exception:
                    raise exception
                return result
            else:
                # No running loop — safe to use asyncio.run
                return asyncio.run(handler(**arguments))
        except Exception as e:
            _logger.exception("MCP in-process sync call failed: %s", tool_name)
            return {"error": f"{tool_name} failed: {e}"}

    # ─── Stdio subprocess mode ──────────────────────────────

    async def _call_via_stdio(self, tool_name: str, arguments: dict) -> dict:
        """Call MCP server via subprocess stdio (async).

        Spins up the MCP server process, sends a tools/call request,
        and reads the response. Uses JSON-RPC protocol.
        """
        return self._call_via_stdio_sync(tool_name, arguments)

    def _call_via_stdio_sync(self, tool_name: str, arguments: dict) -> dict:
        """Call MCP server via subprocess stdio (sync).

        Sends a JSON-RPC request to the MCP server process over stdin
        and reads the result from stdout.
        """
        mcp_command = os.environ.get("FINTECH_MCP_COMMAND", sys.executable)
        mcp_args = os.environ.get("FINTECH_MCP_ARGS", "scripts/run_mcp_server.py")
        cmd = [mcp_command] + mcp_args.split()

        # MCP uses JSON-RPC 2.0 over stdio
        # First send initialize, then tools/call
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fintech-mcp-client", "version": "1.0.0"},
            },
        }
        call_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        # Newline-delimited JSON-RPC
        stdin_data = json.dumps(init_request) + "\n" + json.dumps(call_request) + "\n"

        try:
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            proc = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(project_root),
            )

            # Parse the last non-empty line from stdout as the response
            lines = [ln.strip() for ln in proc.stdout.strip().split("\n") if ln.strip()]
            if not lines:
                return {"error": f"MCP server returned no output. stderr: {proc.stderr[:500]}"}

            # Find the tools/call response (id=2)
            for line in reversed(lines):
                try:
                    resp = json.loads(line)
                    if resp.get("id") == 2:
                        result = resp.get("result", {})
                        # MCP tools/call returns content array
                        content = result.get("content", [])
                        if content and isinstance(content, list):
                            text = content[0].get("text", "{}")
                            return json.loads(text)
                        return result
                except json.JSONDecodeError:
                    continue

            return {"error": "No valid JSON-RPC response from MCP server"}

        except subprocess.TimeoutExpired:
            return {"error": f"MCP server timed out for tool: {tool_name}"}
        except FileNotFoundError:
            return {"error": f"MCP server command not found: {cmd}"}
        except Exception as e:
            _logger.exception("MCP stdio call failed: %s", tool_name)
            return {"error": f"MCP stdio failed: {e}"}


# ═══════════════════════════════════════════════════════════════
#  Singleton / factory
# ═══════════════════════════════════════════════════════════════

_client: FintechMCPClient | None = None


def get_mcp_client() -> FintechMCPClient:
    """Get or create the singleton MCP client.

    Reads FINTECH_TOOL_MODE from environment:
      - "mcp": Use subprocess stdio transport (production)
      - "in_process" (default): Use direct in-process calls (dev/test)

    Both modes route through the same MCP handler layer.
    """
    global _client
    if _client is None:
        mode = os.environ.get("FINTECH_TOOL_MODE", "in_process")
        _logger.info("Creating FintechMCPClient in '%s' mode", mode)
        _client = FintechMCPClient(mode=mode)
    return _client


def reset_mcp_client() -> None:
    """Reset the singleton client (for testing)."""
    global _client
    _client = None

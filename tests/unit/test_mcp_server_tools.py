"""Unit tests for MCP server tools — verifies tools work through the MCP client.

Tests use the in-process MCP client which calls MCP handlers directly.
All data comes from Supabase (SUPABASE_ENABLED=true).
"""

import pytest

from fintech_agent.mcp_client.client import FintechMCPClient


@pytest.fixture
def mcp() -> FintechMCPClient:
    """Create a fresh in-process MCP client for each test."""
    return FintechMCPClient(mode="in_process")


class TestReadOnlyTools:
    """Read-only tools should return data without modifying anything."""

    def test_get_transaction_found(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("get_transaction", {"transaction_id": "TXN_TOPUP_001"})
        assert "error" not in result
        assert result["transaction_id"] == "TXN_TOPUP_001"
        assert result["status"] == "pending"
        assert result["service_type"] == "wallet_topup"
        assert result["amount"] == 500000

    def test_get_transaction_not_found(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("get_transaction", {"transaction_id": "NONEXISTENT"})
        assert "error" in result

    def test_get_reconciliation_status_found(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("get_reconciliation_status", {"transaction_id": "TXN_TOPUP_001"})
        assert "error" not in result
        assert result["transaction_id"] == "TXN_TOPUP_001"
        assert result["mismatch_type"] == "bank_success_wallet_pending"
        assert result["bank_status"] == "success"
        assert result["money_received_in_master_wallet"] is True

    def test_get_reconciliation_not_found(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("get_reconciliation_status", {"transaction_id": "NONEXISTENT"})
        assert "error" in result

    def test_unknown_tool_returns_error(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("nonexistent_tool", {})
        assert "error" in result


class TestDraftTools:
    """Draft tools should create drafts without executing financial ops."""

    def test_force_success_draft(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("create_force_success_draft", {
            "case_id": "CASE_TEST_MCP_001",
            "transaction_id": "TXN_TOPUP_001",
            "user_id": "U_TOPUP_001",
            "amount": 500000,
            "reason": "bank success, wallet pending",
            "evidence_summary": ["bank_status=success", "money_received=true"],
        })
        assert "error" not in result
        assert result["type"] == "force_success_draft"
        assert result["draft_id"] == "DRAFT_FORCE_SUCCESS_TXN_TOPUP_001"
        assert result["approval_required"] is True
        assert result["status"] == "pending_approval"
        assert "Human approval required" in result["note"]

    def test_force_success_draft_zero_amount_fails(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("create_force_success_draft", {
            "case_id": "CASE_TEST_MCP_002",
            "transaction_id": "TXN_TOPUP_001",
            "user_id": "U_TOPUP_001",
            "amount": 0,
            "reason": "test",
            "evidence_summary": ["test"],
        })
        assert "error" in result

    def test_refund_request_draft(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("create_refund_request_draft", {
            "case_id": "CASE_TEST_MCP_003",
            "transaction_id": "TXN_TRAIN_001",
            "user_id": "U001",
            "amount": 450000,
            "reason": "ticket not issued",
            "evidence_summary": ["wallet debited", "ticket not issued"],
        })
        assert "error" not in result
        assert result["type"] == "refund_request_draft"
        assert result["approval_required"] is True

    def test_customer_response_draft(self, mcp: FintechMCPClient) -> None:
        result = mcp.call_tool_sync("create_customer_response_draft", {
            "case_id": "CASE_TEST_MCP_004",
            "transaction_id": "TXN_TRAIN_002",
            "message": "Vé đã được phát hành thành công.",
        })
        assert "error" not in result
        assert result["type"] == "customer_response_draft"
        assert result["approval_required"] is False

"""Integration test: wallet topup end-to-end MCP-first flow.

Verifies the complete LangGraph workflow for use case 1:
  Input → Extract → Fetch via MCP → Rules → Recommend → Approval Gate → Draft via MCP

All tool calls go through MCP client adapter → MCP handlers → Supabase.
"""

import pytest

from fintech_agent.graph.builder import compile_graph
from fintech_agent.schemas.enums import ActionType, CaseStatus, RiskLevel


TOPUP_COMPLAINT = (
    "Tôi nạp tiền từ ngân hàng vào ví, tài khoản ngân hàng đã trừ tiền "
    "nhưng ví vẫn báo 0 đồng. Mã giao dịch TXN_TOPUP_001"
)


class TestWalletTopupE2E:
    """End-to-end: complaint → waiting_approval with force_success_draft."""

    def test_extract_info(self) -> None:
        """Extract must detect wallet_topup service and topup_pending issue."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        ei = result["extracted_info"]
        assert ei.transaction_id == "TXN_TOPUP_001"
        assert ei.service_type == "wallet_topup"
        assert ei.issue_type == "topup_pending"

    def test_workflow_routing(self) -> None:
        """Workflow must be routed to wallet_topup."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        assert result["selected_workflow"] == "wallet_topup"

    def test_evidence_fetched_via_mcp(self) -> None:
        """Evidence must include transaction + reconciliation from Supabase."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        ev = result["evidence_bundle"]
        assert ev.transaction is not None
        assert ev.transaction.transaction_id == "TXN_TOPUP_001"
        assert ev.transaction.status == "pending"
        assert ev.transaction.amount == 500000

        assert ev.reconciliation_status is not None
        assert ev.reconciliation_status.bank_status == "success"
        assert ev.reconciliation_status.money_received_in_master_wallet is True

    def test_rule_decision_force_success(self) -> None:
        """Rule engine must recommend create_force_success_draft."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        rd = result["rule_decision"]
        assert rd["action"] == "create_force_success_draft"
        assert rd["approval_required"] is True

    def test_recommended_action(self) -> None:
        """Recommended action must be force_success_draft with high risk."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        ra = result["recommended_action"]
        assert ra.action_type == ActionType.CREATE_FORCE_SUCCESS_DRAFT
        assert ra.risk_level == RiskLevel.HIGH
        assert ra.approval_required is True

    def test_stops_at_waiting_approval(self) -> None:
        """Graph must pause at waiting_approval — no draft without human approval."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        assert result["status"] == CaseStatus.WAITING_APPROVAL
        assert result["approval_status"] == "pending"
        assert result["approval_required"] is True
        # No draft before approval
        assert result.get("draft_output") is None

    def test_no_wallet_ledger_needed(self) -> None:
        """wallet_topup workflow must NOT fail due to missing wallet_ledger."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        ev = result["evidence_bundle"]
        # wallet_ledger is not required for wallet_topup
        # The flow should succeed regardless
        assert result["status"] == CaseStatus.WAITING_APPROVAL

    def test_mcp_tool_results_recorded(self) -> None:
        """Tool results dict must show which MCP tools were called."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": TOPUP_COMPLAINT,
            "user_id": "U_TOPUP_001",
        })
        tr = result.get("tool_results", {})
        assert tr.get("transaction") == "ok"
        assert tr.get("reconciliation") == "ok"


class TestWalletTopupSafetyInvariants:
    """Safety: no real money operations are executed."""

    def test_no_execute_force_success_tool(self) -> None:
        """There must be no execute_force_success tool in the MCP server."""
        from fintech_agent.mcp_server.server import mcp
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "execute_force_success" not in tool_names
        assert "update_wallet_balance" not in tool_names
        assert "edit_ledger" not in tool_names
        assert "mark_transaction_success" not in tool_names

    def test_force_success_draft_does_not_modify_data(self) -> None:
        """Calling create_force_success_draft must not change transaction status."""
        from fintech_agent.mcp_client.client import FintechMCPClient
        from fintech_agent.tools.draft_action_tools import reset_default_store

        # Reset the global draft store to avoid idempotency collision
        reset_default_store()

        client = FintechMCPClient()

        # Read transaction before
        before = client.call_tool_sync("get_transaction", {"transaction_id": "TXN_TOPUP_001"})
        assert before["status"] == "pending"

        # Create draft
        draft = client.call_tool_sync("create_force_success_draft", {
            "case_id": "CASE_SAFETY_INVARIANT_TEST",
            "transaction_id": "TXN_TOPUP_001",
            "user_id": "U_TOPUP_001",
            "amount": 500000,
            "reason": "safety test",
            "evidence_summary": ["test"],
        })
        assert draft.get("type") == "force_success_draft"

        # Read transaction after — must still be pending
        after = client.call_tool_sync("get_transaction", {"transaction_id": "TXN_TOPUP_001"})
        assert after["status"] == "pending"

        client.close()

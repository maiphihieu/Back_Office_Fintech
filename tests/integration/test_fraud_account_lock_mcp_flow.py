"""Integration tests for fraud/account lock MCP flow.

Tests the full pipeline: extract → fetch_evidence → rule_decision → recommendation → draft_action.
Verifies MCP-first architecture: all data flows through MCP client → handlers → repos.
"""

from __future__ import annotations

import pytest

from fintech_agent.llm.mock_extractor import mock_extract
from fintech_agent.mcp_client.client import get_mcp_client
from fintech_agent.nodes.draft_action import create_draft
from fintech_agent.nodes.extract_info import extract_info
from fintech_agent.nodes.fetch_evidence import fetch_evidence
from fintech_agent.nodes.recommendation import recommend_action
from fintech_agent.nodes.rule_decision import apply_rules
from fintech_agent.nodes.workflow_router import route_workflow
from fintech_agent.schemas.enums import ActionType, CaseStatus


# ═══════════════════════════════════════════════════════════════
#  High-Risk Fraud Flow (U_FRAUD_001)
# ═══════════════════════════════════════════════════════════════


class TestHighRiskFraudFlow:
    """Full flow: U_FRAUD_001, risk_score=82, suspicious signals.

    Expected: create_request_documents_response_draft, approval required.
    """

    COMPLAINT = "Tài khoản của tôi bất ngờ bị khóa vô cớ, tôi không thể rút tiền. User ID U_FRAUD_001"

    def test_01_extraction(self):
        """Mock extractor detects account_security + account_locked."""
        result = mock_extract(self.COMPLAINT)
        assert result.service_type == "account_security"
        assert result.issue_type == "account_locked"
        assert result.user_id == "U_FRAUD_001"
        # Fraud cases don't need transaction_id
        assert "transaction_id" not in result.missing_fields

    def test_02_extract_info_node(self):
        """Extract_info node maps to fraud_account_lock workflow."""
        state = {
            "case_id": "CASE_FRAUD_TEST_001",
            "raw_complaint": self.COMPLAINT,
        }
        result = extract_info(state)
        assert result["selected_workflow"] == "fraud_account_lock"
        assert result["extracted_info"].user_id == "U_FRAUD_001"
        assert result["extracted_info"].service_type == "account_security"

    def test_03_workflow_router(self):
        """Router accepts fraud_account_lock workflow."""
        state = {
            "case_id": "CASE_FRAUD_TEST_001",
            "selected_workflow": "fraud_account_lock",
        }
        result = route_workflow(state)
        assert result["status"] == CaseStatus.RULE_DECISION

    def test_04_fetch_evidence_via_mcp(self):
        """Fetch_evidence calls get_account_status + get_fraud_case via MCP."""
        state = {
            "case_id": "CASE_FRAUD_TEST_001",
            "extracted_info": mock_extract(self.COMPLAINT),
        }
        result = fetch_evidence(state)

        # Should have fetched evidence
        assert result["selected_workflow"] == "fraud_account_lock"
        assert result["evidence_bundle"] is not None

        # Check tool results
        tool_results = result.get("tool_results", {})
        assert tool_results.get("account_status") == "ok", f"account_status fetch failed: {result.get('errors', [])}"
        assert tool_results.get("fraud_case") == "ok", f"fraud_case fetch failed: {result.get('errors', [])}"

        # Evidence bundle should contain account + fraud data
        evidence = result["evidence_bundle"]
        assert evidence.account_status is not None
        assert evidence.account_status.account_status == "locked"
        assert evidence.fraud_case is not None
        assert evidence.fraud_case.risk_score == 82

    def test_05_rule_decision(self):
        """Rule engine produces create_request_documents_response_draft."""
        state = {
            "case_id": "CASE_FRAUD_TEST_001",
            "selected_workflow": "fraud_account_lock",
            "extracted_info": mock_extract(self.COMPLAINT),
        }
        ev_result = fetch_evidence(state)
        state.update(ev_result)

        rule_result = apply_rules(state)
        decision = rule_result["rule_decision"]
        assert decision["action"] == ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT.value
        assert decision["diagnosis"] == "suspicious_activity_keep_locked_request_documents"
        assert decision["approval_required"] is True

    def test_06_recommendation(self):
        """Recommendation sets risk=HIGH and approval_required=True."""
        state = {
            "case_id": "CASE_FRAUD_TEST_001",
            "selected_workflow": "fraud_account_lock",
            "extracted_info": mock_extract(self.COMPLAINT),
        }
        ev_result = fetch_evidence(state)
        state.update(ev_result)
        rule_result = apply_rules(state)
        state.update(rule_result)

        rec_result = recommend_action(state)
        action = rec_result["recommended_action"]
        assert action.action_type == ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT
        assert action.risk_level == "high"
        # approval_required is set by apply_rules, not recommend_action
        assert state["approval_required"] is True

    def test_07_draft_action_via_mcp(self):
        """Draft action calls create_request_documents_response_draft via MCP."""
        state = {
            "case_id": "CASE_FRAUD_TEST_001",
            "selected_workflow": "fraud_account_lock",
            "extracted_info": mock_extract(self.COMPLAINT),
            "user_id": "U_FRAUD_001",
        }
        ev_result = fetch_evidence(state)
        state.update(ev_result)
        rule_result = apply_rules(state)
        state.update(rule_result)
        rec_result = recommend_action(state)
        state.update(rec_result)

        draft_result = create_draft(state)
        assert draft_result["status"] == CaseStatus.DRAFT_CREATED
        assert draft_result["draft_output"]["type"] == "request_documents_response_draft"
        assert "draft_id" in draft_result["draft_output"]


# ═══════════════════════════════════════════════════════════════
#  False Positive Flow (U_FRAUD_002)
# ═══════════════════════════════════════════════════════════════


class TestFalsePositiveFlow:
    """Full flow: U_FRAUD_002, risk_score=25, false positive.

    Expected: create_unlock_account_draft, approval required.
    """

    COMPLAINT = "Tài khoản của tôi bị khóa vô cớ, tôi không thể rút tiền. User ID U_FRAUD_002"

    def test_01_extraction(self):
        result = mock_extract(self.COMPLAINT)
        assert result.service_type == "account_security"
        assert result.user_id == "U_FRAUD_002"

    def test_02_fetch_evidence(self):
        state = {
            "case_id": "CASE_FRAUD_TEST_002",
            "extracted_info": mock_extract(self.COMPLAINT),
        }
        result = fetch_evidence(state)
        evidence = result["evidence_bundle"]
        assert evidence.account_status is not None
        assert evidence.account_status.account_status == "locked"
        assert evidence.fraud_case is not None
        assert evidence.fraud_case.risk_score == 25

    def test_03_rule_decision(self):
        state = {
            "case_id": "CASE_FRAUD_TEST_002",
            "selected_workflow": "fraud_account_lock",
            "extracted_info": mock_extract(self.COMPLAINT),
        }
        state.update(fetch_evidence(state))
        rule_result = apply_rules(state)
        decision = rule_result["rule_decision"]
        assert decision["action"] == ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT.value
        assert decision["diagnosis"] == "likely_false_positive_unlock_candidate"
        assert decision["approval_required"] is True

    def test_04_recommendation_high_risk(self):
        state = {
            "case_id": "CASE_FRAUD_TEST_002",
            "selected_workflow": "fraud_account_lock",
            "extracted_info": mock_extract(self.COMPLAINT),
        }
        state.update(fetch_evidence(state))
        state.update(apply_rules(state))
        rec = recommend_action(state)
        assert rec["recommended_action"].action_type == ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT
        assert rec["recommended_action"].risk_level == "high"
        # approval_required is set by apply_rules, not recommend_action
        assert state["approval_required"] is True

    def test_05_draft_unlock_via_mcp(self):
        state = {
            "case_id": "CASE_FRAUD_TEST_002",
            "selected_workflow": "fraud_account_lock",
            "extracted_info": mock_extract(self.COMPLAINT),
            "user_id": "U_FRAUD_002",
        }
        state.update(fetch_evidence(state))
        state.update(apply_rules(state))
        state.update(recommend_action(state))

        draft_result = create_draft(state)
        assert draft_result["status"] == CaseStatus.DRAFT_CREATED
        assert draft_result["draft_output"]["type"] == "unlock_account_draft"
        assert "draft_id" in draft_result["draft_output"]
        assert "note" in draft_result["draft_output"]


# ═══════════════════════════════════════════════════════════════
#  MCP-first architecture checks
# ═══════════════════════════════════════════════════════════════


class TestMCPFirstArchitecture:
    """Verify that all data flows through MCP, not direct imports."""

    def test_fetch_evidence_no_direct_repo_import(self):
        """fetch_evidence.py must not import from database/repositories."""
        import inspect
        from fintech_agent.nodes import fetch_evidence as mod
        source = inspect.getsource(mod)
        assert "from fintech_agent.database" not in source, \
            "fetch_evidence imports directly from database — must go through MCP"

    def test_fetch_evidence_no_direct_tool_import(self):
        """fetch_evidence.py must not import from tools."""
        import inspect
        from fintech_agent.nodes import fetch_evidence as mod
        source = inspect.getsource(mod)
        assert "from fintech_agent.tools" not in source, \
            "fetch_evidence imports directly from tools — must go through MCP"

    def test_draft_action_no_direct_repo_import(self):
        """draft_action.py must not import from database/repositories."""
        import inspect
        from fintech_agent.nodes import draft_action as mod
        source = inspect.getsource(mod)
        assert "from fintech_agent.database" not in source, \
            "draft_action imports directly from database — must go through MCP"

    def test_mcp_client_has_fraud_handlers(self):
        """MCP client handler map includes all fraud tools."""
        mcp = get_mcp_client()
        handler_map = mcp._get_handler_map()
        assert "get_account_status" in handler_map
        assert "get_fraud_case" in handler_map
        assert "create_unlock_account_draft" in handler_map
        assert "create_request_documents_response_draft" in handler_map

    def test_no_dangerous_execution_in_production_code(self):
        """Scan production code for dangerous execution patterns."""
        import os
        dangerous_patterns = [
            "execute_unlock_account",
            "unlock_account_now",
            "update_account_status",
            "delete_fraud_case",
            "modify_risk_score",
        ]
        src_dir = os.path.join(os.path.dirname(__file__), "..", "..", "src", "fintech_agent")
        src_dir = os.path.abspath(src_dir)

        violations = []
        for root, _dirs, files in os.walk(src_dir):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path) as fh:
                    lines = fh.readlines()
                for i, line in enumerate(lines):
                    for pattern in dangerous_patterns:
                        if pattern in line:
                            stripped = line.strip()
                            # Skip: comments, docstrings, string literals in frozenset/blocklist
                            if stripped.startswith("#"):
                                continue
                            if stripped.startswith('"""') or stripped.startswith("'"):
                                continue
                            # Skip entries inside FORBIDDEN_ACTIONS frozenset definition
                            if stripped.startswith('"') and stripped.endswith(','):
                                # This is a string literal in a set/list — part of blocklist
                                continue
                            violations.append(f"{path}:{i+1}: {stripped}")

        assert not violations, f"Dangerous execution patterns found:\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════════
#  Non-regression: Case 1 (wallet_topup) still works
# ═══════════════════════════════════════════════════════════════


class TestWalletTopupNonRegression:
    """Verify that wallet_topup flow still works after fraud changes."""

    COMPLAINT = "Tôi nạp tiền từ ngân hàng vào ví, bank đã trừ tiền nhưng ví vẫn báo 0 đồng. Mã giao dịch TXN_TOPUP_001"

    def test_extraction_still_wallet_topup(self):
        result = mock_extract(self.COMPLAINT)
        assert result.service_type == "wallet_topup"
        assert result.issue_type == "topup_pending"

    def test_workflow_routing(self):
        state = {
            "case_id": "CASE_TOPUP_NONREG",
            "raw_complaint": self.COMPLAINT,
        }
        result = extract_info(state)
        assert result["selected_workflow"] == "wallet_topup"

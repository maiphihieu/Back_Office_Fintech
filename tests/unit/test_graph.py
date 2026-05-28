"""Unit tests for the LangGraph state, builder, and basic node execution.

Covers:
  1. Graph compiles successfully
  2. Graph has all expected nodes
  3. Graph has correct edges
  4. Individual nodes return valid state updates
  5. End-to-end graph invocation for TRAIN_001 scenario
"""

import pytest

from fintech_agent.graph.builder import build_graph, compile_graph
from fintech_agent.graph.state import AgentState
from fintech_agent.nodes.case_intake import case_intake
from fintech_agent.nodes.extract_info import extract_info
from fintech_agent.nodes.missing_info import missing_info_handler
from fintech_agent.nodes.conflict_detection import detect_conflict
from fintech_agent.nodes.workflow_router import route_workflow
from fintech_agent.nodes.approval_gate import approval_gate
from fintech_agent.nodes.audit_close import audit_and_close
from fintech_agent.nodes.retry_dead_letter import retry_or_dead_letter
from fintech_agent.schemas.enums import CaseStatus
from fintech_agent.schemas.evidence import EvidenceBundle


# ═══════════════════════════════════════════════════════════
#  1. Graph compilation
# ═══════════════════════════════════════════════════════════


class TestGraphCompilation:
    def test_graph_compiles(self) -> None:
        """The graph must compile without errors."""
        app = compile_graph()
        assert app is not None

    def test_graph_has_all_nodes(self) -> None:
        """Verify all 13 expected nodes are registered."""
        graph = build_graph()
        expected_nodes = {
            "case_intake",
            "extract_info",
            "missing_info_handler",
            "fetch_evidence",
            "detect_conflict",
            "route_workflow",
            "apply_rules",
            "recommend_action",
            "approval_gate",
            "create_draft",
            "audit_and_close",
            "manual_review",
            "retry_or_dead_letter",
        }
        actual_nodes = set(graph.nodes.keys())
        assert expected_nodes == actual_nodes, f"Missing: {expected_nodes - actual_nodes}"

    def test_graph_node_count(self) -> None:
        graph = build_graph()
        assert len(graph.nodes) == 13


# ═══════════════════════════════════════════════════════════
#  2. Individual node tests
# ═══════════════════════════════════════════════════════════


class TestCaseIntakeNode:
    def test_generates_case_id(self) -> None:
        result = case_intake({"raw_complaint": "test"})
        assert result["case_id"].startswith("CASE_")
        assert result["status"] == CaseStatus.EXTRACTING

    def test_preserves_existing_case_id(self) -> None:
        result = case_intake({"case_id": "CASE_EXISTING", "raw_complaint": "test"})
        assert result["case_id"] == "CASE_EXISTING"

    def test_initializes_retry_count(self) -> None:
        result = case_intake({"raw_complaint": "test"})
        assert result["retry_count"] == 0
        assert result["max_retries"] == 3

    def test_generates_correlation_id(self) -> None:
        result = case_intake({"raw_complaint": "test"})
        assert result["correlation_id"]
        assert len(result["correlation_id"]) == 12


class TestExtractInfoNode:
    def test_extracts_transaction_id(self) -> None:
        result = extract_info({"raw_complaint": "Giao dịch TXN_TRAIN_001 bị lỗi"})
        assert result["extracted_info"].transaction_id == "TXN_TRAIN_001"

    def test_extracts_user_id(self) -> None:
        result = extract_info({"raw_complaint": "User U001 gặp vấn đề"})
        assert result["user_id"] == "U001"

    def test_detects_train_service(self) -> None:
        result = extract_info({"raw_complaint": "Mua vé tàu TXN_001 nhưng chưa nhận được"})
        assert result["extracted_info"].service_type == "train_ticket"

    def test_detects_electric_service(self) -> None:
        result = extract_info({"raw_complaint": "Thanh toán tiền điện TXN_001 bị lỗi"})
        assert result["extracted_info"].service_type == "electric_bill"

    def test_missing_transaction_id_flagged(self) -> None:
        result = extract_info({"raw_complaint": "Tôi bị trừ tiền"})
        assert "transaction_id" in result["missing_info"]
        assert result["status"] == CaseStatus.MISSING_INFO

    def test_complete_info_goes_to_fetching(self) -> None:
        result = extract_info({
            "raw_complaint": "TXN_TRAIN_001 vé tàu bị lỗi",
            "user_id": "U001",
        })
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE


class TestMissingInfoNode:
    def test_missing_txn_id_dead_letter(self) -> None:
        result = missing_info_handler({"missing_info": ["transaction_id"]})
        assert result["status"] == CaseStatus.DEAD_LETTER

    def test_missing_service_type_proceeds(self) -> None:
        result = missing_info_handler({"missing_info": ["service_type"]})
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE


class TestConflictDetectionNode:
    def test_no_conflict(self) -> None:
        result = detect_conflict({"evidence_bundle": EvidenceBundle()})
        assert result["has_conflict"] is False
        assert result["status"] == CaseStatus.ROUTED

    def test_with_conflict(self) -> None:
        from fintech_agent.schemas.evidence import Transaction, WalletLedger
        evidence = EvidenceBundle(
            wallet_ledger=WalletLedger(
                transaction_id="TXN_001", user_id="U001",
                has_user_debit=True, debit_amount=100000,
            ),
            transaction=Transaction(
                transaction_id="TXN_001", user_id="U001",
                service_type="train_ticket", amount=100000, status="pending",
            ),
        )
        result = detect_conflict({"evidence_bundle": evidence, "user_id": "U001"})
        assert result["has_conflict"] is True
        assert result["status"] == CaseStatus.CONFLICT_DETECTED


class TestWorkflowRouterNode:
    def test_routes_train(self) -> None:
        result = route_workflow({"selected_workflow": "train_ticket"})
        assert result["status"] == CaseStatus.RULE_DECISION

    def test_routes_utility(self) -> None:
        result = route_workflow({"selected_workflow": "utility_bill"})
        assert result["status"] == CaseStatus.RULE_DECISION

    def test_unknown_goes_manual(self) -> None:
        result = route_workflow({"selected_workflow": None})
        assert result["status"] == CaseStatus.MANUAL_REVIEW


class TestApprovalGateNode:
    def test_no_approval_required(self) -> None:
        result = approval_gate({"approval_required": False})
        assert result["approval_status"] == "not_required"
        assert result["status"] == CaseStatus.DRAFT_CREATED

    def test_approval_required_sets_pending(self) -> None:
        """Approval gate must set PENDING (not auto-approve)."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        from fintech_agent.schemas.case_state import ExtractedInfo
        result = approval_gate({
            "approval_required": True,
            "case_id": "CASE_TEST",
            "user_id": "U001",
            "recommended_action": RecommendedAction(
                action_type=ActionType.CREATE_REFUND_REQUEST_DRAFT,
                diagnosis="test",
                summary="test",
                risk_level=RiskLevel.HIGH,
                approval_required=True,
            ),
            "rule_decision": {"action": "create_refund_request_draft", "diagnosis": "test"},
            "extracted_info": ExtractedInfo(transaction_id="TXN_001"),
        })
        assert result["approval_status"] == "pending"
        assert result["status"] == CaseStatus.WAITING_APPROVAL
        assert result["approval_packet"] is not None
        assert result["approval_packet"].case_id == "CASE_TEST"


class TestRetryNode:
    def test_retry_increments(self) -> None:
        result = retry_or_dead_letter({"retry_count": 0, "max_retries": 3})
        assert result["retry_count"] == 1
        assert result["status"] == CaseStatus.FETCHING_EVIDENCE

    def test_max_retries_dead_letter(self) -> None:
        result = retry_or_dead_letter({"retry_count": 3, "max_retries": 3})
        assert result["status"] == CaseStatus.DEAD_LETTER


class TestAuditCloseNode:
    def test_closes_case(self) -> None:
        result = audit_and_close({})
        assert result["status"] == CaseStatus.CLOSED

    def test_preserves_waiting_approval(self) -> None:
        """WAITING_APPROVAL should NOT be overwritten to CLOSED."""
        result = audit_and_close({"status": CaseStatus.WAITING_APPROVAL})
        assert result["status"] == CaseStatus.WAITING_APPROVAL


# ═══════════════════════════════════════════════════════════
#  3. Conditional edges
# ═══════════════════════════════════════════════════════════


class TestConditionalEdges:
    def test_after_extract_missing(self) -> None:
        from fintech_agent.graph.edges import after_extract_info
        assert after_extract_info({"missing_info": ["transaction_id"]}) == "missing_info_handler"

    def test_after_extract_complete(self) -> None:
        from fintech_agent.graph.edges import after_extract_info
        assert after_extract_info({"missing_info": []}) == "fetch_evidence"

    def test_after_missing_dead_letter(self) -> None:
        from fintech_agent.graph.edges import after_missing_info
        assert after_missing_info({"status": CaseStatus.DEAD_LETTER}) == "audit_and_close"

    def test_after_missing_proceed(self) -> None:
        from fintech_agent.graph.edges import after_missing_info
        assert after_missing_info({"status": CaseStatus.FETCHING_EVIDENCE}) == "fetch_evidence"

    def test_after_conflict_detected(self) -> None:
        from fintech_agent.graph.edges import after_detect_conflict
        assert after_detect_conflict({"has_conflict": True}) == "manual_review"

    def test_after_no_conflict(self) -> None:
        from fintech_agent.graph.edges import after_detect_conflict
        assert after_detect_conflict({"has_conflict": False}) == "route_workflow"

    def test_after_recommend_approval(self) -> None:
        from fintech_agent.graph.edges import after_recommend_action
        assert after_recommend_action({"status": CaseStatus.WAITING_APPROVAL}) == "approval_gate"

    def test_after_recommend_no_approval(self) -> None:
        from fintech_agent.graph.edges import after_recommend_action
        assert after_recommend_action({"status": CaseStatus.DRAFT_CREATED}) == "create_draft"

    def test_after_approval_approved(self) -> None:
        from fintech_agent.graph.edges import after_approval_gate
        assert after_approval_gate({"approval_status": "approved"}) == "create_draft"

    def test_after_approval_rejected(self) -> None:
        from fintech_agent.graph.edges import after_approval_gate
        assert after_approval_gate({"approval_status": "rejected"}) == "audit_and_close"

    def test_after_approval_pending(self) -> None:
        from fintech_agent.graph.edges import after_approval_gate
        assert after_approval_gate({"approval_status": "pending"}) == "audit_and_close"

    def test_after_retry_continues(self) -> None:
        from fintech_agent.graph.edges import after_retry
        assert after_retry({"status": CaseStatus.FETCHING_EVIDENCE}) == "fetch_evidence"

    def test_after_retry_dead_letter(self) -> None:
        from fintech_agent.graph.edges import after_retry
        assert after_retry({"status": CaseStatus.DEAD_LETTER}) == "audit_and_close"


# ═══════════════════════════════════════════════════════════
#  4. End-to-end graph invocation
# ═══════════════════════════════════════════════════════════


class TestEndToEndGraph:
    def test_train_001_stops_at_waiting_approval(self) -> None:
        """TRAIN_001: wallet debited + ticket not issued → WAITING_APPROVAL (no draft yet)."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": "Tôi mua vé tàu TXN_TRAIN_001 nhưng chưa nhận được vé",
            "user_id": "U001",
        })
        assert result["status"] == CaseStatus.WAITING_APPROVAL
        assert result["approval_status"] == "pending"
        assert result["approval_packet"] is not None
        assert result.get("draft_output") is None  # No draft before approval!

    def test_train_002_ticket_issued(self) -> None:
        """TRAIN_002: ticket issued → customer response."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": "Tôi mua vé tàu TXN_TRAIN_002 nhưng chưa nhận",
            "user_id": "U001",
        })
        assert result["status"] == CaseStatus.CLOSED
        assert result["draft_output"]["type"] == "customer_response_draft"

    def test_missing_transaction_id_dead_letter(self) -> None:
        """No transaction ID → dead letter."""
        app = compile_graph()
        result = app.invoke({
            "raw_complaint": "Tôi bị trừ tiền nhưng không nhớ mã giao dịch",
            "user_id": "U001",
        })
        assert result["status"] == CaseStatus.CLOSED
        assert "transaction_id missing" in str(result.get("errors", []))

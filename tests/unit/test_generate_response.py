"""Tests for the LLM response generator node and service.

Covers:
  1. LLM response valid — mock generate_case_response, verify output
  2. LLM unavailable — no API key → fallback response, graph doesn't fail
  3. Safety — draft/approval responses don't claim completion
  4. Dynamic — works across multiple workflows without hard-coding
  5. Decision fields are never modified by generate_response node
  6. Context builder — structured sections, evidence-first rule
  7. New fields — evidence_supporting_problem_location, problem_location_confidence
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from fintech_agent.llm.response_generator import (
    build_response_context,
    generate_case_response,
    generate_safe_fallback_response,
)
from fintech_agent.nodes.generate_response import generate_response
from fintech_agent.schemas.actions import RecommendedAction
from fintech_agent.schemas.enums import (
    ActionType,
    ApprovalStatus,
    CaseStatus,
    RiskLevel,
)
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    Transaction,
    WalletLedger,
)
from fintech_agent.schemas.response_generation import GeneratedResponse


# ─── Fixtures ───────────────────────────────────────────────


def _make_state(
    workflow: str = "wallet_topup",
    action: ActionType = ActionType.CREATE_REFUND_REQUEST_DRAFT,
    risk: RiskLevel = RiskLevel.LOW,
    approval_required: bool = False,
    status: CaseStatus = CaseStatus.DRAFT_CREATED,
) -> dict:
    """Build a minimal AgentState dict for testing."""
    return {
        "case_id": "CASE-TEST-001",
        "raw_complaint": "Tôi nạp 500,000đ nhưng ví chưa nhận được tiền.",
        "user_id": "user_123",
        "ticket_id": "TKT-001",
        "selected_workflow": workflow,
        "extracted_info": {
            "user_id": "user_123",
            "transaction_id": "TXN-001",
            "service_type": "wallet_topup",
            "amount_claimed": 500000,
        },
        "evidence_bundle": EvidenceBundle(
            transaction=Transaction(
                transaction_id="TXN-001",
                user_id="user_123",
                service_type="wallet_topup",
                amount=500000,
                status="failed",
            ),
            wallet_ledger=WalletLedger(
                transaction_id="TXN-001",
                user_id="user_123",
                has_user_debit=True,
                debit_amount=500000,
                has_credit_refund=False,
            ),
        ),
        "recommended_action": RecommendedAction(
            action_type=action,
            diagnosis="bank_confirmed_wallet_not_credited",
            summary=f"Rule engine recommends: {action.value}",
            risk_level=risk,
            approval_required=approval_required,
        ),
        "rule_decision": {
            "action": action.value,
            "diagnosis": "bank_confirmed_wallet_not_credited",
            "approval_required": approval_required,
        },
        "risk_level": risk,
        "approval_required": approval_required,
        "approval_status": (
            ApprovalStatus.PENDING if approval_required
            else ApprovalStatus.NOT_REQUIRED
        ),
        "status": status,
        "errors": [],
        "audit_event_ids": ["evt-1", "evt-2"],
    }


# ─── 1. LLM response valid ─────────────────────────────────


class TestGenerateResponseNode:
    """Test generate_response node output."""

    def test_node_returns_generated_response(self):
        """Node returns state with generated_response field."""
        state = _make_state()

        mock_response = GeneratedResponse(
            case_summary="Khách nạp 500k nhưng ví chưa cộng.",
            problem_location="wallet_system",
            problem_explanation="Ngân hàng xác nhận nhận tiền nhưng ví chưa ghi credit.",
            evidence_checked=["transaction", "wallet_ledger"],
            evidence_supporting_problem_location=[
                "transaction.status=failed",
                "wallet_ledger.has_user_debit=true",
                "wallet_ledger.has_credit_refund=false",
            ],
            problem_location_confidence="high",
            internal_summary="Bank received, wallet not credited. Refund draft recommended.",
            recommended_next_step="Xem xét và phê duyệt refund draft.",
            customer_reply_draft="Dạ em đã kiểm tra, tiền chưa vào ví. Bộ phận sẽ xử lý.",
            safety_notes=["Action là draft, chưa thực hiện."],
        )

        with patch(
            "fintech_agent.nodes.generate_response.generate_case_response",
            return_value=mock_response,
        ):
            result = generate_response(state)

        assert "generated_response" in result
        assert isinstance(result["generated_response"], GeneratedResponse)
        assert result["generated_response"].problem_location == "wallet_system"
        assert result["generated_response"].problem_location_confidence == "high"
        assert len(result["generated_response"].evidence_supporting_problem_location) == 3

    def test_node_preserves_decision_fields(self):
        """Node MUST NOT modify any decision fields."""
        state = _make_state(
            action=ActionType.CREATE_REFUND_REQUEST_DRAFT,
            risk=RiskLevel.MEDIUM,
            approval_required=True,
            status=CaseStatus.WAITING_APPROVAL,
        )

        with patch(
            "fintech_agent.nodes.generate_response.generate_case_response",
            return_value=generate_safe_fallback_response(state),
        ):
            result = generate_response(state)

        # Node returns ONLY generated_response and audit_event_ids
        assert "generated_response" in result
        assert "audit_event_ids" in result
        # Must NOT overwrite decision fields
        assert "recommended_action" not in result
        assert "risk_level" not in result
        assert "approval_required" not in result
        assert "status" not in result
        assert "selected_workflow" not in result
        assert "evidence_bundle" not in result
        assert "draft_output" not in result

    def test_node_appends_audit_ids(self):
        """Node preserves existing audit event IDs."""
        state = _make_state()
        state["audit_event_ids"] = ["evt-existing"]

        with patch(
            "fintech_agent.nodes.generate_response.generate_case_response",
            return_value=generate_safe_fallback_response(state),
        ):
            result = generate_response(state)

        assert "evt-existing" in result["audit_event_ids"]


# ─── 2. LLM unavailable → fallback ─────────────────────────


class TestFallbackResponse:
    """Test fallback behavior when LLM is unavailable."""

    def test_no_api_key_returns_fallback(self):
        """Without OPENAI_API_KEY, generate_case_response returns fallback."""
        state = _make_state()

        with patch.dict("os.environ", {}, clear=False):
            # Ensure no API key
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            result = generate_case_response(state)

        assert isinstance(result, GeneratedResponse)
        # DiagnosticEngine now provides evidence-driven location
        assert result.problem_location != ""
        assert "fallback" in result.internal_summary.lower() or "diagnostic" in result.internal_summary.lower()

    def test_fallback_has_required_fields(self):
        """Fallback response has all required fields populated."""
        state = _make_state()
        result = generate_safe_fallback_response(state)

        assert result.case_summary != ""
        assert result.problem_location != ""
        assert result.problem_explanation != ""
        assert result.internal_summary != ""
        assert result.recommended_next_step != ""
        assert result.customer_reply_draft != ""
        assert len(result.safety_notes) > 0

    def test_fallback_has_new_fields(self):
        """Fallback response includes new evidence-based fields."""
        state = _make_state()
        result = generate_safe_fallback_response(state)

        assert isinstance(result.evidence_supporting_problem_location, list)
        # DiagnosticEngine now populates evidence data points
        assert isinstance(result.missing_data, list)
        assert result.problem_location_confidence in ("high", "medium", "low", "unknown")

    def test_node_does_not_fail_on_llm_error(self):
        """The safe wrapper must catch LLM errors and return fallback."""
        state = _make_state()

        import os
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            # Patch openai module import to raise
            with patch.dict("sys.modules", {"openai": None}):
                from fintech_agent.llm.response_generator import generate_response_with_llm
                result = generate_response_with_llm(state)
                assert isinstance(result, GeneratedResponse)
                # DiagnosticEngine provides evidence-driven location
                assert result.problem_location != ""
        finally:
            os.environ.pop("OPENAI_API_KEY", None)


# ─── 3. Safety checks ──────────────────────────────────────


class TestSafetyConstraints:
    """Test that responses don't claim completion for draft/pending actions."""

    def test_fallback_safety_notes_mention_draft(self):
        """Fallback response warns about draft/approval actions."""
        state = _make_state(approval_required=True)
        result = generate_safe_fallback_response(state)

        notes_text = " ".join(result.safety_notes).lower()
        assert "phê duyệt" in notes_text or "draft" in notes_text

    def test_context_builder_filters_secrets(self):
        """Context builder must not include sensitive keys."""
        state = _make_state()
        state["openai_api_key"] = "sk-secret-123"
        state["supabase_key"] = "sb-secret-456"
        state["password"] = "hunter2"
        state["stack_trace"] = "Traceback..."

        context = build_response_context(state)

        assert "openai_api_key" not in context
        assert "supabase_key" not in context
        assert "password" not in context
        assert "stack_trace" not in context

    def test_context_builder_includes_safe_fields(self):
        """Context builder includes necessary fields for LLM."""
        state = _make_state()
        context = build_response_context(state)

        assert "raw_complaint" in context
        assert "selected_workflow" in context
        assert "rule_decision" in context

    def test_fallback_customer_draft_no_completion_claims(self):
        """Customer reply draft must not claim refund/unlock completed."""
        state = _make_state(approval_required=True)
        result = generate_safe_fallback_response(state)

        draft = result.customer_reply_draft.lower()
        forbidden = ["đã cộng tiền", "đã hoàn tiền", "đã mở khóa", "đã xử lý thành công"]
        for phrase in forbidden:
            assert phrase not in draft, f"Draft contains forbidden phrase: {phrase}"


# ─── 4. Dynamic — multiple workflows ───────────────────────


class TestDynamicWorkflows:
    """Test that node works across different workflows without hard-coding."""

    @pytest.mark.parametrize("workflow,action", [
        ("wallet_topup", ActionType.CREATE_REFUND_REQUEST_DRAFT),
        ("train_ticket", ActionType.CREATE_REFUND_REQUEST_DRAFT),
        ("wallet_topup", ActionType.CREATE_RECONCILIATION_TICKET_DRAFT),
        ("train_ticket", ActionType.MANUAL_REVIEW),
    ])
    def test_node_works_for_different_workflows(self, workflow, action):
        """generate_response node produces valid output for any workflow."""
        state = _make_state(workflow=workflow, action=action)

        with patch(
            "fintech_agent.nodes.generate_response.generate_case_response",
            return_value=generate_safe_fallback_response(state),
        ):
            result = generate_response(state)

        assert isinstance(result["generated_response"], GeneratedResponse)
        # Node doesn't know about specific workflows
        assert "recommended_action" not in result
        assert "selected_workflow" not in result

    def test_fallback_is_workflow_agnostic(self):
        """Fallback response doesn't mention specific workflow names."""
        for workflow in ["wallet_topup", "train_ticket", "utility_bill"]:
            state = _make_state(workflow=workflow)
            result = generate_safe_fallback_response(state)
            # Fallback text should be generic
            assert "wallet_topup" not in result.case_summary
            assert "train_ticket" not in result.case_summary


# ─── 5. Context builder — structured sections ──────────────


class TestContextStructure:
    """Test that context builder creates properly separated sections."""

    def test_context_has_structured_evidence_section(self):
        """Context must have structured_evidence as a separate top-level key."""
        state = _make_state()
        context = build_response_context(state)

        assert "structured_evidence" in context
        assert isinstance(context["structured_evidence"], dict)

    def test_context_has_rule_decision_section(self):
        """Context must have rule_decision as a separate top-level key."""
        state = _make_state()
        context = build_response_context(state)

        assert "rule_decision" in context
        assert isinstance(context["rule_decision"], dict)
        assert "recommended_action" in context["rule_decision"]

    def test_context_evidence_not_mixed_with_complaint(self):
        """raw_complaint must NOT appear inside structured_evidence."""
        state = _make_state()
        context = build_response_context(state)

        # Complaint text
        complaint = state["raw_complaint"]
        evidence = context["structured_evidence"]

        # Evidence section must not contain the complaint text
        import json
        evidence_str = json.dumps(evidence, ensure_ascii=False, default=str)
        assert complaint not in evidence_str

    def test_context_rule_decision_has_diagnosis(self):
        """Rule decision section must include diagnosis from RecommendedAction."""
        state = _make_state()
        context = build_response_context(state)

        rule = context["rule_decision"]
        assert "diagnosis" in rule
        assert rule["diagnosis"] == "bank_confirmed_wallet_not_credited"

    def test_context_evidence_has_transaction(self):
        """Structured evidence must include transaction data from evidence_bundle."""
        state = _make_state()
        context = build_response_context(state)

        evidence = context["structured_evidence"]
        assert "transaction" in evidence

    def test_context_evidence_has_wallet_ledger(self):
        """Structured evidence must include wallet_ledger data from evidence_bundle."""
        state = _make_state()
        context = build_response_context(state)

        evidence = context["structured_evidence"]
        assert "wallet_ledger" in evidence

    def test_context_no_flat_evidence_keys(self):
        """Top-level context must NOT have flat evidence keys (transaction, wallet_ledger).
        Evidence must be nested inside structured_evidence."""
        state = _make_state()
        context = build_response_context(state)

        # These should be inside structured_evidence, not at top level
        assert "transaction" not in context
        assert "wallet_ledger" not in context
        assert "provider_status" not in context

    def test_context_no_flat_action_keys(self):
        """Top-level context must NOT have flat rule decision keys.
        They must be nested inside rule_decision."""
        state = _make_state()
        context = build_response_context(state)

        # These should be inside rule_decision, not at top level
        assert "recommended_action" not in context
        assert "risk_level" not in context
        assert "approval_required" not in context
        assert "diagnosis" not in context

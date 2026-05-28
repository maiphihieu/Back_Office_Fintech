"""Integration tests — end-to-end workflow scenarios with proper approval flow.

Two-phase approval workflow:
  Phase 1: Graph runs → stops at WAITING_APPROVAL (no draft created)
  Phase 2: ApprovalService.approve_case() → creates draft → closes case

Tests:
  TRAIN_001: refund → approval required → pauses → approve → draft created
  TRAIN_002: customer response → no approval → draft created immediately
  TRAIN_003: reconciliation → no approval → draft created immediately
  BILL_002:  not_confirmed → reconciliation (NOT refund!) → no approval
  BILL_003:  failed → refund → approval required → pauses → approve → draft
  CONFLICT_001: conflict → manual review (no approval flow)
  REFUND_001:  duplicate prevention → no_action
  APPROVAL: approve, reject, double-approve, unknown case
"""

import pytest

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.builder import compile_graph
from fintech_agent.schemas.enums import ApprovalStatus, AuditEventType, CaseStatus
from fintech_agent.workflows import (
    AlreadyDecidedError,
    ApprovalService,
    CaseNotFoundError,
)


# ═══════════════════════════════════════════════════════════
#  Helper: run Phase 1 and optionally Phase 2
# ═══════════════════════════════════════════════════════════

def _run_phase1(complaint: str, user_id: str, audit: AuditLogger):
    """Run graph Phase 1 and return result."""
    app = compile_graph(audit=audit)
    return app.invoke({"raw_complaint": complaint, "user_id": user_id})


def _run_full(complaint: str, user_id: str, audit: AuditLogger, approver: str = "ops_admin"):
    """Run Phase 1 + Phase 2 (approve) for cases that need approval."""
    result = _run_phase1(complaint, user_id, audit)
    if result.get("status") == CaseStatus.WAITING_APPROVAL:
        service = ApprovalService(audit=audit)
        service.register_pending(result)
        result = service.approve_case(result["case_id"], approver)
    return result


# ═══════════════════════════════════════════════════════════
#  1. Train ticket workflows
# ═══════════════════════════════════════════════════════════

class TestTrainTicketWorkflows:

    def test_train_001_phase1_pauses_at_approval(self) -> None:
        """TRAIN_001 Phase 1: stops at WAITING_APPROVAL with ApprovalPacket."""
        audit = AuditLogger()
        result = _run_phase1(
            "Tôi mua vé tàu TXN_TRAIN_001 nhưng chưa nhận. User U001",
            "U001", audit,
        )

        assert result["status"] == CaseStatus.WAITING_APPROVAL
        assert result["approval_status"] == ApprovalStatus.PENDING
        assert result["approval_packet"] is not None
        assert result["approval_packet"].case_id == result["case_id"]
        assert result["approval_packet"].amount == 450000
        assert result["approval_packet"].proposed_action == "create_refund_request_draft"
        assert result.get("draft_output") is None  # NO draft before approval

    def test_train_001_full_with_approval(self) -> None:
        """TRAIN_001 Phase 1 + 2: approve → refund draft created → closed."""
        audit = AuditLogger()
        result = _run_full(
            "Tôi mua vé tàu TXN_TRAIN_001 nhưng chưa nhận. User U001",
            "U001", audit,
        )

        assert result["status"] == CaseStatus.CLOSED
        assert result["draft_output"]["type"] == "refund_request_draft"
        assert result["draft_output"]["amount"] == 450000
        assert result["approval_status"] == ApprovalStatus.APPROVED

        # Audit trail
        events = audit.get_events_by_case(result["case_id"])
        event_types = {e.event_type for e in events}
        assert AuditEventType.CASE_RECEIVED in event_types
        assert AuditEventType.APPROVAL_REQUESTED in event_types
        assert AuditEventType.HUMAN_APPROVED in event_types
        assert AuditEventType.DRAFT_CREATED in event_types
        assert AuditEventType.CASE_CLOSED in event_types

    def test_train_002_no_approval_needed(self) -> None:
        """TRAIN_002: ticket issued → customer response (no approval)."""
        audit = AuditLogger()
        result = _run_phase1(
            "Tôi mua vé tàu TXN_TRAIN_002 nhưng chưa nhận",
            "U001", audit,
        )

        assert result["status"] == CaseStatus.CLOSED
        assert result["draft_output"]["type"] == "customer_response_draft"
        assert result["rule_decision"]["approval_required"] is False

    def test_train_003_reconciliation_no_approval(self) -> None:
        """TRAIN_003: provider_no_record → reconciliation (no approval)."""
        audit = AuditLogger()
        result = _run_phase1(
            "Giao dịch TXN_TRAIN_003 mua vé tàu bị lỗi",
            "U002", audit,
        )

        assert result["status"] == CaseStatus.CLOSED
        assert result["draft_output"]["type"] == "reconciliation_ticket_draft"
        assert result["rule_decision"]["approval_required"] is False


# ═══════════════════════════════════════════════════════════
#  2. Utility bill workflows
# ═══════════════════════════════════════════════════════════

class TestUtilityBillWorkflows:

    def test_bill_002_not_confirmed_reconciliation(self) -> None:
        """BILL_002: not_confirmed → reconciliation (NOT refund!)."""
        audit = AuditLogger()
        result = _run_phase1(
            "Thanh toán tiền điện TXN_BILL_002 nhưng chưa xác nhận",
            "U004", audit,
        )

        assert result["status"] == CaseStatus.CLOSED
        assert result["draft_output"]["type"] == "reconciliation_ticket_draft"
        assert result["draft_output"]["type"] != "refund_request_draft"
        assert "not_confirmed" in result["rule_decision"]["diagnosis"]

    def test_bill_003_failed_needs_approval(self) -> None:
        """BILL_003 Phase 1: provider failed → refund → WAITING_APPROVAL."""
        audit = AuditLogger()
        result = _run_phase1(
            "Thanh toán tiền nước TXN_BILL_003 bị lỗi",
            "U005", audit,
        )

        assert result["status"] == CaseStatus.WAITING_APPROVAL
        assert result["approval_packet"] is not None
        assert result["approval_packet"].amount == 310000

    def test_bill_003_full_with_approval(self) -> None:
        """BILL_003 Phase 1 + 2: approve → refund draft → closed."""
        audit = AuditLogger()
        result = _run_full(
            "Thanh toán tiền nước TXN_BILL_003 bị lỗi",
            "U005", audit,
        )

        assert result["status"] == CaseStatus.CLOSED
        assert result["draft_output"]["type"] == "refund_request_draft"
        assert result["draft_output"]["amount"] == 310000


# ═══════════════════════════════════════════════════════════
#  3. Conflict and safety
# ═══════════════════════════════════════════════════════════

class TestConflictAndSafety:

    def test_conflict_001_manual_review(self) -> None:
        """CONFLICT_001: ledger debited + txn pending → conflict → manual review."""
        audit = AuditLogger()
        result = _run_phase1(
            "Giao dịch TXN_CONFLICT_001 bị lỗi vé tàu",
            "U006", audit,
        )

        assert result["status"] == CaseStatus.CLOSED
        assert result["has_conflict"] is True
        assert result["draft_output"]["type"] == "manual_review"

        events = audit.get_events_by_case(result["case_id"])
        event_types = {e.event_type for e in events}
        assert AuditEventType.CONFLICT_DETECTED in event_types

    def test_refund_001_no_duplicate(self) -> None:
        """REFUND_001: refund already executed → no_action."""
        audit = AuditLogger()
        result = _run_phase1(
            "Giao dịch TXN_REFUND_001 mua vé tàu bị lỗi",
            "U007", audit,
        )

        assert result["status"] == CaseStatus.CLOSED
        assert result["draft_output"]["type"] == "no_action"
        assert "refund_not_eligible" in result["rule_decision"]["diagnosis"]


# ═══════════════════════════════════════════════════════════
#  4. Approval service operations
# ═══════════════════════════════════════════════════════════

class TestApprovalService:

    def _get_pending_state(self, audit: AuditLogger) -> dict:
        """Helper: run TRAIN_001 to WAITING_APPROVAL."""
        return _run_phase1(
            "Tôi mua vé tàu TXN_TRAIN_001 chưa nhận. U001",
            "U001", audit,
        )

    def test_register_pending(self) -> None:
        """Can register a WAITING_APPROVAL state."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)

        case_id = service.register_pending(state)
        assert service.is_pending(case_id)
        assert service.get_pending_cases() == [case_id]

    def test_approve_creates_draft(self) -> None:
        """approve_case → draft created + CLOSED."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)
        service.register_pending(state)
        case_id = state["case_id"]

        final = service.approve_case(case_id, "ops_admin", "Looks correct")

        assert final["status"] == CaseStatus.CLOSED
        assert final["draft_output"]["type"] == "refund_request_draft"
        assert final["approval_status"] == ApprovalStatus.APPROVED
        assert final["approval_decision"].approver == "ops_admin"
        assert final["approval_decision"].comment == "Looks correct"
        assert not service.is_pending(case_id)
        assert service.is_decided(case_id)

    def test_reject_no_draft(self) -> None:
        """reject_case → no draft + CLOSED."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)
        service.register_pending(state)
        case_id = state["case_id"]

        final = service.reject_case(case_id, "ops_admin", "Insufficient evidence")

        assert final["status"] == CaseStatus.CLOSED
        assert final["draft_output"]["type"] == "rejected"
        assert final["draft_output"]["reason"] == "Insufficient evidence"
        assert final["approval_status"] == ApprovalStatus.REJECTED
        assert not service.is_pending(case_id)
        assert service.is_decided(case_id)

    def test_reject_audit_trail(self) -> None:
        """Rejected case must have HUMAN_REJECTED audit event."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)
        service.register_pending(state)
        case_id = state["case_id"]

        service.reject_case(case_id, "reviewer_X", "Risk too high")

        events = audit.get_events_by_case(case_id)
        event_types = {e.event_type for e in events}
        assert AuditEventType.HUMAN_REJECTED in event_types
        rejected = [e for e in events if e.event_type == AuditEventType.HUMAN_REJECTED]
        assert "reviewer_X" in rejected[0].actor

    def test_cannot_approve_unknown_case(self) -> None:
        """Approving an unknown case_id raises CaseNotFoundError."""
        service = ApprovalService()
        with pytest.raises(CaseNotFoundError):
            service.approve_case("CASE_NONEXISTENT", "admin")

    def test_cannot_reject_unknown_case(self) -> None:
        """Rejecting an unknown case_id raises CaseNotFoundError."""
        service = ApprovalService()
        with pytest.raises(CaseNotFoundError):
            service.reject_case("CASE_NONEXISTENT", "admin", "no")

    def test_cannot_approve_twice(self) -> None:
        """Double-approving raises AlreadyDecidedError."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)
        service.register_pending(state)
        case_id = state["case_id"]

        service.approve_case(case_id, "admin_1")

        with pytest.raises(AlreadyDecidedError):
            service.approve_case(case_id, "admin_2")

    def test_cannot_reject_after_approve(self) -> None:
        """Rejecting after approval raises AlreadyDecidedError."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)
        service.register_pending(state)
        case_id = state["case_id"]

        service.approve_case(case_id, "admin_1")

        with pytest.raises(AlreadyDecidedError):
            service.reject_case(case_id, "admin_2", "too late")

    def test_cannot_register_non_waiting_state(self) -> None:
        """Register rejects states not in WAITING_APPROVAL."""
        service = ApprovalService()
        with pytest.raises(ValueError, match="WAITING_APPROVAL"):
            service.register_pending({"case_id": "CASE_X", "status": CaseStatus.CLOSED})

    def test_get_approval_packet(self) -> None:
        """Can retrieve ApprovalPacket for pending case."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)
        service.register_pending(state)
        case_id = state["case_id"]

        packet = service.get_approval_packet(case_id)
        assert packet is not None
        assert packet.case_id == case_id
        assert packet.amount == 450000
        assert packet.risk_level == "medium"  # 450k < 2M threshold → medium

    def test_approve_audit_trail(self) -> None:
        """Approved case must have full audit trail."""
        audit = AuditLogger()
        state = self._get_pending_state(audit)
        service = ApprovalService(audit=audit)
        service.register_pending(state)
        case_id = state["case_id"]

        service.approve_case(case_id, "ops_admin")

        events = audit.get_events_by_case(case_id)
        event_types = {e.event_type for e in events}
        assert AuditEventType.APPROVAL_REQUESTED in event_types
        assert AuditEventType.HUMAN_APPROVED in event_types
        assert AuditEventType.DRAFT_CREATED in event_types
        assert AuditEventType.CASE_CLOSED in event_types


# ═══════════════════════════════════════════════════════════
#  5. Dead letter
# ═══════════════════════════════════════════════════════════

class TestDeadLetter:

    def test_missing_transaction_id(self) -> None:
        audit = AuditLogger()
        result = _run_phase1(
            "Tôi bị trừ tiền nhưng không nhớ mã giao dịch",
            "U001", audit,
        )
        assert result["status"] == CaseStatus.CLOSED
        assert "transaction_id missing" in str(result.get("errors", []))


# ═══════════════════════════════════════════════════════════
#  6. Audit trail completeness
# ═══════════════════════════════════════════════════════════

class TestAuditTrail:

    def test_no_approval_case_has_complete_trail(self) -> None:
        """TRAIN_002 (no approval) has complete audit trail."""
        audit = AuditLogger()
        result = _run_phase1(
            "TXN_TRAIN_002 vé tàu",
            "U001", audit,
        )

        events = audit.get_events_by_case(result["case_id"])
        assert len(events) >= 6
        event_types = {e.event_type for e in events}
        assert AuditEventType.CASE_RECEIVED in event_types
        assert AuditEventType.INFO_EXTRACTED in event_types
        assert AuditEventType.RULE_APPLIED in event_types
        assert AuditEventType.DRAFT_CREATED in event_types
        assert AuditEventType.CASE_CLOSED in event_types

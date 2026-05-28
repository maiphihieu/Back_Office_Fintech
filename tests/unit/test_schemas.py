"""Unit tests for schema validation rules.

Tests are grouped by:
  1. Enum safety (no execute_refund)
  2. Evidence field validation (amount >= 0, IDs non-empty)
  3. Action drafts (idempotency_key required, evidence non-empty)
  4. Approval (refund must have evidence, no model_confidence)
  5. CaseState (state transitions, reopen cap)
"""

import pytest
from pydantic import ValidationError

from fintech_agent.schemas import (
    ActionType,
    ApprovalDecision,
    ApprovalPacket,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    CaseState,
    CaseStatus,
    EvidenceBundle,
    EvidenceConflict,
    ExtractedInfo,
    IssueType,
    ReconciliationTicketDraft,
    RecommendedAction,
    RefundRequestDraft,
    RefundStatus,
    RiskLevel,
    ServiceType,
    TrainProviderStatus,
    Transaction,
    UtilityProviderStatus,
    WalletLedger,
    WalletLedgerEntry,
)


# ════════════════════════════════════════════════════════════
#  1. Enum safety
# ════════════════════════════════════════════════════════════


class TestEnums:
    """Verify enum definitions and safety constraints."""

    def test_no_execute_refund_in_action_type(self) -> None:
        """execute_refund must NEVER exist as a valid action."""
        action_values = [a.value for a in ActionType]
        assert "execute_refund" not in action_values

    def test_service_type_values(self) -> None:
        assert ServiceType.TRAIN_TICKET == "train_ticket"
        assert ServiceType.ELECTRIC_BILL == "electric_bill"
        assert ServiceType.WATER_BILL == "water_bill"
        assert ServiceType.UNKNOWN == "unknown"

    def test_issue_type_values(self) -> None:
        assert IssueType.PAID_BUT_NO_TICKET == "paid_but_no_ticket"
        assert IssueType.PROVIDER_FAILED == "provider_failed"

    def test_case_status_has_all_required_states(self) -> None:
        required = {
            "new", "extracting", "missing_info", "fetching_evidence",
            "conflict_detected", "routed", "rule_decision", "waiting_approval",
            "draft_created", "manual_review", "dead_letter", "closed",
        }
        actual = {s.value for s in CaseStatus}
        assert required.issubset(actual)

    def test_risk_level_ordering(self) -> None:
        levels = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        assert len(levels) == 4


# ════════════════════════════════════════════════════════════
#  2. Evidence field validation
# ════════════════════════════════════════════════════════════


class TestTransaction:
    """Transaction model validation."""

    def test_valid_transaction(self) -> None:
        txn = Transaction(
            transaction_id="TXN_001",
            user_id="U001",
            service_type="train_ticket",
            amount=450000,
            status="completed",
        )
        assert txn.amount == 450000

    def test_amount_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            Transaction(
                transaction_id="TXN_001",
                user_id="U001",
                service_type="train_ticket",
                amount=-100,
                status="completed",
            )

    def test_transaction_id_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            Transaction(
                transaction_id="",
                user_id="U001",
                service_type="train_ticket",
                amount=100,
                status="completed",
            )

    def test_user_id_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            Transaction(
                transaction_id="TXN_001",
                user_id="",
                service_type="train_ticket",
                amount=100,
                status="completed",
            )


class TestWalletLedger:
    """WalletLedger validation."""

    def test_valid_ledger(self) -> None:
        ledger = WalletLedger(
            transaction_id="TXN_001",
            user_id="U001",
            has_user_debit=True,
            debit_amount=450000,
        )
        assert ledger.debit_amount == 450000
        assert ledger.has_credit_refund is False

    def test_debit_amount_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            WalletLedger(
                transaction_id="TXN_001",
                user_id="U001",
                debit_amount=-1,
            )

    def test_ledger_entry(self) -> None:
        entry = WalletLedgerEntry(entry_type="debit", amount=450000, balance_after=550000)
        assert entry.amount == 450000

    def test_entry_amount_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            WalletLedgerEntry(entry_type="debit", amount=-1)


class TestProviderStatus:
    """Provider status models."""

    def test_train_provider_valid(self) -> None:
        tp = TrainProviderStatus(
            provider_ref_id="REF_001",
            booking_status="ticket_issued",
            ticket_code="PNR_ABC123",
        )
        assert tp.ticket_code == "PNR_ABC123"

    def test_train_provider_ref_id_required(self) -> None:
        with pytest.raises(ValidationError):
            TrainProviderStatus(provider_ref_id="")

    def test_utility_provider_valid(self) -> None:
        up = UtilityProviderStatus(
            provider_ref_id="EVN_001",
            provider_status="confirmed",
            bill_status="paid",
        )
        assert up.provider_status == "confirmed"

    def test_utility_provider_amount_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            UtilityProviderStatus(
                provider_ref_id="EVN_001",
                amount=-500,
            )


class TestRefundStatus:
    """RefundStatus validation."""

    def test_valid_refund(self) -> None:
        rs = RefundStatus(
            transaction_id="TXN_001",
            refund_status="executed",
            refund_amount=450000,
        )
        assert rs.refund_amount == 450000

    def test_refund_amount_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            RefundStatus(transaction_id="TXN_001", refund_amount=-1)


class TestEvidenceBundle:
    """EvidenceBundle properties."""

    def test_empty_bundle(self) -> None:
        eb = EvidenceBundle()
        assert eb.has_conflicts is False
        assert eb.has_critical_failures is False

    def test_has_conflicts(self) -> None:
        eb = EvidenceBundle(
            conflicts=[
                EvidenceConflict(
                    source_a="wallet_ledger",
                    source_b="provider",
                    field="status",
                    value_a="debited",
                    value_b="no_record",
                    description="Wallet debited but provider has no record",
                )
            ]
        )
        assert eb.has_conflicts is True

    def test_critical_failure_wallet_ledger(self) -> None:
        eb = EvidenceBundle(tool_errors=["get_wallet_ledger"])
        assert eb.has_critical_failures is True

    def test_critical_failure_transaction(self) -> None:
        eb = EvidenceBundle(tool_errors=["get_transaction"])
        assert eb.has_critical_failures is True

    def test_non_critical_failure(self) -> None:
        eb = EvidenceBundle(tool_errors=["get_train_provider_status"])
        assert eb.has_critical_failures is False


# ════════════════════════════════════════════════════════════
#  3. Action drafts
# ════════════════════════════════════════════════════════════


class TestRefundRequestDraft:
    """RefundRequestDraft validation."""

    def test_valid_draft(self) -> None:
        draft = RefundRequestDraft(
            idempotency_key="abc123hash",
            case_id="CASE_001",
            transaction_id="TXN_001",
            user_id="U001",
            amount=450000,
            reason="Wallet debited, ticket not issued",
            evidence_summary=["wallet_ledger: debit 450000", "provider: ticket_not_issued"],
        )
        assert draft.amount == 450000

    def test_idempotency_key_required(self) -> None:
        with pytest.raises(ValidationError):
            RefundRequestDraft(
                idempotency_key="",
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=450000,
                reason="test",
                evidence_summary=["evidence"],
            )

    def test_evidence_summary_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            RefundRequestDraft(
                idempotency_key="abc123",
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=450000,
                reason="test",
                evidence_summary=[],
            )

    def test_amount_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            RefundRequestDraft(
                idempotency_key="abc123",
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=-1,
                reason="test",
                evidence_summary=["evidence"],
            )


class TestReconciliationTicketDraft:
    """ReconciliationTicketDraft validation."""

    def test_valid_draft(self) -> None:
        draft = ReconciliationTicketDraft(
            idempotency_key="xyz789hash",
            case_id="CASE_002",
            transaction_id="TXN_002",
            user_id="U002",
            mismatch_type="wallet_provider_mismatch",
            evidence_summary=["wallet debited", "provider not confirmed"],
        )
        assert draft.mismatch_type == "wallet_provider_mismatch"

    def test_idempotency_key_required(self) -> None:
        with pytest.raises(ValidationError):
            ReconciliationTicketDraft(
                idempotency_key="",
                case_id="CASE_002",
                transaction_id="TXN_002",
                user_id="U002",
                mismatch_type="test",
                evidence_summary=["evidence"],
            )


# ════════════════════════════════════════════════════════════
#  4. Approval
# ════════════════════════════════════════════════════════════


class TestApprovalPacket:
    """ApprovalPacket validation."""

    def test_valid_packet(self) -> None:
        pkt = ApprovalPacket(
            case_id="CASE_001",
            proposed_action=ActionType.CREATE_REFUND_REQUEST_DRAFT,
            amount=450000,
            transaction_id="TXN_001",
            user_id="U001",
            reason="Wallet debited, ticket not issued",
            evidence_summary=["ledger: debit 450K", "provider: ticket_not_issued"],
            risk_level=RiskLevel.MEDIUM,
        )
        assert pkt.requires_approval is True

    def test_refund_action_without_evidence_raises(self) -> None:
        """Refund approval MUST have non-empty evidence_summary."""
        with pytest.raises(ValidationError, match="evidence_summary must not be empty"):
            ApprovalPacket(
                case_id="CASE_001",
                proposed_action=ActionType.CREATE_REFUND_REQUEST_DRAFT,
                amount=450000,
                transaction_id="TXN_001",
                user_id="U001",
                reason="test",
                evidence_summary=[],
                risk_level=RiskLevel.LOW,
            )

    def test_non_refund_action_can_have_empty_evidence(self) -> None:
        """Non-refund actions don't require evidence in the packet."""
        pkt = ApprovalPacket(
            case_id="CASE_001",
            proposed_action=ActionType.MANUAL_REVIEW,
            amount=0,
            transaction_id="TXN_001",
            user_id="U001",
            reason="Conflict detected",
            evidence_summary=[],
            risk_level=RiskLevel.HIGH,
        )
        assert pkt.proposed_action == ActionType.MANUAL_REVIEW

    def test_no_model_confidence_field(self) -> None:
        """ApprovalPacket must NOT have a model_confidence field."""
        assert not hasattr(ApprovalPacket, "model_confidence")
        fields = ApprovalPacket.model_fields
        assert "model_confidence" not in fields


class TestApprovalDecision:
    """ApprovalDecision validation."""

    def test_valid_decision(self) -> None:
        dec = ApprovalDecision(
            case_id="CASE_001",
            approver="ops_senior_nguyen",
            status=ApprovalStatus.APPROVED,
            comment="Evidence clear, approve refund.",
        )
        assert dec.status == ApprovalStatus.APPROVED


# ════════════════════════════════════════════════════════════
#  5. CaseState
# ════════════════════════════════════════════════════════════


class TestCaseState:
    """CaseState validation and behavior."""

    def _make_case(self, **overrides) -> CaseState:
        defaults = {"case_id": "CASE_001", "ticket_id": "TICKET_001"}
        defaults.update(overrides)
        return CaseState(**defaults)

    def test_default_state_is_new(self) -> None:
        case = self._make_case()
        assert case.current_state == CaseStatus.NEW
        assert case.previous_state is None

    def test_state_transition(self) -> None:
        case = self._make_case()
        case.transition_to(CaseStatus.EXTRACTING)
        assert case.current_state == CaseStatus.EXTRACTING
        assert case.previous_state == CaseStatus.NEW

    def test_multiple_transitions(self) -> None:
        case = self._make_case()
        case.transition_to(CaseStatus.EXTRACTING)
        case.transition_to(CaseStatus.FETCHING_EVIDENCE)
        assert case.current_state == CaseStatus.FETCHING_EVIDENCE
        assert case.previous_state == CaseStatus.EXTRACTING

    def test_can_reopen_when_closed(self) -> None:
        case = self._make_case(current_state=CaseStatus.CLOSED, reopen_count=0)
        assert case.can_reopen is True

    def test_cannot_reopen_at_max(self) -> None:
        case = self._make_case(
            current_state=CaseStatus.CLOSED, reopen_count=3, max_reopen=3
        )
        assert case.can_reopen is False

    def test_cannot_reopen_if_not_closed(self) -> None:
        case = self._make_case(current_state=CaseStatus.EXTRACTING, reopen_count=0)
        assert case.can_reopen is False

    def test_case_id_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            CaseState(case_id="", ticket_id="TICKET_001")

    def test_ticket_id_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            CaseState(case_id="CASE_001", ticket_id="")

    def test_default_evidence_bundle(self) -> None:
        case = self._make_case()
        assert case.evidence.has_conflicts is False

    def test_reopen_count_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            self._make_case(reopen_count=-1)


class TestExtractedInfo:
    """ExtractedInfo validation."""

    def test_default_all_none(self) -> None:
        info = ExtractedInfo()
        assert info.user_id is None
        assert info.transaction_id is None
        assert info.service_type is None

    def test_with_values(self) -> None:
        info = ExtractedInfo(
            user_id="U001",
            transaction_id="TXN_001",
            service_type=ServiceType.TRAIN_TICKET,
            issue_type=IssueType.PAID_BUT_NO_TICKET,
        )
        assert info.service_type == ServiceType.TRAIN_TICKET


# ════════════════════════════════════════════════════════════
#  6. Audit
# ════════════════════════════════════════════════════════════


class TestAuditEvent:
    """AuditEvent validation."""

    def test_valid_event(self) -> None:
        evt = AuditEvent(
            case_id="CASE_001",
            actor="agent",
            event_type=AuditEventType.CASE_RECEIVED,
            details={"raw_complaint": "Khách mua vé tàu..."},
        )
        assert evt.event_id  # auto-generated
        assert evt.timestamp  # auto-generated
        assert evt.actor == "agent"

    def test_case_id_required(self) -> None:
        with pytest.raises(ValidationError):
            AuditEvent(
                case_id="",
                actor="agent",
                event_type=AuditEventType.CASE_RECEIVED,
            )

    def test_actor_required(self) -> None:
        with pytest.raises(ValidationError):
            AuditEvent(
                case_id="CASE_001",
                actor="",
                event_type=AuditEventType.CASE_RECEIVED,
            )


# ════════════════════════════════════════════════════════════
#  7. RecommendedAction
# ════════════════════════════════════════════════════════════


class TestRecommendedAction:
    """RecommendedAction validation."""

    def test_valid_recommendation(self) -> None:
        rec = RecommendedAction(
            action_type=ActionType.CREATE_REFUND_REQUEST_DRAFT,
            diagnosis="wallet_debited_ticket_not_issued",
            summary="Ví đã trừ 450K, vé chưa phát hành, đề xuất tạo refund draft.",
            risk_level=RiskLevel.MEDIUM,
            evidence_refs=["wallet_ledger", "train_provider"],
            approval_required=True,
        )
        assert rec.approval_required is True
        assert rec.action_type != "execute_refund"

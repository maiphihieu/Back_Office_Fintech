"""Unit tests for rule engine.

Covers:
  1. Conflict detection (4 conflict types)
  2. Refund eligibility (5 check functions)
  3. Train ticket decisions (6 scenarios)
  4. Utility bill decisions (7 scenarios)
  5. Risk classification
  6. Idempotency
"""

import pytest

from fintech_agent.rules.conflict_rules import detect_all_conflicts
from fintech_agent.rules.idempotency_rules import (
    generate_idempotency_key,
    is_duplicate_action,
)
from fintech_agent.rules.refund_rules import (
    RefundEligibility,
    check_no_conflicts,
    check_no_existing_refund,
    check_provider_failed,
    check_wallet_debited,
    full_refund_eligibility_check,
)
from fintech_agent.rules.risk_rules import classify_risk, requires_approval
from fintech_agent.rules.train_ticket_rules import decide_train_ticket
from fintech_agent.rules.utility_bill_rules import decide_utility_bill
from fintech_agent.schemas.enums import (
    ActionType,
    ProviderStatusValue,
    RefundStatusValue,
    RiskLevel,
)
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    EvidenceConflict,
    RefundStatus,
    TrainProviderStatus,
    Transaction,
    UtilityProviderStatus,
    WalletLedger,
)


# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════


def _ledger(debited: bool = True, amount: int = 450000, refunded: bool = False) -> WalletLedger:
    return WalletLedger(
        transaction_id="TXN_001",
        user_id="U001",
        has_user_debit=debited,
        debit_amount=amount if debited else 0,
        has_credit_refund=refunded,
        credit_refund_amount=amount if refunded else 0,
        net_amount=0 if refunded else amount,
    )


def _txn(status: str = "completed", user_id: str = "U001") -> Transaction:
    return Transaction(
        transaction_id="TXN_001",
        user_id=user_id,
        service_type="train_ticket",
        amount=450000,
        status=status,
    )


def _train_provider(
    status: str = "ticket_not_issued", ticket_code: str | None = None
) -> TrainProviderStatus:
    return TrainProviderStatus(
        provider_ref_id="REF_001",
        booking_status=status,
        ticket_code=ticket_code,
    )


def _utility_provider(status: str = "confirmed") -> UtilityProviderStatus:
    return UtilityProviderStatus(
        provider_ref_id="EVN_001",
        provider_status=status,
        bill_status="paid" if status == "confirmed" else "unpaid",
    )


def _refund(status: str = "not_requested") -> RefundStatus:
    return RefundStatus(
        transaction_id="TXN_001",
        refund_status=status,
        refund_amount=450000 if status in ("approved", "executed") else None,
    )


def _bundle(**kwargs) -> EvidenceBundle:
    return EvidenceBundle(**kwargs)


# ═══════════════════════════════════════════════════════════
#  1. Conflict Detection
# ═══════════════════════════════════════════════════════════


class TestConflictRules:
    def test_ledger_debited_txn_pending_is_conflict(self) -> None:
        """Wallet debited + txn pending → conflict."""
        evidence = _bundle(
            wallet_ledger=_ledger(debited=True),
            transaction=_txn(status="pending"),
        )
        conflicts = detect_all_conflicts(evidence)
        assert len(conflicts) == 1
        assert "pending" in conflicts[0].description

    def test_ledger_debited_txn_completed_no_conflict(self) -> None:
        """Wallet debited + txn completed → no conflict."""
        evidence = _bundle(
            wallet_ledger=_ledger(debited=True),
            transaction=_txn(status="completed"),
        )
        conflicts = detect_all_conflicts(evidence)
        assert len(conflicts) == 0

    def test_provider_ticket_issued_but_no_code(self) -> None:
        """Provider says ticket_issued but ticket_code is null → conflict."""
        evidence = _bundle(
            train_provider=_train_provider(status="ticket_issued", ticket_code=None),
        )
        conflicts = detect_all_conflicts(evidence)
        assert len(conflicts) == 1
        assert "ticket_code" in conflicts[0].field

    def test_provider_ticket_issued_with_code_no_conflict(self) -> None:
        """Provider says ticket_issued with code → no conflict."""
        evidence = _bundle(
            train_provider=_train_provider(status="ticket_issued", ticket_code="PNR_123"),
        )
        conflicts = detect_all_conflicts(evidence)
        assert len(conflicts) == 0

    def test_refund_executed_but_no_credit(self) -> None:
        """Refund executed but ledger has no credit → conflict."""
        evidence = _bundle(
            wallet_ledger=_ledger(debited=True, refunded=False),
            refund_status=_refund(status="executed"),
        )
        conflicts = detect_all_conflicts(evidence)
        assert len(conflicts) == 1
        assert "refund credit" in conflicts[0].description.lower()

    def test_refund_executed_with_credit_no_conflict(self) -> None:
        """Refund executed + ledger has credit → no conflict."""
        evidence = _bundle(
            wallet_ledger=_ledger(debited=True, refunded=True),
            refund_status=_refund(status="executed"),
        )
        conflicts = detect_all_conflicts(evidence)
        assert len(conflicts) == 0

    def test_user_ownership_mismatch(self) -> None:
        """Transaction user != case user → conflict."""
        evidence = _bundle(transaction=_txn(user_id="U999"))
        conflicts = detect_all_conflicts(evidence, case_user_id="U001")
        assert len(conflicts) == 1
        assert "fraud" in conflicts[0].description.lower()

    def test_user_ownership_match_no_conflict(self) -> None:
        evidence = _bundle(transaction=_txn(user_id="U001"))
        conflicts = detect_all_conflicts(evidence, case_user_id="U001")
        assert len(conflicts) == 0

    def test_multiple_conflicts_returned(self) -> None:
        """Multiple conflicts can be detected simultaneously."""
        evidence = _bundle(
            wallet_ledger=_ledger(debited=True, refunded=False),
            transaction=_txn(status="pending"),
            refund_status=_refund(status="executed"),
        )
        conflicts = detect_all_conflicts(evidence)
        assert len(conflicts) == 2


# ═══════════════════════════════════════════════════════════
#  2. Refund Eligibility
# ═══════════════════════════════════════════════════════════


class TestRefundRules:
    def test_wallet_not_debited_blocks_refund(self) -> None:
        result = check_wallet_debited(_ledger(debited=False))
        assert result.eligible is False
        assert "not debited" in result.reason

    def test_wallet_debited_allows_refund(self) -> None:
        result = check_wallet_debited(_ledger(debited=True))
        assert result.eligible is True

    def test_wallet_none_blocks_refund(self) -> None:
        result = check_wallet_debited(None)
        assert result.eligible is False

    def test_provider_ticket_issued_blocks_refund(self) -> None:
        result = check_provider_failed(ProviderStatusValue.TICKET_ISSUED)
        assert result.eligible is False
        assert "delivered" in result.reason

    def test_provider_confirmed_blocks_refund(self) -> None:
        result = check_provider_failed(ProviderStatusValue.CONFIRMED)
        assert result.eligible is False

    def test_provider_ticket_not_issued_allows_refund(self) -> None:
        result = check_provider_failed(ProviderStatusValue.TICKET_NOT_ISSUED)
        assert result.eligible is True

    def test_provider_failed_allows_refund(self) -> None:
        result = check_provider_failed(ProviderStatusValue.FAILED)
        assert result.eligible is True

    def test_provider_not_confirmed_blocks_refund(self) -> None:
        """not_confirmed ≠ failed — not eligible for immediate refund."""
        result = check_provider_failed(ProviderStatusValue.NOT_CONFIRMED)
        assert result.eligible is False
        assert "ambiguous" in result.reason

    def test_provider_pending_blocks_refund(self) -> None:
        result = check_provider_failed(ProviderStatusValue.PENDING)
        assert result.eligible is False

    def test_refund_already_executed_blocks(self) -> None:
        result = check_no_existing_refund(_refund(status="executed"))
        assert result.eligible is False
        assert "duplicate" in result.reason

    def test_refund_already_requested_blocks(self) -> None:
        result = check_no_existing_refund(_refund(status="requested"))
        assert result.eligible is False

    def test_refund_already_approved_blocks(self) -> None:
        result = check_no_existing_refund(_refund(status="approved"))
        assert result.eligible is False

    def test_refund_not_requested_allows(self) -> None:
        result = check_no_existing_refund(_refund(status="not_requested"))
        assert result.eligible is True

    def test_refund_none_allows(self) -> None:
        result = check_no_existing_refund(None)
        assert result.eligible is True

    def test_conflicts_block_refund(self) -> None:
        evidence = _bundle(
            conflicts=[
                EvidenceConflict(
                    source_a="a", source_b="b", field="f",
                    value_a="1", value_b="2", description="test conflict",
                )
            ]
        )
        result = check_no_conflicts(evidence)
        assert result.eligible is False

    def test_no_conflicts_allows_refund(self) -> None:
        result = check_no_conflicts(_bundle())
        assert result.eligible is True

    def test_full_check_all_pass(self) -> None:
        result = full_refund_eligibility_check(
            ledger=_ledger(debited=True),
            provider_status=ProviderStatusValue.TICKET_NOT_ISSUED,
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert result.eligible is True
        assert "all checks passed" in result.reason

    def test_full_check_first_failure_wins(self) -> None:
        """If wallet not debited, don't even check provider."""
        result = full_refund_eligibility_check(
            ledger=_ledger(debited=False),
            provider_status=ProviderStatusValue.TICKET_NOT_ISSUED,
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert result.eligible is False
        assert "not debited" in result.reason


# ═══════════════════════════════════════════════════════════
#  3. Train Ticket Decision Matrix
# ═══════════════════════════════════════════════════════════


class TestTrainTicketRules:
    def test_train_001_ticket_not_issued_refund_draft(self) -> None:
        """TRAIN_001: debited + ticket_not_issued + no refund → refund draft."""
        decision = decide_train_ticket(
            ledger=_ledger(debited=True),
            provider=_train_provider(status="ticket_not_issued"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.CREATE_REFUND_REQUEST_DRAFT
        assert decision.approval_required is True

    def test_train_002_ticket_issued_no_refund(self) -> None:
        """TRAIN_002: ticket issued with code → draft response, no refund."""
        decision = decide_train_ticket(
            ledger=_ledger(debited=True),
            provider=_train_provider(status="ticket_issued", ticket_code="PNR_ABC"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert decision.approval_required is False

    def test_train_003_provider_no_record_reconciliation(self) -> None:
        """TRAIN_003: provider_no_record → reconciliation ticket."""
        decision = decide_train_ticket(
            ledger=_ledger(debited=True),
            provider=_train_provider(status="provider_no_record"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.CREATE_RECONCILIATION_TICKET_DRAFT

    def test_conflict_triggers_manual_review(self) -> None:
        """Conflict → manual_review regardless of other data."""
        evidence = _bundle(
            conflicts=[
                EvidenceConflict(
                    source_a="a", source_b="b", field="f",
                    value_a="1", value_b="2", description="test",
                )
            ]
        )
        decision = decide_train_ticket(
            ledger=_ledger(debited=True),
            provider=_train_provider(status="ticket_not_issued"),
            refund=_refund(status="not_requested"),
            evidence=evidence,
        )
        assert decision.action == ActionType.MANUAL_REVIEW
        assert decision.approval_required is True

    def test_refund_already_executed_no_action(self) -> None:
        """Refund already executed → no duplicate."""
        decision = decide_train_ticket(
            ledger=_ledger(debited=True),
            provider=_train_provider(status="ticket_not_issued"),
            refund=_refund(status="executed"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.NO_ACTION

    def test_booking_pending_wait_sla(self) -> None:
        decision = decide_train_ticket(
            ledger=_ledger(debited=True),
            provider=_train_provider(status="booking_pending"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.WAIT_SLA

    def test_wallet_not_debited_draft_response(self) -> None:
        decision = decide_train_ticket(
            ledger=_ledger(debited=False),
            provider=_train_provider(status="ticket_not_issued"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert decision.approval_required is False


# ═══════════════════════════════════════════════════════════
#  4. Utility Bill Decision Matrix
# ═══════════════════════════════════════════════════════════


class TestUtilityBillRules:
    def test_bill_001_confirmed_no_refund(self) -> None:
        """BILL_001: confirmed/paid → draft response, no refund."""
        decision = decide_utility_bill(
            ledger=_ledger(debited=True, amount=720000),
            provider=_utility_provider(status="confirmed"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.DRAFT_CUSTOMER_RESPONSE
        assert decision.approval_required is False

    def test_bill_002_not_confirmed_reconciliation(self) -> None:
        """BILL_002: not_confirmed → reconciliation, NOT refund."""
        decision = decide_utility_bill(
            ledger=_ledger(debited=True, amount=480000),
            provider=_utility_provider(status="not_confirmed"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.CREATE_RECONCILIATION_TICKET_DRAFT
        assert "not_confirmed" in decision.diagnosis

    def test_bill_003_failed_refund_draft(self) -> None:
        """BILL_003: failed + debited + no refund → refund draft."""
        decision = decide_utility_bill(
            ledger=_ledger(debited=True, amount=310000),
            provider=_utility_provider(status="failed"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.CREATE_REFUND_REQUEST_DRAFT
        assert decision.approval_required is True

    def test_not_confirmed_is_NOT_failed(self) -> None:
        """CRITICAL: not_confirmed ≠ failed. Must NOT create refund draft."""
        decision = decide_utility_bill(
            ledger=_ledger(debited=True),
            provider=_utility_provider(status="not_confirmed"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action != ActionType.CREATE_REFUND_REQUEST_DRAFT

    def test_pending_wait_sla(self) -> None:
        decision = decide_utility_bill(
            ledger=_ledger(debited=True),
            provider=_utility_provider(status="pending"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.WAIT_SLA

    def test_amount_mismatch_manual_review(self) -> None:
        decision = decide_utility_bill(
            ledger=_ledger(debited=True),
            provider=_utility_provider(status="amount_mismatch"),
            refund=_refund(status="not_requested"),
            evidence=_bundle(),
        )
        assert decision.action == ActionType.MANUAL_REVIEW

    def test_conflict_manual_review(self) -> None:
        evidence = _bundle(
            conflicts=[
                EvidenceConflict(
                    source_a="a", source_b="b", field="f",
                    value_a="1", value_b="2", description="test",
                )
            ]
        )
        decision = decide_utility_bill(
            ledger=_ledger(debited=True),
            provider=_utility_provider(status="failed"),
            refund=_refund(status="not_requested"),
            evidence=evidence,
        )
        assert decision.action == ActionType.MANUAL_REVIEW


# ═══════════════════════════════════════════════════════════
#  5. Risk Classification
# ═══════════════════════════════════════════════════════════


class TestRiskRules:
    def test_refund_small_amount_medium_risk(self) -> None:
        assert classify_risk(ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000) == RiskLevel.MEDIUM

    def test_refund_large_amount_high_risk(self) -> None:
        assert classify_risk(ActionType.CREATE_REFUND_REQUEST_DRAFT, 5_000_000) == RiskLevel.HIGH

    def test_manual_review_high_risk(self) -> None:
        assert classify_risk(ActionType.MANUAL_REVIEW) == RiskLevel.HIGH

    def test_draft_response_low_risk(self) -> None:
        assert classify_risk(ActionType.DRAFT_CUSTOMER_RESPONSE) == RiskLevel.LOW

    def test_reconciliation_low_risk(self) -> None:
        assert classify_risk(ActionType.CREATE_RECONCILIATION_TICKET_DRAFT) == RiskLevel.LOW

    def test_refund_requires_approval(self) -> None:
        assert requires_approval(ActionType.CREATE_REFUND_REQUEST_DRAFT) is True

    def test_manual_review_requires_approval(self) -> None:
        assert requires_approval(ActionType.MANUAL_REVIEW) is True

    def test_draft_response_no_approval(self) -> None:
        assert requires_approval(ActionType.DRAFT_CUSTOMER_RESPONSE) is False

    def test_reconciliation_no_approval(self) -> None:
        assert requires_approval(ActionType.CREATE_RECONCILIATION_TICKET_DRAFT) is False


# ═══════════════════════════════════════════════════════════
#  6. Idempotency
# ═══════════════════════════════════════════════════════════


class TestIdempotencyRules:
    def test_key_is_deterministic(self) -> None:
        """Same inputs → same key."""
        k1 = generate_idempotency_key("TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000)
        k2 = generate_idempotency_key("TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000)
        assert k1 == k2

    def test_different_inputs_different_keys(self) -> None:
        k1 = generate_idempotency_key("TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000)
        k2 = generate_idempotency_key("TXN_002", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000)
        assert k1 != k2

    def test_different_amounts_different_keys(self) -> None:
        k1 = generate_idempotency_key("TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000)
        k2 = generate_idempotency_key("TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 350000)
        assert k1 != k2

    def test_key_length(self) -> None:
        key = generate_idempotency_key("TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000)
        assert len(key) == 16

    def test_duplicate_refund_already_executed(self) -> None:
        assert is_duplicate_action(
            "TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000,
            _refund(status="executed"),
        ) is True

    def test_duplicate_refund_already_requested(self) -> None:
        assert is_duplicate_action(
            "TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000,
            _refund(status="requested"),
        ) is True

    def test_not_duplicate_if_not_requested(self) -> None:
        assert is_duplicate_action(
            "TXN_001", ActionType.CREATE_REFUND_REQUEST_DRAFT, 450000,
            _refund(status="not_requested"),
        ) is False

    def test_non_refund_action_never_duplicate(self) -> None:
        assert is_duplicate_action(
            "TXN_001", ActionType.DRAFT_CUSTOMER_RESPONSE, 0,
            _refund(status="executed"),
        ) is False

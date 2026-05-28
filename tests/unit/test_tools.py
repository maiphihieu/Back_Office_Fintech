"""Unit tests for the tool layer.

Covers:
  1. Read-only tools — return correct schemas
  2. Draft tools — safety guard, idempotency, duplicate prevention
  3. Error handling — ToolDataNotFound, ToolTimeout, ToolValidationError
  4. Safety — forbidden actions blocked
"""

import pytest

from fintech_agent.safety.money_action_guard import SafetyViolation
from fintech_agent.schemas.enums import RefundStatusValue
from fintech_agent.schemas.evidence import RefundStatus, Transaction, WalletLedger
from fintech_agent.tools.draft_action_tools import (
    DraftStore,
    create_customer_response_draft,
    create_reconciliation_ticket_draft,
    create_refund_request_draft,
    reset_default_store,
)
from fintech_agent.tools.ledger_tools import LedgerResult, get_wallet_ledger
from fintech_agent.tools.reconciliation_tools import (
    ReconciliationResult,
    get_reconciliation_status,
)
from fintech_agent.tools.refund_tools import RefundStatusResult, get_refund_status
from fintech_agent.tools.tool_errors import (
    DuplicateActionError,
    ToolDataNotFound,
    ToolTimeout,
    ToolValidationError,
)
from fintech_agent.tools.train_provider_tools import (
    TrainProviderResult,
    get_train_provider_status,
)
from fintech_agent.tools.transaction_tools import (
    TransactionResult,
    get_transaction,
)
from fintech_agent.tools.utility_provider_tools import (
    UtilityProviderResult,
    get_utility_bill_status,
)


# ═══════════════════════════════════════════════════════════
#  1. Read-only tools — correct schema
# ═══════════════════════════════════════════════════════════


class TestTransactionTool:
    def test_get_existing_transaction(self) -> None:
        result = get_transaction("TXN_TRAIN_001")
        assert isinstance(result, TransactionResult)
        assert result.success is True
        assert isinstance(result.transaction, Transaction)
        assert result.transaction.transaction_id == "TXN_TRAIN_001"
        assert result.transaction.amount == 450000

    def test_get_nonexistent_raises(self) -> None:
        with pytest.raises(ToolDataNotFound, match="TXN_NONE"):
            get_transaction("TXN_NONE")

    def test_timeout_simulation(self) -> None:
        with pytest.raises(ToolTimeout, match="Timeout"):
            get_transaction("TXN_TIMEOUT_001")


class TestLedgerTool:
    def test_get_existing_ledger(self) -> None:
        result = get_wallet_ledger("TXN_TRAIN_001")
        assert isinstance(result, LedgerResult)
        assert result.success is True
        assert isinstance(result.ledger, WalletLedger)
        assert result.ledger.has_user_debit is True
        assert result.ledger.debit_amount == 450000

    def test_get_nonexistent_raises(self) -> None:
        with pytest.raises(ToolDataNotFound):
            get_wallet_ledger("TXN_NONE")

    def test_timeout_simulation(self) -> None:
        with pytest.raises(ToolTimeout):
            get_wallet_ledger("TXN_TIMEOUT_001")


class TestTrainProviderTool:
    def test_get_existing_provider(self) -> None:
        result = get_train_provider_status("TRAIN_REF_001")
        assert isinstance(result, TrainProviderResult)
        assert result.success is True
        assert result.provider_status.booking_status == "ticket_not_issued"

    def test_get_ticket_issued_with_code(self) -> None:
        result = get_train_provider_status("TRAIN_REF_002")
        assert result.provider_status.ticket_code == "PNR_ABC123"

    def test_nonexistent_raises(self) -> None:
        with pytest.raises(ToolDataNotFound):
            get_train_provider_status("REF_NONE")

    def test_timeout_simulation(self) -> None:
        with pytest.raises(ToolTimeout):
            get_train_provider_status("TRAIN_REF_TIMEOUT_001")


class TestUtilityProviderTool:
    def test_get_confirmed_provider(self) -> None:
        result = get_utility_bill_status("EVN_REF_001")
        assert isinstance(result, UtilityProviderResult)
        assert result.success is True
        assert result.provider_status.provider_status == "confirmed"

    def test_get_not_confirmed(self) -> None:
        result = get_utility_bill_status("EVN_REF_002")
        assert result.provider_status.provider_status == "not_confirmed"

    def test_get_failed(self) -> None:
        result = get_utility_bill_status("WATER_REF_001")
        assert result.provider_status.provider_status == "failed"

    def test_nonexistent_raises(self) -> None:
        with pytest.raises(ToolDataNotFound):
            get_utility_bill_status("REF_NONE")


class TestRefundTool:
    def test_get_not_requested(self) -> None:
        result = get_refund_status("TXN_TRAIN_001")
        assert isinstance(result, RefundStatusResult)
        assert result.success is True
        assert result.refund_status.refund_status == "not_requested"

    def test_get_already_executed(self) -> None:
        result = get_refund_status("TXN_REFUND_001")
        assert result.refund_status.refund_status == "executed"
        assert result.refund_status.refund_amount == 450000

    def test_nonexistent_raises(self) -> None:
        with pytest.raises(ToolDataNotFound):
            get_refund_status("TXN_NONE")


class TestReconciliationTool:
    def test_get_existing_mismatch(self) -> None:
        result = get_reconciliation_status("TXN_BILL_002")
        assert isinstance(result, ReconciliationResult)
        assert result.success is True
        assert result.reconciliation.status == "wallet_provider_mismatch"

    def test_no_record_returns_none(self) -> None:
        """No reconciliation record is normal — returns None, not error."""
        result = get_reconciliation_status("TXN_TRAIN_001")
        assert result.success is True
        assert result.reconciliation is None


# ═══════════════════════════════════════════════════════════
#  2. Draft tools — core functionality
# ═══════════════════════════════════════════════════════════


class TestRefundDraftTool:
    def setup_method(self) -> None:
        reset_default_store()
        self.store = DraftStore()

    def test_create_refund_draft_success(self) -> None:
        result = create_refund_request_draft(
            case_id="CASE_001",
            transaction_id="TXN_001",
            user_id="U001",
            amount=450000,
            reason="wallet debited, ticket not issued",
            evidence_summary=["ledger=debited", "provider=ticket_not_issued"],
            store=self.store,
        )
        assert result.success is True
        assert result.draft is not None
        assert result.draft.amount == 450000
        assert result.idempotency_key  # non-empty

    def test_refund_draft_has_idempotency_key(self) -> None:
        result = create_refund_request_draft(
            case_id="CASE_001",
            transaction_id="TXN_001",
            user_id="U001",
            amount=450000,
            reason="test",
            evidence_summary=["test evidence"],
            store=self.store,
        )
        assert len(result.idempotency_key) == 16
        assert result.draft.idempotency_key == result.idempotency_key

    def test_duplicate_draft_blocked_by_store(self) -> None:
        """Second draft with same key → DuplicateActionError."""
        create_refund_request_draft(
            case_id="CASE_001",
            transaction_id="TXN_001",
            user_id="U001",
            amount=450000,
            reason="first",
            evidence_summary=["first evidence"],
            store=self.store,
        )
        with pytest.raises(DuplicateActionError, match="Duplicate"):
            create_refund_request_draft(
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=450000,
                reason="second attempt",
                evidence_summary=["second evidence"],
                store=self.store,
            )

    def test_duplicate_blocked_by_refund_status(self) -> None:
        """Refund already executed → DuplicateActionError."""
        refund = RefundStatus(
            transaction_id="TXN_001",
            refund_status=RefundStatusValue.EXECUTED,
            refund_amount=450000,
        )
        with pytest.raises(DuplicateActionError):
            create_refund_request_draft(
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=450000,
                reason="test",
                evidence_summary=["test"],
                refund_status=refund,
                store=self.store,
            )

    def test_duplicate_blocked_by_refund_requested(self) -> None:
        """Refund already requested → DuplicateActionError."""
        refund = RefundStatus(
            transaction_id="TXN_001",
            refund_status=RefundStatusValue.REQUESTED,
        )
        with pytest.raises(DuplicateActionError):
            create_refund_request_draft(
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=450000,
                reason="test",
                evidence_summary=["test"],
                refund_status=refund,
                store=self.store,
            )

    def test_zero_amount_rejected(self) -> None:
        with pytest.raises(ToolValidationError, match="amount"):
            create_refund_request_draft(
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=0,
                reason="test",
                evidence_summary=["test"],
                store=self.store,
            )

    def test_empty_evidence_rejected(self) -> None:
        with pytest.raises(ToolValidationError, match="evidence_summary"):
            create_refund_request_draft(
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                amount=450000,
                reason="test",
                evidence_summary=[],
                store=self.store,
            )


class TestReconciliationDraftTool:
    def setup_method(self) -> None:
        self.store = DraftStore()

    def test_create_reconciliation_draft(self) -> None:
        result = create_reconciliation_ticket_draft(
            case_id="CASE_001",
            transaction_id="TXN_001",
            user_id="U001",
            mismatch_type="wallet_debited_provider_no_record",
            evidence_summary=["Provider has no record but wallet was debited"],
            store=self.store,
        )
        assert result.success is True
        assert result.draft is not None
        assert result.idempotency_key

    def test_empty_evidence_rejected(self) -> None:
        with pytest.raises(ToolValidationError, match="evidence_summary"):
            create_reconciliation_ticket_draft(
                case_id="CASE_001",
                transaction_id="TXN_001",
                user_id="U001",
                mismatch_type="test",
                evidence_summary=[],
                store=self.store,
            )


class TestCustomerResponseDraftTool:
    def setup_method(self) -> None:
        self.store = DraftStore()

    def test_create_response_draft(self) -> None:
        result = create_customer_response_draft(
            case_id="CASE_001",
            transaction_id="TXN_001",
            message="Vé tàu của bạn đã được cấp. Mã PNR: PNR_ABC123",
            store=self.store,
        )
        assert result.success is True
        assert result.draft.message.startswith("Vé tàu")

    def test_empty_message_rejected(self) -> None:
        with pytest.raises(ToolValidationError, match="message"):
            create_customer_response_draft(
                case_id="CASE_001",
                transaction_id="TXN_001",
                message="",
                store=self.store,
            )


# ═══════════════════════════════════════════════════════════
#  3. Safety — forbidden actions blocked
# ═══════════════════════════════════════════════════════════


class TestToolSafety:
    """Verify that the safety module blocks forbidden actions
    even if someone tries to call them through the tool layer."""

    def test_no_execute_refund_tool_exists(self) -> None:
        """There must not be any function named execute_refund in the tools package."""
        import fintech_agent.tools as tools_module

        assert not hasattr(tools_module, "execute_refund")

    def test_no_update_wallet_balance_tool(self) -> None:
        import fintech_agent.tools as tools_module

        assert not hasattr(tools_module, "update_wallet_balance")

    def test_no_edit_ledger_tool(self) -> None:
        import fintech_agent.tools as tools_module

        assert not hasattr(tools_module, "edit_ledger")

    def test_guard_blocks_execute_refund(self) -> None:
        from fintech_agent.safety.money_action_guard import guard_action

        with pytest.raises(SafetyViolation, match="execute_refund"):
            guard_action("execute_refund", context="test")


# ═══════════════════════════════════════════════════════════
#  4. Error types
# ═══════════════════════════════════════════════════════════


class TestToolErrors:
    def test_tool_data_not_found_message(self) -> None:
        err = ToolDataNotFound("get_transaction", "transaction_id", "TXN_999")
        assert "TXN_999" in str(err)
        assert err.tool_name == "get_transaction"

    def test_tool_timeout_message(self) -> None:
        err = ToolTimeout("get_wallet_ledger", timeout_ms=3000)
        assert "3000" in str(err)

    def test_tool_validation_error(self) -> None:
        err = ToolValidationError("create_refund", "amount", "must be positive")
        assert "amount" in str(err)
        assert err.field == "amount"

    def test_duplicate_action_error(self) -> None:
        err = DuplicateActionError("create_refund", "abc123")
        assert "abc123" in str(err)
        assert err.idempotency_key == "abc123"


# ═══════════════════════════════════════════════════════════
#  5. Cross-scenario: full evidence fetch pipeline
# ═══════════════════════════════════════════════════════════


class TestEvidenceFetchPipeline:
    """Test that the complete evidence fetch for a scenario works."""

    def test_train_001_full_fetch(self) -> None:
        """TRAIN_001: fetch all evidence → all succeed."""
        txn = get_transaction("TXN_TRAIN_001")
        assert txn.success

        ledger = get_wallet_ledger("TXN_TRAIN_001")
        assert ledger.success
        assert ledger.ledger.has_user_debit

        provider = get_train_provider_status(txn.transaction.provider_ref_id)
        assert provider.success
        assert provider.provider_status.booking_status == "ticket_not_issued"

        refund = get_refund_status("TXN_TRAIN_001")
        assert refund.success
        assert refund.refund_status.refund_status == "not_requested"

        recon = get_reconciliation_status("TXN_TRAIN_001")
        assert recon.success
        assert recon.reconciliation is None

    def test_bill_002_full_fetch(self) -> None:
        """BILL_002: not_confirmed with reconciliation record."""
        txn = get_transaction("TXN_BILL_002")
        provider = get_utility_bill_status(txn.transaction.provider_ref_id)
        recon = get_reconciliation_status("TXN_BILL_002")

        assert provider.provider_status.provider_status == "not_confirmed"
        assert recon.reconciliation is not None
        assert recon.reconciliation.status == "wallet_provider_mismatch"

    def test_train_001_full_draft_pipeline(self) -> None:
        """TRAIN_001: evidence → refund draft."""
        store = DraftStore()

        txn = get_transaction("TXN_TRAIN_001")
        ledger = get_wallet_ledger("TXN_TRAIN_001")
        refund = get_refund_status("TXN_TRAIN_001")

        result = create_refund_request_draft(
            case_id="CASE_TRAIN_001",
            transaction_id="TXN_TRAIN_001",
            user_id=txn.transaction.user_id,
            amount=ledger.ledger.debit_amount,
            reason="wallet debited, ticket not issued",
            evidence_summary=["ledger=debited", "provider=ticket_not_issued"],
            refund_status=refund.refund_status,
            store=store,
        )

        assert result.success is True
        assert result.draft.amount == 450000
        assert result.draft.case_id == "CASE_TRAIN_001"

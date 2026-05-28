"""Unit tests for repository layer.

Tests verify:
  1. Each repository loads mock data and returns typed models.
  2. RecordNotFound is raised for missing records.
  3. All 8 scenarios are accessible via the correct keys.
  4. CaseRepository CRUD operations.
"""

import pytest

from fintech_agent.repositories import (
    CaseRepository,
    LedgerRepository,
    ReconciliationRepository,
    RecordNotFound,
    RefundRepository,
    TrainProviderRepository,
    TransactionRepository,
    UtilityProviderRepository,
)
from fintech_agent.schemas import (
    CaseState,
    CaseStatus,
    ReconciliationStatus,
    RefundStatus,
    TrainProviderStatus,
    Transaction,
    UtilityProviderStatus,
    WalletLedger,
)


# ════════════════════════════════════════════════════════════
#  Transaction Repository
# ════════════════════════════════════════════════════════════


class TestTransactionRepository:
    def setup_method(self) -> None:
        self.repo = TransactionRepository()

    def test_get_train_001(self) -> None:
        txn = self.repo.get_by_id("TXN_TRAIN_001")
        assert isinstance(txn, Transaction)
        assert txn.amount == 450000
        assert txn.user_id == "U001"
        assert txn.service_type == "train_ticket"

    def test_get_bill_001(self) -> None:
        txn = self.repo.get_by_id("TXN_BILL_001")
        assert txn.amount == 720000
        assert txn.bill_code == "EVN123456"

    def test_get_conflict_001(self) -> None:
        txn = self.repo.get_by_id("TXN_CONFLICT_001")
        assert txn.status == "pending"

    def test_get_refund_001(self) -> None:
        txn = self.repo.get_by_id("TXN_REFUND_001")
        assert txn.amount == 450000

    def test_not_found_raises(self) -> None:
        with pytest.raises(RecordNotFound, match="Transaction not found"):
            self.repo.get_by_id("TXN_NONEXISTENT")

    def test_get_by_user_id(self) -> None:
        txns = self.repo.get_by_user_id("U001")
        assert len(txns) == 2  # TRAIN_001 + TRAIN_002
        assert all(t.user_id == "U001" for t in txns)

    def test_get_by_user_id_empty(self) -> None:
        txns = self.repo.get_by_user_id("U999")
        assert txns == []


# ════════════════════════════════════════════════════════════
#  Ledger Repository
# ════════════════════════════════════════════════════════════


class TestLedgerRepository:
    def setup_method(self) -> None:
        self.repo = LedgerRepository()

    def test_train_001_debited(self) -> None:
        ledger = self.repo.get_by_transaction_id("TXN_TRAIN_001")
        assert isinstance(ledger, WalletLedger)
        assert ledger.has_user_debit is True
        assert ledger.debit_amount == 450000
        assert ledger.has_credit_refund is False

    def test_conflict_001_debited(self) -> None:
        """CONFLICT_001: ledger debited but txn status is pending → conflict."""
        ledger = self.repo.get_by_transaction_id("TXN_CONFLICT_001")
        assert ledger.has_user_debit is True
        assert ledger.debit_amount == 400000

    def test_refund_001_already_refunded(self) -> None:
        """REFUND_001: debit + credit, net_amount = 0."""
        ledger = self.repo.get_by_transaction_id("TXN_REFUND_001")
        assert ledger.has_user_debit is True
        assert ledger.has_credit_refund is True
        assert ledger.credit_refund_amount == 450000
        assert ledger.net_amount == 0
        assert len(ledger.entries) == 2

    def test_not_found_raises(self) -> None:
        with pytest.raises(RecordNotFound, match="WalletLedger not found"):
            self.repo.get_by_transaction_id("TXN_NONEXISTENT")


# ════════════════════════════════════════════════════════════
#  Train Provider Repository
# ════════════════════════════════════════════════════════════


class TestTrainProviderRepository:
    def setup_method(self) -> None:
        self.repo = TrainProviderRepository()

    def test_train_001_not_issued(self) -> None:
        """TRAIN_001: wallet debited, ticket NOT issued."""
        status = self.repo.get_by_ref_id("TRAIN_REF_001")
        assert isinstance(status, TrainProviderStatus)
        assert status.booking_status == "ticket_not_issued"
        assert status.ticket_code is None

    def test_train_002_issued(self) -> None:
        """TRAIN_002: wallet debited, ticket issued with PNR."""
        status = self.repo.get_by_ref_id("TRAIN_REF_002")
        assert status.booking_status == "ticket_issued"
        assert status.ticket_code == "PNR_ABC123"

    def test_train_003_no_record(self) -> None:
        """TRAIN_003: provider has no record at all."""
        status = self.repo.get_by_ref_id("TRAIN_REF_003")
        assert status.booking_status == "provider_no_record"

    def test_not_found_raises(self) -> None:
        with pytest.raises(RecordNotFound):
            self.repo.get_by_ref_id("NONEXISTENT_REF")


# ════════════════════════════════════════════════════════════
#  Utility Provider Repository
# ════════════════════════════════════════════════════════════


class TestUtilityProviderRepository:
    def setup_method(self) -> None:
        self.repo = UtilityProviderRepository()

    def test_bill_001_confirmed(self) -> None:
        """BILL_001: provider confirmed, bill paid."""
        status = self.repo.get_by_ref_id("EVN_REF_001")
        assert isinstance(status, UtilityProviderStatus)
        assert status.provider_status == "confirmed"
        assert status.bill_status == "paid"

    def test_bill_002_not_confirmed(self) -> None:
        """BILL_002: provider not_confirmed, bill unpaid."""
        status = self.repo.get_by_ref_id("EVN_REF_002")
        assert status.provider_status == "not_confirmed"
        assert status.bill_status == "unpaid"

    def test_bill_003_failed(self) -> None:
        """BILL_003: provider failed."""
        status = self.repo.get_by_ref_id("WATER_REF_001")
        assert status.provider_status == "failed"

    def test_not_found_raises(self) -> None:
        with pytest.raises(RecordNotFound):
            self.repo.get_by_ref_id("NONEXISTENT_REF")


# ════════════════════════════════════════════════════════════
#  Refund Repository
# ════════════════════════════════════════════════════════════


class TestRefundRepository:
    def setup_method(self) -> None:
        self.repo = RefundRepository()

    def test_train_001_not_requested(self) -> None:
        status = self.repo.get_by_transaction_id("TXN_TRAIN_001")
        assert isinstance(status, RefundStatus)
        assert status.refund_status == "not_requested"

    def test_refund_001_already_executed(self) -> None:
        """REFUND_001: refund already executed — prevent duplicates."""
        status = self.repo.get_by_transaction_id("TXN_REFUND_001")
        assert status.refund_status == "executed"
        assert status.refund_amount == 450000
        assert status.refund_id == "REFUND_001"

    def test_not_found_raises(self) -> None:
        with pytest.raises(RecordNotFound):
            self.repo.get_by_transaction_id("TXN_NONEXISTENT")


# ════════════════════════════════════════════════════════════
#  Reconciliation Repository
# ════════════════════════════════════════════════════════════


class TestReconciliationRepository:
    def setup_method(self) -> None:
        self.repo = ReconciliationRepository()

    def test_bill_002_has_mismatch(self) -> None:
        status = self.repo.get_by_transaction_id("TXN_BILL_002")
        assert isinstance(status, ReconciliationStatus)
        assert status.status == "wallet_provider_mismatch"

    def test_no_record_returns_none(self) -> None:
        """Most transactions have no reconciliation record — returns None."""
        result = self.repo.get_by_transaction_id("TXN_TRAIN_001")
        assert result is None


# ════════════════════════════════════════════════════════════
#  Case Repository (in-memory)
# ════════════════════════════════════════════════════════════


class TestCaseRepository:
    def setup_method(self) -> None:
        self.repo = CaseRepository()

    def _make_case(self, case_id: str = "CASE_001") -> CaseState:
        return CaseState(case_id=case_id, ticket_id="TICKET_001")

    def test_save_and_get(self) -> None:
        case = self._make_case()
        self.repo.save(case)
        fetched = self.repo.get_by_id("CASE_001")
        assert fetched.case_id == "CASE_001"
        assert fetched.current_state == CaseStatus.NEW

    def test_get_not_found(self) -> None:
        with pytest.raises(RecordNotFound, match="CaseState not found"):
            self.repo.get_by_id("NONEXISTENT")

    def test_exists(self) -> None:
        assert self.repo.exists("CASE_001") is False
        self.repo.save(self._make_case())
        assert self.repo.exists("CASE_001") is True

    def test_list_all(self) -> None:
        assert self.repo.list_all() == []
        self.repo.save(self._make_case("CASE_A"))
        self.repo.save(self._make_case("CASE_B"))
        assert len(self.repo.list_all()) == 2

    def test_update_overwrites(self) -> None:
        case = self._make_case()
        self.repo.save(case)
        case.transition_to(CaseStatus.EXTRACTING)
        self.repo.save(case)
        fetched = self.repo.get_by_id("CASE_001")
        assert fetched.current_state == CaseStatus.EXTRACTING


# ════════════════════════════════════════════════════════════
#  Cross-scenario: verify all 8 scenarios are accessible
# ════════════════════════════════════════════════════════════


class TestAllScenariosAccessible:
    """Verify each MVP scenario can be fully queried."""

    def test_train_001_full_path(self) -> None:
        """TRAIN_001: debited + ticket_not_issued + no refund → refund draft."""
        txn = TransactionRepository().get_by_id("TXN_TRAIN_001")
        ledger = LedgerRepository().get_by_transaction_id("TXN_TRAIN_001")
        provider = TrainProviderRepository().get_by_ref_id(txn.provider_ref_id)
        refund = RefundRepository().get_by_transaction_id("TXN_TRAIN_001")

        assert ledger.has_user_debit is True
        assert provider.booking_status == "ticket_not_issued"
        assert refund.refund_status == "not_requested"

    def test_train_002_full_path(self) -> None:
        """TRAIN_002: debited + ticket_issued → no refund, send ticket code."""
        txn = TransactionRepository().get_by_id("TXN_TRAIN_002")
        provider = TrainProviderRepository().get_by_ref_id(txn.provider_ref_id)

        assert provider.booking_status == "ticket_issued"
        assert provider.ticket_code == "PNR_ABC123"

    def test_train_003_full_path(self) -> None:
        """TRAIN_003: debited + provider_no_record → reconciliation ticket."""
        txn = TransactionRepository().get_by_id("TXN_TRAIN_003")
        provider = TrainProviderRepository().get_by_ref_id(txn.provider_ref_id)

        assert provider.booking_status == "provider_no_record"

    def test_bill_001_full_path(self) -> None:
        """BILL_001: debited + confirmed/paid → no refund."""
        txn = TransactionRepository().get_by_id("TXN_BILL_001")
        provider = UtilityProviderRepository().get_by_ref_id(txn.provider_ref_id)

        assert provider.provider_status == "confirmed"
        assert provider.bill_status == "paid"

    def test_bill_002_full_path(self) -> None:
        """BILL_002: debited + not_confirmed → reconciliation ticket."""
        txn = TransactionRepository().get_by_id("TXN_BILL_002")
        provider = UtilityProviderRepository().get_by_ref_id(txn.provider_ref_id)
        recon = ReconciliationRepository().get_by_transaction_id("TXN_BILL_002")

        assert provider.provider_status == "not_confirmed"
        assert recon is not None
        assert recon.status == "wallet_provider_mismatch"

    def test_bill_003_full_path(self) -> None:
        """BILL_003: debited + failed → refund draft."""
        txn = TransactionRepository().get_by_id("TXN_BILL_003")
        provider = UtilityProviderRepository().get_by_ref_id(txn.provider_ref_id)

        assert provider.provider_status == "failed"

    def test_conflict_001_full_path(self) -> None:
        """CONFLICT_001: ledger debited + txn pending → manual review."""
        txn = TransactionRepository().get_by_id("TXN_CONFLICT_001")
        ledger = LedgerRepository().get_by_transaction_id("TXN_CONFLICT_001")

        assert txn.status == "pending"
        assert ledger.has_user_debit is True  # conflict!

    def test_refund_001_full_path(self) -> None:
        """REFUND_001: already refunded → no duplicate."""
        ledger = LedgerRepository().get_by_transaction_id("TXN_REFUND_001")
        refund = RefundRepository().get_by_transaction_id("TXN_REFUND_001")

        assert refund.refund_status == "executed"
        assert ledger.has_credit_refund is True
        assert ledger.net_amount == 0

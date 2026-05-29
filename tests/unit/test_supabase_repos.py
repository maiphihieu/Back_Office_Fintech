"""Tests for Supabase repository implementations using mock client."""

from unittest.mock import MagicMock

import pytest

from fintech_agent.repositories.base import RecordNotFound


# ─── Helper: mock Supabase response ─────────────────────


def _mock_response(data: list | None = None, count: int | None = None):
    """Create a mock Supabase response object."""
    resp = MagicMock()
    resp.data = data
    resp.count = count
    return resp


def _mock_client_with_table(table_name: str, data: list | None = None):
    """Create a mock client where .table(name).select().eq().execute() returns data."""
    client = MagicMock()
    response = _mock_response(data)

    # Build the chained mock: client.table().select().eq().execute()
    table = MagicMock()
    select = MagicMock()
    eq = MagicMock()
    order = MagicMock()

    client.table.return_value = table
    table.select.return_value = select
    select.eq.return_value = eq
    eq.execute.return_value = response
    eq.order.return_value = order
    order.execute.return_value = response
    select.order.return_value = order

    return client


# ─── Transaction Repository ─────────────────────────────


class TestSupabaseTransactionRepo:
    def test_get_by_id_found(self):
        from fintech_agent.repositories.supabase.supabase_transaction_repo import (
            SupabaseTransactionRepository,
        )

        row = {
            "transaction_id": "TXN_001",
            "user_id": "U001",
            "service_type": "train_ticket",
            "amount": 450000,
            "status": "completed",
            "order_id": "ORD_001",
            "provider_ref_id": "REF_001",
            "bill_code": None,
            "customer_code": None,
            "created_at": "2026-05-27T10:00:00Z",
        }
        client = _mock_client_with_table("transactions", [row])
        repo = SupabaseTransactionRepository(client)

        txn = repo.get_by_id("TXN_001")
        assert txn.transaction_id == "TXN_001"
        assert txn.amount == 450000
        assert txn.service_type == "train_ticket"

    def test_get_by_id_not_found(self):
        from fintech_agent.repositories.supabase.supabase_transaction_repo import (
            SupabaseTransactionRepository,
        )

        client = _mock_client_with_table("transactions", [])
        repo = SupabaseTransactionRepository(client)

        with pytest.raises(RecordNotFound, match="transaction_id"):
            repo.get_by_id("TXN_MISSING")


# ─── Ledger Repository ──────────────────────────────────


class TestSupabaseLedgerRepo:
    def test_get_by_transaction_id_found(self):
        from fintech_agent.repositories.supabase.supabase_ledger_repo import (
            SupabaseLedgerRepository,
        )

        rows = [
            {
                "entry_id": "WLE_001",
                "transaction_id": "TXN_001",
                "user_id": "U001",
                "entry_type": "debit",
                "amount": 450000,
                "balance_after": 550000,
                "reason": "purchase",
                "status": "debited",
                "has_user_debit": True,
                "debit_amount": 450000,
                "has_credit_refund": False,
                "credit_refund_amount": 0,
                "net_amount": 450000,
                "created_at": "2026-05-27T10:00:00Z",
            }
        ]
        client = _mock_client_with_table("wallet_ledger_entries", rows)
        repo = SupabaseLedgerRepository(client)

        ledger = repo.get_by_transaction_id("TXN_001")
        assert ledger.transaction_id == "TXN_001"
        assert ledger.debit_amount == 450000
        assert ledger.has_user_debit is True
        assert len(ledger.entries) == 1

    def test_get_by_transaction_id_not_found(self):
        from fintech_agent.repositories.supabase.supabase_ledger_repo import (
            SupabaseLedgerRepository,
        )

        client = _mock_client_with_table("wallet_ledger_entries", [])
        repo = SupabaseLedgerRepository(client)

        with pytest.raises(RecordNotFound, match="transaction_id"):
            repo.get_by_transaction_id("TXN_MISSING")


# ─── Refund Repository ──────────────────────────────────


class TestSupabaseRefundRepo:
    def test_get_by_transaction_id_found(self):
        from fintech_agent.repositories.supabase.supabase_refund_repo import (
            SupabaseRefundRepository,
        )

        row = {
            "transaction_id": "TXN_001",
            "refund_id": None,
            "status": "not_requested",
            "amount": None,
            "created_at": "2026-05-27T10:00:00Z",
            "updated_at": "2026-05-27T10:00:00Z",
        }
        client = _mock_client_with_table("refunds", [row])
        repo = SupabaseRefundRepository(client)

        status = repo.get_by_transaction_id("TXN_001")
        assert status.transaction_id == "TXN_001"
        assert status.refund_status.value == "not_requested"

    def test_get_by_transaction_id_not_found(self):
        from fintech_agent.repositories.supabase.supabase_refund_repo import (
            SupabaseRefundRepository,
        )

        client = _mock_client_with_table("refunds", [])
        repo = SupabaseRefundRepository(client)

        with pytest.raises(RecordNotFound, match="transaction_id"):
            repo.get_by_transaction_id("TXN_MISSING")


# ─── Train Provider Repository ──────────────────────────


class TestSupabaseTrainProviderRepo:
    def test_get_by_ref_id_found(self):
        from fintech_agent.repositories.supabase.supabase_train_provider_repo import (
            SupabaseTrainProviderRepository,
        )

        row = {
            "provider_ref_id": "TRAIN_REF_001",
            "status": "ticket_not_issued",
            "ticket_code": None,
        }
        client = _mock_client_with_table("train_provider_statuses", [row])
        repo = SupabaseTrainProviderRepository(client)

        result = repo.get_by_ref_id("TRAIN_REF_001")
        assert result.provider_ref_id == "TRAIN_REF_001"
        assert result.booking_status.value == "ticket_not_issued"

    def test_get_by_ref_id_not_found(self):
        from fintech_agent.repositories.supabase.supabase_train_provider_repo import (
            SupabaseTrainProviderRepository,
        )

        client = _mock_client_with_table("train_provider_statuses", [])
        repo = SupabaseTrainProviderRepository(client)

        with pytest.raises(RecordNotFound, match="provider_ref_id"):
            repo.get_by_ref_id("MISSING_REF")


# ─── Reconciliation Repository ──────────────────────────


class TestSupabaseReconciliationRepo:
    def test_get_by_transaction_id_found(self):
        from fintech_agent.repositories.supabase.supabase_reconciliation_repo import (
            SupabaseReconciliationRepository,
        )

        row = {
            "transaction_id": "TXN_BILL_002",
            "status": "wallet_provider_mismatch",
            "mismatch_type": "wallet_debited_provider_not_confirmed",
            "reconciliation_id": "RECON_001",
            "created_at": "2026-05-27T12:30:00Z",
        }
        client = _mock_client_with_table("reconciliation_cases", [row])
        repo = SupabaseReconciliationRepository(client)

        result = repo.get_by_transaction_id("TXN_BILL_002")
        assert result is not None
        assert result.status == "wallet_provider_mismatch"

    def test_get_by_transaction_id_not_found_returns_none(self):
        from fintech_agent.repositories.supabase.supabase_reconciliation_repo import (
            SupabaseReconciliationRepository,
        )

        client = _mock_client_with_table("reconciliation_cases", [])
        repo = SupabaseReconciliationRepository(client)

        result = repo.get_by_transaction_id("TXN_MISSING")
        assert result is None

"""Supabase merchant settlement repository — READ-ONLY.

Provides read access to merchant settlement tables:
  - merchants
  - merchant_bank_accounts
  - settlement_batches
  - merchant_settlement_ledgers
  - merchant_payouts
  - bank_transfer_receipts

Does NOT expose merchant_settlement_expected_tests.
Does NOT insert, update, or delete any records.
"""

from __future__ import annotations

import logging

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import (
    BankTransferReceipt,
    MerchantBankAccount,
    MerchantPayout,
    MerchantProfile,
    MerchantSettlementLedger,
    SettlementBatch,
)

_logger = logging.getLogger(__name__)


class SupabaseMerchantSettlementRepository:
    """Supabase-backed merchant settlement repository — READ-ONLY."""

    def __init__(self, client) -> None:
        self._client = client

    # ─── Merchant Profile ────────────────────────────────────

    def get_merchant_profile(
        self,
        *,
        merchant_id: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        tax_code: str | None = None,
    ) -> MerchantProfile | None:
        """Fetch merchant profile by identity (priority: merchant_id > phone > email > tax_code).

        Returns None if not found.
        """
        try:
            q = self._client.table("merchants").select("*")
            if merchant_id:
                q = q.eq("merchant_id", merchant_id)
            elif phone:
                q = q.eq("phone", phone)
            elif email:
                q = q.eq("contact_email", email)
            elif tax_code:
                q = q.eq("tax_code", tax_code)
            else:
                return None

            resp = q.limit(1).execute()
            if not resp.data:
                return None

            row = resp.data[0]
            return MerchantProfile(
                merchant_id=row["merchant_id"],
                merchant_name=row.get("merchant_name"),
                tax_code=row.get("tax_code"),
                contact_email=row.get("contact_email"),
                phone=row.get("phone"),
                status=row.get("status"),
                settlement_cycle=row.get("settlement_cycle"),
                bank_account_id=row.get("bank_account_id"),
                created_at=row.get("created_at"),
            )
        except Exception as e:
            _logger.exception("get_merchant_profile failed")
            return None

    # ─── Merchant Bank Account ───────────────────────────────

    def get_merchant_bank_account(self, merchant_id: str) -> MerchantBankAccount | None:
        """Fetch merchant bank account by merchant_id.

        Returns None if not found.
        """
        try:
            resp = (
                self._client.table("merchant_bank_accounts")
                .select("*")
                .eq("merchant_id", merchant_id)
                .limit(1)
                .execute()
            )
            if not resp.data:
                return None

            row = resp.data[0]
            return MerchantBankAccount(
                bank_account_id=row["bank_account_id"],
                merchant_id=row["merchant_id"],
                bank_code=row.get("bank_code"),
                bank_name=row.get("bank_name"),
                account_number=row.get("account_number"),
                account_holder_name=row.get("account_holder_name"),
                branch_name=row.get("branch_name"),
                verification_status=row.get("verification_status"),
                is_active=row.get("is_active"),
                failure_reason=row.get("failure_reason"),
                last_verified_at=row.get("last_verified_at"),
                updated_at=row.get("updated_at"),
            )
        except Exception as e:
            _logger.exception("get_merchant_bank_account failed for %s", merchant_id)
            return None

    # ─── Settlement Batch ────────────────────────────────────

    def get_settlement_batch(
        self,
        *,
        batch_id: str | None = None,
        settlement_date: str | None = None,
        cycle: str = "D+1",
    ) -> SettlementBatch | None:
        """Fetch settlement batch by batch_id or settlement_date + cycle.

        Returns None if not found.
        """
        try:
            q = self._client.table("settlement_batches").select("*")
            if batch_id:
                q = q.eq("batch_id", batch_id)
            elif settlement_date:
                q = q.eq("settlement_date", settlement_date).eq("cycle", cycle)
            else:
                # Fetch latest batch
                q = q.order("created_at", desc=True)

            resp = q.limit(1).execute()
            if not resp.data:
                return None

            row = resp.data[0]
            return SettlementBatch(
                batch_id=row["batch_id"],
                settlement_date=row.get("settlement_date"),
                cycle=row.get("cycle"),
                status=row.get("status"),
                total_merchants=row.get("total_merchants"),
                total_amount=row.get("total_amount"),
                started_at=row.get("started_at"),
                finished_at=row.get("finished_at"),
                failure_reason=row.get("failure_reason"),
                created_at=row.get("created_at"),
            )
        except Exception as e:
            _logger.exception("get_settlement_batch failed")
            return None

    # ─── Merchant Settlement Ledger ──────────────────────────

    def get_merchant_settlement_ledger(
        self,
        merchant_id: str,
        settlement_date: str | None = None,
    ) -> MerchantSettlementLedger | None:
        """Fetch merchant settlement ledger entry.

        If settlement_date is provided, fetch by merchant_id + settlement_date.
        Otherwise fetch the latest ledger entry for this merchant.

        Returns None if not found.
        """
        try:
            q = (
                self._client.table("merchant_settlement_ledgers")
                .select("*")
                .eq("merchant_id", merchant_id)
            )
            if settlement_date:
                q = q.eq("settlement_date", settlement_date)
            else:
                q = q.order("created_at", desc=True)

            resp = q.limit(1).execute()
            if not resp.data:
                return None

            row = resp.data[0]
            return MerchantSettlementLedger(
                ledger_id=row["ledger_id"],
                merchant_id=row["merchant_id"],
                settlement_date=row.get("settlement_date"),
                due_date=row.get("due_date"),
                gross_amount=row.get("gross_amount"),
                fee_amount=row.get("fee_amount"),
                refund_amount=row.get("refund_amount"),
                chargeback_amount=row.get("chargeback_amount"),
                net_settlement_amount=row.get("net_settlement_amount"),
                currency=row.get("currency"),
                status=row.get("status"),
                created_at=row.get("created_at"),
            )
        except Exception as e:
            _logger.exception("get_merchant_settlement_ledger failed for %s", merchant_id)
            return None

    # ─── Merchant Payout ─────────────────────────────────────

    def get_merchant_payout(
        self,
        merchant_id: str,
        *,
        settlement_date: str | None = None,
        payout_id: str | None = None,
    ) -> MerchantPayout | None:
        """Fetch merchant payout (priority: payout_id > merchant_id+date > latest).

        Returns None if not found.
        """
        try:
            q = self._client.table("merchant_payouts").select("*")
            if payout_id:
                q = q.eq("payout_id", payout_id)
            elif settlement_date:
                q = q.eq("merchant_id", merchant_id).eq("settlement_date", settlement_date)
            else:
                q = q.eq("merchant_id", merchant_id).order("created_at", desc=True)

            resp = q.limit(1).execute()
            if not resp.data:
                return None

            row = resp.data[0]
            return MerchantPayout(
                payout_id=row["payout_id"],
                batch_id=row.get("batch_id"),
                merchant_id=row["merchant_id"],
                settlement_date=row.get("settlement_date"),
                bank_account_id=row.get("bank_account_id"),
                amount=row.get("amount"),
                currency=row.get("currency"),
                status=row.get("status"),
                bank_transfer_ref=row.get("bank_transfer_ref"),
                failure_reason=row.get("failure_reason"),
                scheduled_date=row.get("scheduled_date"),
                executed_at=row.get("executed_at"),
                created_at=row.get("created_at"),
            )
        except Exception as e:
            _logger.exception("get_merchant_payout failed for %s", merchant_id)
            return None

    # ─── Bank Transfer Receipt ───────────────────────────────

    def get_bank_transfer_receipt(
        self,
        *,
        bank_transfer_ref: str | None = None,
        payout_id: str | None = None,
    ) -> BankTransferReceipt | None:
        """Fetch bank transfer receipt (priority: bank_transfer_ref > payout_id).

        Returns None if not found.
        """
        try:
            q = self._client.table("bank_transfer_receipts").select("*")
            if bank_transfer_ref:
                q = q.eq("bank_transfer_ref", bank_transfer_ref)
            elif payout_id:
                q = q.eq("payout_id", payout_id)
            else:
                return None

            resp = q.limit(1).execute()
            if not resp.data:
                return None

            row = resp.data[0]
            return BankTransferReceipt(
                receipt_id=row["receipt_id"],
                payout_id=row.get("payout_id"),
                bank_transfer_ref=row.get("bank_transfer_ref"),
                bank_status=row.get("bank_status"),
                unc_number=row.get("unc_number"),
                receipt_url=row.get("receipt_url"),
                sent_to_merchant=row.get("sent_to_merchant"),
                sent_at=row.get("sent_at"),
                created_at=row.get("created_at"),
            )
        except Exception as e:
            _logger.exception("get_bank_transfer_receipt failed")
            return None

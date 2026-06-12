"""Merchant settlement: the agent looks up the logged-in merchant's own
settlement/payout/bank-account data before asking for a payout id.

Offline/deterministic: the merchant settlement repository is mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fintech_agent.api.generic_resolver import no_match_message, resolve_case_evidence
from fintech_agent.llm.message_analyzer import ExtractedFields, MessageAnalysis


_MERCHANT_SESSION = {"subject_type": "merchant", "merchant_id": "MC1", "tax_code": "TX1"}


def _complaint(**extracted):
    return MessageAnalysis(
        message_type="new_complaint", workflow_hint="merchant_settlement_delay",
        extracted=ExtractedFields(**extracted),
    )


@pytest.fixture
def patch_merchant_repo(monkeypatch):
    def _install(*, payout=None, ledger=None, bank=None):
        repo = MagicMock()
        repo.get_merchant_payout.return_value = payout
        repo.get_merchant_settlement_ledger.return_value = ledger
        repo.get_merchant_bank_account.return_value = bank
        monkeypatch.setattr(
            "fintech_agent.database.repository_factory.get_merchant_settlement_repo",
            lambda *a, **k: repo,
        )
        return repo

    return _install


def _payout(status, payout_id="PO_1", account_number="1234567890"):
    return SimpleNamespace(
        payout_id=payout_id, merchant_id="MC1", status=status,
        amount=5_000_000, bank_transfer_ref="REF", failure_reason="x",
    )


def _ledger(status="pending"):
    return SimpleNamespace(merchant_id="MC1", status=status, gross_amount=5_000_000)


def _bank(verification_status):
    return SimpleNamespace(
        merchant_id="MC1", verification_status=verification_status,
        account_number="1234567890", bank_name="VCB",
    )


# ─── Identity-driven lookup, no interrogation ────────────────────

def test_payout_pending_resolved_from_merchant_id(patch_merchant_repo):
    patch_merchant_repo(payout=_payout("failed"), ledger=_ledger("pending"))
    res = resolve_case_evidence(_MERCHANT_SESSION, {}, _complaint())
    assert res.resolution_status == "resolved"
    assert res.resolved_entity_id == "MC1"        # trusted session identity
    assert res.public_safe_evidence["payout_status"] == "failed"
    assert res.public_safe_evidence["payment_status"] == "success"  # settlement recorded
    assert res.missing_info == []                 # does not ask for a payout id first


def test_invalid_bank_account_surfaced(patch_merchant_repo):
    patch_merchant_repo(bank=_bank("rejected"), ledger=_ledger("pending"))
    res = resolve_case_evidence(_MERCHANT_SESSION, {}, _complaint())
    assert res.resolution_status == "resolved"
    assert res.public_safe_evidence["bank_account_status"] == "rejected"


def test_no_settlement_record_is_no_match(patch_merchant_repo):
    patch_merchant_repo()  # nothing on the account
    res = resolve_case_evidence(_MERCHANT_SESSION, {}, _complaint())
    assert res.resolution_status == "no_match"
    assert res.public_response == no_match_message("merchant_settlement_delay")
    assert "settlement" in res.public_response.lower()


def test_evidence_never_exposes_raw_account_or_payout_id(patch_merchant_repo):
    patch_merchant_repo(
        payout=_payout("failed", payout_id="PO_SECRET", account_number="9999"),
        ledger=_ledger("pending"), bank=_bank("rejected"),
    )
    res = resolve_case_evidence(_MERCHANT_SESSION, {}, _complaint())
    ev = res.public_safe_evidence
    assert "PO_SECRET" not in str(ev)
    assert "9999" not in str(ev) and "1234567890" not in str(ev)
    # Only public-safe status labels are exposed.
    assert set(ev.keys()) <= {"payment_status", "payout_status", "settlement_status", "bank_account_status"}


def test_invalid_merchant_session_is_rejected(patch_merchant_repo):
    res = resolve_case_evidence({"subject_type": "merchant"}, {}, _complaint())  # no merchant_id
    assert res.resolution_status == "invalid_session"

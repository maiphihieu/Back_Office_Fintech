"""Tests for account-data discovery logic.

Validates that:
  1. Logged-in topup account + "tôi nạp tiền nhưng ví không nhận" → proactive scan
     finds pending transaction, bot answers from verified data.
  2. No amount/transaction_id provided → still discovers pending transaction.
  3. Account without wallet_topup issue → honest "no match" with correct wording.
  4. Wrong amount → amount_mismatch detection with claim vs verified.
  5. Identity resolution trace present in all responses.
  6. Scan repo error → evidence_error, NOT no_match.

NO hard-coded phrase, phone, user_id, amount, transaction_id, or specific case.
All test data is constructed dynamically.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from fintech_agent.api.generic_resolver import (
    ResolutionResult,
    AccountDiscoveryResult,
    IdentityTrace,
    discover_account_issues,
    resolve_case_evidence,
    _find_problematic_user_transactions,
    _build_identity_trace,
    no_match_message,
)
from fintech_agent.llm.message_analyzer import (
    MessageAnalysis,
    ExtractedFields,
    _fallback_analyze,
)


# ─── Helpers ────────────────────────────────────────────────────

_NOW = datetime.now(tz=timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)


def _make_txn(**kwargs):
    """Build a mock transaction with dot-attribute access."""
    return SimpleNamespace(
        transaction_id=kwargs.get("transaction_id", "TXN_TEST_001"),
        user_id=kwargs.get("user_id", "U_TEST_001"),
        service_type=kwargs.get("service_type", "wallet_topup"),
        amount=kwargs.get("amount", 500000),
        status=kwargs.get("status", "pending"),
        order_id=kwargs.get("order_id"),
        bill_code=kwargs.get("bill_code"),
        customer_code=kwargs.get("customer_code"),
        provider_ref_id=kwargs.get("provider_ref_id"),
        created_at=kwargs.get("created_at", _NOW),
        bank_code=kwargs.get("bank_code"),
        bank_reference=kwargs.get("bank_reference"),
    )


def _make_session(**kwargs):
    """Build a mock session dict."""
    return {
        "session_id": kwargs.get("session_id", "demo_test_topup"),
        "subject_type": kwargs.get("subject_type", "wallet_user"),
        "display_name": kwargs.get("display_name", "Test User"),
        "role": kwargs.get("role", "customer"),
        "is_authenticated": kwargs.get("is_authenticated", True),
        "user_id": kwargs.get("user_id", "U_TEST_001"),
        "wallet_id": kwargs.get("wallet_id", "WALLET_TEST_001"),
        "phone": kwargs.get("phone", "0980000001"),
        "email": kwargs.get("email", "test@example.com"),
    }


def _mock_txn_repo(transactions):
    """Create a mock transaction repo from a list of mock transactions."""
    from fintech_agent.repositories.base import RecordNotFound

    repo = MagicMock()

    def _get_by_id(txn_id):
        for t in transactions:
            if t.transaction_id == txn_id:
                return t
        raise RecordNotFound("Transaction", "transaction_id", txn_id)

    def _get_by_user_id(user_id):
        return [t for t in transactions if t.user_id == user_id]

    repo.get_by_id.side_effect = _get_by_id
    repo.get_by_user_id.side_effect = _get_by_user_id
    repo.find_by_user_id = repo.get_by_user_id
    return repo


def _patch_repo(txn_repo):
    """Patch transaction repo at the factory level (where resolver imports it)."""
    return patch(
        "fintech_agent.database.repository_factory.get_transaction_repo",
        return_value=txn_repo,
    )


# ─── Test 1: Proactive discovery on topup demo ─────────────────

class TestProactiveDiscoveryTopup:
    """Logged-in topup account says 'nạp tiền nhưng ví không nhận'.
    Bot must find the pending transaction WITHOUT amount/transaction_id."""

    def test_proactive_scan_finds_pending_topup(self):
        """No search criteria → proactive scan finds the single pending txn."""
        txn = _make_txn(status="pending", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "tôi nạp tiền nhưng ví không nhận", {}, {},
        )
        assert analysis.workflow_hint == "wallet_topup"

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "resolved"
        assert result.resolved_entity_id == txn.transaction_id
        assert result.verified_amount == txn.amount
        assert result.verified_owner_id == session["user_id"]

    def test_no_amount_still_discovers(self):
        """Customer says 'ví chưa nhận được tiền' without any amount."""
        txn = _make_txn(status="pending", amount=300000)
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "ví chưa nhận được tiền nạp", {}, {},
        )
        # The message has no amount, no txn_id
        assert analysis.extracted.amount is None
        assert analysis.extracted.transaction_id is None

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "resolved"
        assert result.verified_amount == 300000

    def test_failed_status_also_discovered(self):
        """A 'failed' transaction also needs attention and should be found."""
        txn = _make_txn(status="failed", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "giao dịch nạp tiền bị lỗi", {}, {},
        )

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "resolved"
        assert result.verified_status == "failed"


# ─── Test 2: No amount/transaction_id required ─────────────────

class TestNoIdRequired:
    """Bot must NOT ask for amount or transaction_id before scanning."""

    def test_does_not_ask_for_transaction_id(self):
        """When proactive scan resolves, missing_info must NOT include transaction_id."""
        txn = _make_txn(status="pending")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "nạp tiền mà ví không nhận", {}, {},
        )

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "resolved"
        assert "transaction_id" not in (result.missing_info or [])


# ─── Test 3: Account without issue → honest no_match ───────────

class TestAccountWithoutIssue:
    """Account has completed topup transactions (no issues) →
    bot says 'current data does not show issue'."""

    def test_completed_topup_no_issue(self):
        """All transactions completed → no_match, no false positive."""
        txn = _make_txn(status="completed", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "tôi nạp tiền nhưng ví chưa cộng", {}, {},
        )

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "no_match"
        assert "chưa tìm thấy" in result.public_response

    def test_empty_account_no_match(self):
        """Account has zero transactions for this workflow → no_match."""
        session = _make_session()
        repo = _mock_txn_repo([])  # empty

        analysis = _fallback_analyze(
            "tôi nạp tiền nhưng ví không nhận", {}, {},
        )

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "no_match"


# ─── Test 4: Wrong amount → amount_mismatch ────────────────────

class TestAmountMismatch:
    """Customer claims 1M, account has 500K pending → amount_mismatch."""

    def test_mismatch_detected(self):
        """Claimed amount differs from verified → amount_mismatch."""
        txn = _make_txn(status="pending", amount=500000)
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "tôi nạp 1 triệu đồng nhưng ví chưa nhận", {}, {},
        )
        assert analysis.extracted.amount == 1_000_000

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "amount_mismatch"
        assert result.claimed_amount == 1_000_000
        assert result.verified_amount == 500_000


# ─── Test 5: Identity trace present ────────────────────────────

class TestIdentityTrace:
    """All resolver responses must include identity_trace when session exists."""

    def test_resolved_has_identity_trace(self):
        """Resolved result includes identity trace."""
        txn = _make_txn(status="pending")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "tôi nạp tiền nhưng ví không nhận", {}, {},
        )

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.identity_trace is not None
        assert result.identity_trace["identity_found"] is True
        assert result.identity_trace["resolved_user_id"] == session["user_id"]
        assert result.identity_trace["subject_type"] == "wallet_user"

    def test_no_match_has_identity_trace(self):
        """No-match result also includes identity trace."""
        session = _make_session()
        repo = _mock_txn_repo([])

        analysis = _fallback_analyze(
            "tôi nạp tiền nhưng ví chưa nhận", {}, {},
        )

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.identity_trace is not None
        assert result.identity_trace["identity_found"] is True

    def test_discovery_result_present(self):
        """Discovery result is populated in the no-criteria path."""
        txn = _make_txn(status="pending")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        analysis = _fallback_analyze(
            "ví chưa nhận tiền nạp", {}, {},
        )

        with _patch_repo(repo):
            result = resolve_case_evidence(session, None, analysis)

        assert result.discovery_result is not None
        assert result.discovery_result["account_data_found"] is True
        assert result.discovery_result["issue_found"] is True
        assert result.discovery_result["scan_error"] is False

    def test_build_identity_trace_helper(self):
        """_build_identity_trace produces correct dict from session."""
        session = _make_session(
            session_id="demo_x", user_id="U_123", wallet_id="W_123",
        )
        trace = _build_identity_trace(session)
        assert trace["session_id"] == "demo_x"
        assert trace["resolved_user_id"] == "U_123"
        assert trace["resolved_wallet_id"] == "W_123"
        assert trace["identity_found"] is True
        assert trace["source_table"] == "mock_sessions"

    def test_empty_user_id_identity_not_found(self):
        """Empty user_id → identity_found=False."""
        session = _make_session(user_id="")
        trace = _build_identity_trace(session)
        assert trace["identity_found"] is False


# ─── Test 6: Scan error → evidence_error, NOT no_match ────────

class TestScanErrorGuardrail:
    """If the transaction repo throws, resolver must NOT return no_match."""

    def test_repo_exception_returns_evidence_error(self):
        """Repo raises → evidence_error status (not no_match)."""
        session = _make_session()

        analysis = _fallback_analyze(
            "tôi nạp tiền nhưng ví không nhận", {}, {},
        )

        # Patch the factory to raise an exception
        with patch(
            "fintech_agent.database.repository_factory.get_transaction_repo",
            side_effect=RuntimeError("DB connection lost"),
        ):
            result = resolve_case_evidence(session, None, analysis)

        assert result.resolution_status == "evidence_error"
        assert result.resolution_status != "no_match"
        assert "thử lại" in result.public_response

    def test_scan_error_has_discovery_result(self):
        """Scan error includes discovery_result with scan_error=True."""
        session = _make_session()

        analysis = _fallback_analyze(
            "nạp tiền chưa nhận", {}, {},
        )

        with patch(
            "fintech_agent.database.repository_factory.get_transaction_repo",
            side_effect=RuntimeError("DB connection lost"),
        ):
            result = resolve_case_evidence(session, None, analysis)

        assert result.discovery_result is not None
        assert result.discovery_result["scan_error"] is True

    def test_find_problematic_returns_scan_failed(self):
        """_find_problematic_user_transactions returns ([], False) on repo error."""
        with patch(
            "fintech_agent.database.repository_factory.get_transaction_repo",
            side_effect=RuntimeError("boom"),
        ):
            found, scan_ok = _find_problematic_user_transactions(
                "U_ANY", "wallet_topup",
            )

        assert found == []
        assert scan_ok is False


# ─── Test 7: discover_account_issues API ───────────────────────

class TestDiscoverAccountIssues:
    """Tests for the generic discover_account_issues function."""

    def test_wallet_user_with_issue(self):
        """wallet_user with pending topup → issue_found=True."""
        txn = _make_txn(status="pending", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        with _patch_repo(repo):
            result = discover_account_issues(session, "wallet_topup")

        assert result.account_data_found is True
        assert result.issue_found is True
        assert len(result.problematic_records) == 1
        assert result.scan_error is False

    def test_wallet_user_no_issue(self):
        """wallet_user with completed topup → issue_found=False."""
        txn = _make_txn(status="completed", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])

        with _patch_repo(repo):
            result = discover_account_issues(session, "wallet_topup")

        assert result.account_data_found is True
        assert result.issue_found is False
        assert len(result.problematic_records) == 0

    def test_wallet_user_empty_account(self):
        """wallet_user with no transactions → account_data_found=False."""
        session = _make_session()
        repo = _mock_txn_repo([])

        with _patch_repo(repo):
            result = discover_account_issues(session, "wallet_topup")

        assert result.account_data_found is False
        assert result.issue_found is False

    def test_scan_error_flagged(self):
        """Repo error → scan_error=True, issue_found=False."""
        session = _make_session()

        with patch(
            "fintech_agent.database.repository_factory.get_transaction_repo",
            side_effect=RuntimeError("DB down"),
        ):
            result = discover_account_issues(session, "wallet_topup")

        assert result.scan_error is True
        assert result.issue_found is False
        assert result.reason == "scan_failed_repo_error"

    def test_no_user_id_returns_identity_not_resolved(self):
        """Session without user_id → reason='identity_not_resolved'."""
        session = _make_session(user_id="")

        result = discover_account_issues(session, "wallet_topup")

        assert result.account_data_found is False
        assert result.reason == "identity_not_resolved"

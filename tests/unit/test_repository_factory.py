"""Tests for the repository factory — verifies correct dispatch logic."""

import os
from unittest.mock import MagicMock, patch

import pytest

from fintech_agent.config import Settings
from fintech_agent.database.repository_factory import (
    get_ledger_repo,
    get_reconciliation_repo,
    get_refund_repo,
    get_train_provider_repo,
    get_transaction_repo,
    get_utility_provider_repo,
    reset_factory,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset factory cache between tests."""
    reset_factory()
    yield
    reset_factory()


def _settings(enabled: bool = False, url: str = "", key: str = "") -> Settings:
    """Create a Settings instance with Supabase config."""
    return Settings(
        supabase_enabled=enabled,
        supabase_url=url,
        supabase_key=key,
        supabase_schema="public",
        _env_file=None,  # Don't read real .env
    )


# ─── JSON fallback tests ──────────────────────────────────


class TestJsonFallback:
    """When SUPABASE_ENABLED=false, factory should return JSON repos."""

    def test_transaction_repo_json(self):
        from fintech_agent.repositories.transaction_repository import TransactionRepository

        repo = get_transaction_repo(_settings(enabled=False))
        assert isinstance(repo, TransactionRepository)

    def test_ledger_repo_json(self):
        from fintech_agent.repositories.ledger_repository import LedgerRepository

        repo = get_ledger_repo(_settings(enabled=False))
        assert isinstance(repo, LedgerRepository)

    def test_train_provider_repo_json(self):
        from fintech_agent.repositories.provider_repository import TrainProviderRepository

        repo = get_train_provider_repo(_settings(enabled=False))
        assert isinstance(repo, TrainProviderRepository)

    def test_utility_provider_repo_json(self):
        from fintech_agent.repositories.provider_repository import UtilityProviderRepository

        repo = get_utility_provider_repo(_settings(enabled=False))
        assert isinstance(repo, UtilityProviderRepository)

    def test_refund_repo_json(self):
        from fintech_agent.repositories.refund_repository import RefundRepository

        repo = get_refund_repo(_settings(enabled=False))
        assert isinstance(repo, RefundRepository)

    def test_reconciliation_repo_json(self):
        from fintech_agent.repositories.reconciliation_repository import ReconciliationRepository

        repo = get_reconciliation_repo(_settings(enabled=False))
        assert isinstance(repo, ReconciliationRepository)


# ─── Supabase dispatch tests ──────────────────────────────


class TestSupabaseDispatch:
    """When SUPABASE_ENABLED=true, factory should return Supabase repos."""

    @patch("fintech_agent.database.repository_factory._get_sb_client")
    def test_transaction_repo_supabase(self, mock_client):
        mock_client.return_value = MagicMock()
        from fintech_agent.repositories.supabase.supabase_transaction_repo import (
            SupabaseTransactionRepository,
        )

        repo = get_transaction_repo(
            _settings(enabled=True, url="https://x.supabase.co", key="test-key")
        )
        assert isinstance(repo, SupabaseTransactionRepository)

    @patch("fintech_agent.database.repository_factory._get_sb_client")
    def test_ledger_repo_supabase(self, mock_client):
        mock_client.return_value = MagicMock()
        from fintech_agent.repositories.supabase.supabase_ledger_repo import (
            SupabaseLedgerRepository,
        )

        repo = get_ledger_repo(
            _settings(enabled=True, url="https://x.supabase.co", key="test-key")
        )
        assert isinstance(repo, SupabaseLedgerRepository)

    @patch("fintech_agent.database.repository_factory._get_sb_client")
    def test_refund_repo_supabase(self, mock_client):
        mock_client.return_value = MagicMock()
        from fintech_agent.repositories.supabase.supabase_refund_repo import (
            SupabaseRefundRepository,
        )

        repo = get_refund_repo(
            _settings(enabled=True, url="https://x.supabase.co", key="test-key")
        )
        assert isinstance(repo, SupabaseRefundRepository)


# ─── Config validation tests ──────────────────────────────


class TestConfigValidation:
    """Enabled but missing URL/key should raise."""

    def test_enabled_missing_url(self):
        with pytest.raises(ValueError, match="SUPABASE_URL"):
            _settings(enabled=True, url="", key="test-key")

    def test_enabled_missing_key(self):
        with pytest.raises(ValueError, match="SUPABASE_KEY"):
            _settings(enabled=True, url="https://x.supabase.co", key="")

    def test_enabled_missing_both(self):
        with pytest.raises(ValueError, match="SUPABASE_URL"):
            _settings(enabled=True, url="", key="")

    def test_disabled_missing_ok(self):
        """When disabled, missing URL/key is fine."""
        s = _settings(enabled=False, url="", key="")
        assert s.supabase_enabled is False

"""Repository factory — returns JSON or Supabase repos based on config.

Usage:
    from fintech_agent.database.repository_factory import get_transaction_repo
    repo = get_transaction_repo()  # automatically picks JSON or Supabase

Tools and nodes should use these factories, never instantiate repos directly.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from fintech_agent.config import Settings, get_settings

_logger = logging.getLogger(__name__)

# Cache the Supabase client across factory calls
_supabase_client = None


def _get_sb_client(settings: Settings):
    """Get or create the shared Supabase client."""
    global _supabase_client
    if _supabase_client is None:
        from fintech_agent.database.supabase_client import get_supabase_client
        _supabase_client = get_supabase_client(settings)
    return _supabase_client


def get_transaction_repo(settings: Settings | None = None):
    """Get TransactionRepository (JSON or Supabase)."""
    s = settings or get_settings()
    if s.supabase_enabled:
        from fintech_agent.repositories.supabase.supabase_transaction_repo import (
            SupabaseTransactionRepository,
        )
        return SupabaseTransactionRepository(_get_sb_client(s))
    from fintech_agent.repositories.transaction_repository import TransactionRepository
    return TransactionRepository()


def get_ledger_repo(settings: Settings | None = None):
    """Get LedgerRepository (JSON or Supabase)."""
    s = settings or get_settings()
    if s.supabase_enabled:
        from fintech_agent.repositories.supabase.supabase_ledger_repo import (
            SupabaseLedgerRepository,
        )
        return SupabaseLedgerRepository(_get_sb_client(s))
    from fintech_agent.repositories.ledger_repository import LedgerRepository
    return LedgerRepository()


def get_train_provider_repo(settings: Settings | None = None):
    """Get TrainProviderRepository (JSON or Supabase)."""
    s = settings or get_settings()
    if s.supabase_enabled:
        from fintech_agent.repositories.supabase.supabase_train_provider_repo import (
            SupabaseTrainProviderRepository,
        )
        return SupabaseTrainProviderRepository(_get_sb_client(s))
    from fintech_agent.repositories.provider_repository import TrainProviderRepository
    return TrainProviderRepository()


def get_utility_provider_repo(settings: Settings | None = None):
    """Get UtilityProviderRepository (JSON or Supabase)."""
    s = settings or get_settings()
    if s.supabase_enabled:
        from fintech_agent.repositories.supabase.supabase_utility_provider_repo import (
            SupabaseUtilityProviderRepository,
        )
        return SupabaseUtilityProviderRepository(_get_sb_client(s))
    from fintech_agent.repositories.provider_repository import UtilityProviderRepository
    return UtilityProviderRepository()


def get_refund_repo(settings: Settings | None = None):
    """Get RefundRepository (JSON or Supabase)."""
    s = settings or get_settings()
    if s.supabase_enabled:
        from fintech_agent.repositories.supabase.supabase_refund_repo import (
            SupabaseRefundRepository,
        )
        return SupabaseRefundRepository(_get_sb_client(s))
    from fintech_agent.repositories.refund_repository import RefundRepository
    return RefundRepository()


def get_reconciliation_repo(settings: Settings | None = None):
    """Get ReconciliationRepository (JSON or Supabase)."""
    s = settings or get_settings()
    if s.supabase_enabled:
        from fintech_agent.repositories.supabase.supabase_reconciliation_repo import (
            SupabaseReconciliationRepository,
        )
        return SupabaseReconciliationRepository(_get_sb_client(s))
    from fintech_agent.repositories.reconciliation_repository import ReconciliationRepository
    return ReconciliationRepository()

def get_account_repo(settings: Settings | None = None):
    """Get AccountRepository (Supabase only — no JSON fallback).

    Use case 2: Account locked by Fraud Detection.
    """
    s = settings or get_settings()
    from fintech_agent.repositories.supabase.supabase_account_repo import (
        SupabaseAccountRepository,
    )
    return SupabaseAccountRepository(_get_sb_client(s))


def get_fraud_repo(settings: Settings | None = None):
    """Get FraudRepository (Supabase only — no JSON fallback).

    Use case 2: Account locked by Fraud Detection.
    """
    s = settings or get_settings()
    from fintech_agent.repositories.supabase.supabase_fraud_repo import (
        SupabaseFraudRepository,
    )
    return SupabaseFraudRepository(_get_sb_client(s))


def get_merchant_settlement_repo(settings: Settings | None = None):
    """Get MerchantSettlementRepository (Supabase only — no JSON fallback).

    Use case 3: Merchant settlement delay.
    """
    s = settings or get_settings()
    from fintech_agent.repositories.supabase.supabase_merchant_settlement_repo import (
        SupabaseMerchantSettlementRepository,
    )
    return SupabaseMerchantSettlementRepository(_get_sb_client(s))


def get_mock_session_repo(settings: Settings | None = None):
    """Get MockSessionRepository (Supabase only — no JSON fallback).

    Mock customer sessions for demo login.
    """
    s = settings or get_settings()
    from fintech_agent.repositories.supabase.supabase_mock_session_repo import (
        SupabaseMockSessionRepository,
    )
    return SupabaseMockSessionRepository(_get_sb_client(s))


def reset_factory() -> None:
    """Reset cached client (for testing)."""
    global _supabase_client
    _supabase_client = None



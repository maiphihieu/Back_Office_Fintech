"""Data access layer — repository pattern.

All repositories provide typed Pydantic models and raise RecordNotFound
on missing records (except ReconciliationRepository which returns None).

Repositories:
    TransactionRepository      — keyed by transaction_id
    LedgerRepository           — keyed by transaction_id (source of truth for money)
    TrainProviderRepository    — keyed by provider_ref_id
    UtilityProviderRepository  — keyed by provider_ref_id
    RefundRepository           — keyed by transaction_id
    ReconciliationRepository   — keyed by transaction_id (returns None if missing)
    CaseRepository             — in-memory store for case state lifecycle
"""

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.repositories.case_repository import CaseRepository
from fintech_agent.repositories.ledger_repository import LedgerRepository
from fintech_agent.repositories.provider_repository import (
    TrainProviderRepository,
    UtilityProviderRepository,
)
from fintech_agent.repositories.reconciliation_repository import ReconciliationRepository
from fintech_agent.repositories.refund_repository import RefundRepository
from fintech_agent.repositories.transaction_repository import TransactionRepository

__all__ = [
    "RecordNotFound",
    "CaseRepository",
    "LedgerRepository",
    "TrainProviderRepository",
    "UtilityProviderRepository",
    "ReconciliationRepository",
    "RefundRepository",
    "TransactionRepository",
]

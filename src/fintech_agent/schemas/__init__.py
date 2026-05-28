"""Pydantic schemas (data contracts) for the fintech agent.

Usage:
    from fintech_agent.schemas import CaseState, Transaction, ActionType
    from fintech_agent.schemas.enums import ServiceType
    from fintech_agent.schemas.evidence import EvidenceBundle
"""

# --- Enums ---
from fintech_agent.schemas.enums import (
    ActionType,
    ApprovalStatus,
    AuditEventType,
    CaseStatus,
    IssueType,
    ProviderStatusValue,
    RefundStatusValue,
    RiskLevel,
    ServiceType,
    WalletLedgerStatus,
)

# --- Evidence ---
from fintech_agent.schemas.evidence import (
    EvidenceBundle,
    EvidenceConflict,
    ReconciliationStatus,
    RefundStatus,
    TrainProviderStatus,
    Transaction,
    UtilityProviderStatus,
    WalletLedger,
    WalletLedgerEntry,
)

# --- Actions ---
from fintech_agent.schemas.actions import (
    ReconciliationTicketDraft,
    RecommendedAction,
    RefundRequestDraft,
)

# --- Approval ---
from fintech_agent.schemas.approval import ApprovalDecision, ApprovalPacket

# --- Audit ---
from fintech_agent.schemas.audit import AuditEvent

# --- Case State ---
from fintech_agent.schemas.case_state import CaseState, ExtractedInfo

__all__ = [
    # Enums
    "ActionType",
    "ApprovalStatus",
    "AuditEventType",
    "CaseStatus",
    "IssueType",
    "ProviderStatusValue",
    "RefundStatusValue",
    "RiskLevel",
    "ServiceType",
    "WalletLedgerStatus",
    # Evidence
    "EvidenceBundle",
    "EvidenceConflict",
    "ReconciliationStatus",
    "RefundStatus",
    "TrainProviderStatus",
    "Transaction",
    "UtilityProviderStatus",
    "WalletLedger",
    "WalletLedgerEntry",
    # Actions
    "RecommendedAction",
    "RefundRequestDraft",
    "ReconciliationTicketDraft",
    # Approval
    "ApprovalPacket",
    "ApprovalDecision",
    # Audit
    "AuditEvent",
    # Case State
    "CaseState",
    "ExtractedInfo",
]

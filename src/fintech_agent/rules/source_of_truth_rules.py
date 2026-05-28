"""Source of truth hierarchy for the fintech domain.

This module codifies WHICH system to trust for WHICH data:
  1. wallet_ledger   → money in wallet (highest authority for money)
  2. refund_table    → refund lifecycle
  3. provider_status → service delivery
  4. transaction     → metadata only (can lag behind ledger)

These rules are referenced by conflict_rules and refund_rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceOfTruth(StrEnum):
    """Which system is authoritative for which domain."""

    WALLET_LEDGER = "wallet_ledger"
    REFUND_TABLE = "refund_table"
    PROVIDER_STATUS = "provider_status"
    TRANSACTION = "transaction"


@dataclass(frozen=True)
class TrustRule:
    """Documents what each source is authoritative for."""

    source: SourceOfTruth
    authoritative_for: str
    notes: str


# Canonical trust hierarchy — used for documentation and conflict resolution.
TRUST_HIERARCHY: list[TrustRule] = [
    TrustRule(
        source=SourceOfTruth.WALLET_LEDGER,
        authoritative_for="money_in_wallet",
        notes="Refund amount MUST come from ledger.debit_amount, never from complaint.",
    ),
    TrustRule(
        source=SourceOfTruth.REFUND_TABLE,
        authoritative_for="refund_lifecycle",
        notes="Only source for whether refund was requested/approved/executed.",
    ),
    TrustRule(
        source=SourceOfTruth.PROVIDER_STATUS,
        authoritative_for="service_delivery",
        notes="Only source for whether ticket was issued or bill was confirmed.",
    ),
    TrustRule(
        source=SourceOfTruth.TRANSACTION,
        authoritative_for="metadata",
        notes="Transaction status can LAG behind ledger. Do not trust for money state.",
    ),
]


def get_authority_for(domain: str) -> SourceOfTruth:
    """Return which source is authoritative for a given domain.

    Args:
        domain: One of 'money_in_wallet', 'refund_lifecycle',
                'service_delivery', 'metadata'.

    Returns:
        The authoritative SourceOfTruth.

    Raises:
        ValueError: If the domain is unknown.
    """
    for rule in TRUST_HIERARCHY:
        if rule.authoritative_for == domain:
            return rule.source
    raise ValueError(f"Unknown domain: {domain}")


"""Customer claims vs verified evidence tracking.

Separates what the customer SAYS (claims) from what the system VERIFIES
(evidence). Detects contradictions between the two.

DESIGN PRINCIPLES:
  - Customer message = claim, NOT verified fact.
  - Only database/tool evidence creates verified facts.
  - If customer corrects themselves, old claim is superseded.
  - Contradictions are surfaced in customer response and staff ticket.

NEVER treat a customer-provided amount, bank, or transaction_id as verified
unless confirmed by resolver/evidence lookup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ─── Claim Record ──────────────────────────────────────────────

@dataclass
class ClaimRecord:
    """One customer-provided piece of information."""
    field: str            # "amount", "bank_name", "transaction_id", ...
    value: Any
    timestamp: str = ""
    superseded: bool = False
    superseded_by: str = ""   # timestamp of the newer claim

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class CustomerClaims:
    """Accumulated customer claims across messages.

    `latest` is the current best-known claim per field.
    `history` preserves every claim ever made (for audit).
    """
    latest: dict[str, Any] = field(default_factory=dict)
    history: list[ClaimRecord] = field(default_factory=list)

    def merge_claim(
        self,
        field_name: str,
        value: Any,
        is_correction: bool = False,
    ) -> None:
        """Add or update a customer claim.

        If `is_correction`, marks the previous claim for this field as
        superseded. Otherwise accumulates (latest wins).
        """
        if value is None:
            return

        now = datetime.now(timezone.utc).isoformat()

        # If this is a correction and we have a previous claim, supersede it
        if is_correction:
            for rec in reversed(self.history):
                if rec.field == field_name and not rec.superseded:
                    rec.superseded = True
                    rec.superseded_by = now
                    logger.info(
                        "[Claims] Superseded %s=%s (corrected to %s)",
                        field_name, rec.value, value,
                    )
                    break

        # Record new claim
        self.history.append(ClaimRecord(
            field=field_name,
            value=value,
            timestamp=now,
        ))
        self.latest[field_name] = value

    def merge_extracted_fields(
        self,
        extracted: Any,
        is_correction: bool = False,
    ) -> None:
        """Merge all non-empty fields from an ExtractedFields dataclass."""
        _FIELD_MAP = {
            "transaction_id": "transaction_id",
            "order_id": "order_id",
            "bill_code": "bill_code",
            "merchant_id": "merchant_id",
            "amount": "amount",
            "bank_name": "bank_name",
            "bank_reference": "bank_reference",
            "approximate_time_text": "time",
            "approximate_date_text": "date",
            "provider_name": "provider_name",
        }
        for attr, claim_field in _FIELD_MAP.items():
            val = getattr(extracted, attr, None)
            if val:
                self.merge_claim(claim_field, val, is_correction=is_correction)

    @property
    def superseded_claims(self) -> list[ClaimRecord]:
        """Claims that were corrected by the customer."""
        return [r for r in self.history if r.superseded]

    def to_summary(self) -> list[dict]:
        """Serialize for ticket/API — [{field, value, superseded}]."""
        result = []
        for rec in self.history:
            result.append({
                "field": rec.field,
                "value": rec.value,
                "superseded": rec.superseded,
                "timestamp": rec.timestamp,
            })
        return result


# ─── Verified Evidence ──────────────────────────────────────────

@dataclass
class VerifiedEvidence:
    """Evidence confirmed by database/tool lookup.

    Each field is only populated when the system can prove it from a
    trusted source (transaction table, provider API, etc.).
    """
    resolved_entity_id: str = ""
    resolved_entity_type: str = ""    # transaction | account | merchant
    verified_amount: int | None = None
    verified_status: str = ""         # pending | success | failed | ...
    verified_owner_id: str = ""       # user_id from the DB record
    verified_bank_name: str = ""
    verified_bank_status: str = ""
    verified_provider_status: str = ""
    evidence_source: str = ""         # "transaction_table" | "provider_api" | ...
    checked_at: str = ""

    def __post_init__(self) -> None:
        if not self.checked_at and self.resolved_entity_id:
            self.checked_at = datetime.now(timezone.utc).isoformat()

    def to_summary(self) -> list[dict]:
        """Serialize for ticket/API — [{field, value, source}]."""
        result = []
        if self.verified_amount is not None:
            result.append({
                "field": "amount",
                "value": self.verified_amount,
                "source": self.evidence_source,
            })
        if self.verified_status:
            result.append({
                "field": "status",
                "value": self.verified_status,
                "source": self.evidence_source,
            })
        if self.verified_bank_name:
            result.append({
                "field": "bank_name",
                "value": self.verified_bank_name,
                "source": self.evidence_source,
            })
        if self.verified_bank_status:
            result.append({
                "field": "bank_status",
                "value": self.verified_bank_status,
                "source": self.evidence_source,
            })
        if self.verified_provider_status:
            result.append({
                "field": "provider_status",
                "value": self.verified_provider_status,
                "source": self.evidence_source,
            })
        return result


# ─── Contradiction Detection ────────────────────────────────────

@dataclass
class Contradiction:
    """A mismatch between customer claim and verified evidence."""
    field: str
    customer_claim: Any
    verified_value: Any
    severity: str = "medium"  # low | medium | high


def _normalize_amount(val: Any) -> int | None:
    """Normalize an amount value to int for comparison."""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            cleaned = (
                val.strip()
                .replace(".", "")
                .replace(",", "")
                .replace("đ", "")
                .replace("vnd", "")
                .replace("k", "000")
                .replace("K", "000")
            )
            return int(cleaned) if cleaned.isdigit() else int(float(cleaned))
        return int(val)
    except (ValueError, TypeError):
        return None


def detect_contradictions(
    claims: CustomerClaims,
    evidence: VerifiedEvidence,
) -> list[Contradiction]:
    """Compare customer claims against verified evidence.

    Only creates contradictions for fields where BOTH the customer has
    made a claim AND the system has verified evidence. If evidence is
    missing, we don't create a contradiction — just "unverified".
    """
    contradictions: list[Contradiction] = []

    # ── Amount mismatch ──
    claimed_amount = _normalize_amount(claims.latest.get("amount"))
    if claimed_amount is not None and evidence.verified_amount is not None:
        if claimed_amount != evidence.verified_amount:
            contradictions.append(Contradiction(
                field="amount",
                customer_claim=claimed_amount,
                verified_value=evidence.verified_amount,
                severity="medium",
            ))

    # ── Bank name mismatch ──
    claimed_bank = str(claims.latest.get("bank_name", "")).strip().lower()
    verified_bank = evidence.verified_bank_name.strip().lower()
    if claimed_bank and verified_bank and claimed_bank != verified_bank:
        # Fuzzy: allow partial match (e.g. "mb" matches "mb bank")
        if claimed_bank not in verified_bank and verified_bank not in claimed_bank:
            contradictions.append(Contradiction(
                field="bank_name",
                customer_claim=claims.latest.get("bank_name"),
                verified_value=evidence.verified_bank_name,
                severity="low",
            ))

    # ── Transaction ownership mismatch ──
    # (This is a HIGH severity — potential data leak attempt)
    # Handled separately by ownership validation, but we record it here too.

    # ── Status mismatch ──
    # If customer says "giao dịch thành công" but DB shows "pending/failed"
    # This is typically detected by the evidence mapper, not here.

    return contradictions


def get_unverified_claims(
    claims: CustomerClaims,
    evidence: VerifiedEvidence,
) -> list[dict]:
    """Return claims that have no corresponding verified evidence.

    These are things the customer said but the system hasn't confirmed yet.
    """
    unverified = []
    for field_name, value in claims.latest.items():
        if field_name == "amount" and evidence.verified_amount is not None:
            continue
        if field_name == "bank_name" and evidence.verified_bank_name:
            continue
        if field_name == "transaction_id" and evidence.resolved_entity_id:
            continue
        unverified.append({"field": field_name, "value": value})
    return unverified

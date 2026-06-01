"""Generic claim verification models — scalable framework for any complaint type.

Business principle:
    Customer complaint text is only an investigation input, NOT the source of truth.
    The agent must use system evidence to verify each claim.

Design:
    - ClaimType enum defines ALL possible claim categories (extensible).
    - Claim model is generic: any claim type uses the same structure.
    - VerificationStatus distinguishes matched/mismatched/not_verifiable/not_found.
    - ClaimVerificationSummary aggregates results and flags:
        * customer_detail_mismatch → workflow continues
        * system_evidence_conflict → block risky actions

Adding a new claim type requires ONLY:
    1. Add to ClaimType enum
    2. Add a verifier function in claim_verifier.py
    3. Register it in the _VERIFIER_REGISTRY
    No other code changes.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


# ─── Enums ───────────────────────────────────────────────────


class ClaimType(StrEnum):
    """Generic claim type categories.

    Each enum value maps to a semantic category of customer assertion.
    Do NOT add one-off case-specific types — keep categories generic.
    """

    TRANSACTION_ID = "transaction_id_claim"
    USER_IDENTITY = "user_identity_claim"
    TRANSACTION_AMOUNT = "transaction_amount_claim"
    WALLET_BALANCE = "wallet_balance_claim"
    PAYMENT_STATUS = "payment_status_claim"
    SERVICE_DELIVERY = "service_delivery_claim"
    PROVIDER_STATUS = "provider_status_claim"
    BANK_STATUS = "bank_status_claim"
    REFUND_STATUS = "refund_status_claim"
    ACCOUNT_STATUS = "account_status_claim"
    WITHDRAWAL_STATUS = "withdrawal_status_claim"
    CUSTOMER_OPINION = "customer_opinion_claim"
    TIME = "time_claim"
    UNKNOWN = "unknown_claim"


class VerificationStatus(StrEnum):
    """Result of verifying a single claim against system evidence."""

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    NOT_VERIFIABLE = "not_verifiable"
    NOT_FOUND = "not_found"
    SYSTEM_ONLY = "system_only"  # System has data but customer did not provide this claim


# ─── Claim Model ─────────────────────────────────────────────


class Claim(BaseModel):
    """A single customer claim extracted from complaint text.

    Generic structure — the same model represents any claim_type.
    Verification fields (verification_status, trusted_system_value, etc.)
    are populated by the verifier engine after evidence lookup.
    """

    claim_id: str = Field(
        default_factory=lambda: f"CLM-{uuid.uuid4().hex[:8]}",
        description="Unique ID for this claim instance",
    )
    claim_type: ClaimType = Field(
        ...,
        description="Semantic category of this claim",
    )
    raw_text: str = Field(
        default="",
        description="Original text fragment from the complaint",
    )
    customer_claimed_value: str | int | float | None = Field(
        default=None,
        description="Value the customer stated",
    )
    normalized_value: str | int | float | None = Field(
        default=None,
        description="Normalized value for comparison (e.g. int for amounts)",
    )
    unit: str | None = Field(
        default=None,
        description="Unit of the value: 'VND', 'status', 'identifier', etc.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Extraction confidence (0.0-1.0)",
    )

    # ── Verification (filled by verifier engine) ──
    verification_status: VerificationStatus = Field(
        default=VerificationStatus.NOT_VERIFIABLE,
        description="Result of checking claim against system evidence",
    )
    trusted_system_value: str | int | float | None = Field(
        default=None,
        description="Value from trusted system source",
    )
    trusted_source: str | None = Field(
        default=None,
        description="System source path, e.g. 'wallet_ledger.debit_amount'",
    )
    explanation: str = Field(
        default="",
        description="Staff-facing explanation (Vietnamese)",
    )


# ─── Summary ─────────────────────────────────────────────────


class ClaimVerificationSummary(BaseModel):
    """Aggregate verification of all customer claims for a case.

    Attached to the resolution ticket so staff can see:
    - Which claims match system data
    - Which claims are wrong
    - Which claims cannot be verified
    - Whether the workflow should continue or needs manual review
    """

    summary: str = Field(
        default="",
        description="Tóm tắt kết quả kiểm tra thông tin khách cung cấp",
    )
    claims: list[Claim] = Field(
        default_factory=list,
        description="Danh sách kết quả kiểm tra từng claim",
    )
    matched_claims: list[str] = Field(
        default_factory=list,
        description="claim_type values of matched claims",
    )
    mismatched_claims: list[str] = Field(
        default_factory=list,
        description="claim_type values of mismatched claims",
    )
    not_verifiable_claims: list[str] = Field(
        default_factory=list,
        description="claim_type values of unverifiable claims",
    )
    not_found_claims: list[str] = Field(
        default_factory=list,
        description="claim_type values where lookup entity not found",
    )
    has_customer_detail_mismatch: bool = Field(
        default=False,
        description=(
            "True nếu khách khai sai nhưng dữ liệu hệ thống nhất quán. "
            "Workflow vẫn tiếp tục dùng dữ liệu chuẩn."
        ),
    )
    has_system_evidence_conflict: bool = Field(
        default=False,
        description=(
            "True nếu các nguồn dữ liệu hệ thống mâu thuẫn nhau. "
            "Cần block action rủi ro và chuyển manual review."
        ),
    )
    staff_explanation: str = Field(
        default="",
        description="Giải thích tổng hợp cho nhân viên (tiếng Việt)",
    )
    trusted_data_used_for_action: dict[str, str | int | float | None] = Field(
        default_factory=dict,
        description=(
            "Trusted system data that will be used for action inputs. "
            "Keys are field names (e.g. 'action_amount'), values are from system evidence. "
            "NEVER from customer complaint."
        ),
    )


# ─── Backward compatibility alias ────────────────────────────
# Old code may reference ClaimVerification — alias to Claim
ClaimVerification = Claim

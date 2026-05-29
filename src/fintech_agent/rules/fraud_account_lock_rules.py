"""Fraud account lock workflow decision rules — deterministic, no LLM.

Use case: "Tài khoản bị khóa do Fraud Detection tự động."

Decision matrix:
┌────────────────────────┬────────────────┬────────────────────────────────────────────────┐
│ Condition              │ Risk Score     │ Action                                          │
├────────────────────────┼────────────────┼────────────────────────────────────────────────┤
│ missing evidence       │ *              │ manual_review                                   │
│ account not locked     │ *              │ draft_customer_response                          │
│ false positive         │ < 50           │ create_unlock_account_draft (approval required)  │
│ fraud likely           │ >= 70 or flags │ create_request_documents_response_draft           │
│ inconclusive           │ 50-69          │ manual_review                                   │
└────────────────────────┴────────────────┴────────────────────────────────────────────────┘

SAFETY: No action in this rule engine unlocks accounts or modifies account_status.
All actions are DRAFT-ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass

from fintech_agent.schemas.enums import ActionType
from fintech_agent.schemas.evidence import (
    AccountStatus,
    EvidenceBundle,
    FraudCase,
)


@dataclass(frozen=True)
class FraudAccountLockDecision:
    """Result of fraud account lock workflow decision."""

    action: ActionType
    diagnosis: str
    approval_required: bool


def decide_fraud_account_lock(
    account_status: AccountStatus | None,
    fraud_case: FraudCase | None,
    evidence: EvidenceBundle,
) -> FraudAccountLockDecision:
    """Apply fraud account lock decision matrix.

    Args:
        account_status: Account record (lock state, balance).
        fraud_case: Fraud case record (risk score, signals).
        evidence: Full evidence bundle (for conflict check).

    Returns:
        FraudAccountLockDecision with action, diagnosis, and approval flag.
    """
    # Conflicts -> always manual review
    if evidence.has_conflicts:
        return FraudAccountLockDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="conflict_detected",
            approval_required=True,
        )

    # Missing evidence -> can't proceed
    if account_status is None or fraud_case is None:
        return FraudAccountLockDecision(
            action=ActionType.MANUAL_REVIEW,
            diagnosis="missing_account_or_fraud_evidence",
            approval_required=True,
        )

    # Account not locked -> nothing to do
    if account_status.account_status != "locked":
        return FraudAccountLockDecision(
            action=ActionType.DRAFT_CUSTOMER_RESPONSE,
            diagnosis="account_not_locked",
            approval_required=False,
        )

    # Extract signals
    signals = fraud_case.signals or {}
    risk_score = fraud_case.risk_score or 0
    blacklist_match = signals.get("blacklist_match", False)
    suspicious_inbound = signals.get("suspicious_inbound_funds", False)
    promotion_abuse = signals.get("promotion_abuse", False)
    velocity_anomaly = signals.get("velocity_anomaly", False)
    multiple_devices = signals.get("multiple_new_devices", False)

    # ── Fraud likely ──────────────────────────────────────────
    # High risk score OR specific dangerous signal combinations
    fraud_likely = (
        risk_score >= 70
        or blacklist_match is True
        or suspicious_inbound is True
        or promotion_abuse is True
        or (multiple_devices is True and velocity_anomaly is True)
    )

    if fraud_likely:
        return FraudAccountLockDecision(
            action=ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT,
            diagnosis="suspicious_activity_keep_locked_request_documents",
            approval_required=True,
        )

    # ── False positive candidate ─────────────────────────────
    # Low risk score AND no dangerous signals
    false_positive = (
        risk_score < 50
        and blacklist_match is not True
        and suspicious_inbound is not True
        and promotion_abuse is not True
        and velocity_anomaly is not True
    )

    if false_positive:
        return FraudAccountLockDecision(
            action=ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT,
            diagnosis="likely_false_positive_unlock_candidate",
            approval_required=True,
        )

    # ── Inconclusive ─────────────────────────────────────────
    return FraudAccountLockDecision(
        action=ActionType.MANUAL_REVIEW,
        diagnosis="fraud_review_inconclusive",
        approval_required=True,
    )

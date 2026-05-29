"""Risk classification rules — determines approval requirements.

Risk matrix:
  refund action            → MEDIUM (amount < 2M) / HIGH (>= 2M)   → approval required
  reconciliation ticket    → LOW                                    → no approval (MVP)
  manual_review            → HIGH                                   → approval required
  draft_customer_response  → LOW                                    → no approval
  wait_sla                 → LOW                                    → no approval
  no_action                → LOW                                    → no approval
"""

from __future__ import annotations

from fintech_agent.schemas.enums import ActionType, RiskLevel

# Threshold in VND for high-risk refunds
HIGH_RISK_THRESHOLD = 2_000_000  # 2M VND


def classify_risk(action: ActionType, amount: int = 0) -> RiskLevel:
    """Classify the risk level of a proposed action.

    Args:
        action: The action type being proposed.
        amount: The monetary amount involved (0 if N/A).

    Returns:
        RiskLevel classification.
    """
    if action == ActionType.CREATE_REFUND_REQUEST_DRAFT:
        if amount >= HIGH_RISK_THRESHOLD:
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

    if action == ActionType.CREATE_FORCE_SUCCESS_DRAFT:
        return RiskLevel.HIGH

    if action == ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT:
        return RiskLevel.HIGH

    if action == ActionType.CREATE_REQUEST_DOCUMENTS_RESPONSE_DRAFT:
        return RiskLevel.HIGH

    if action == ActionType.MANUAL_REVIEW:
        return RiskLevel.HIGH

    # Everything else is low risk
    return RiskLevel.LOW


def requires_approval(action: ActionType, amount: int = 0) -> bool:
    """Determine if the action requires human approval.

    Returns True for:
      - Any refund action (regardless of amount)
      - Manual review
    """
    risk = classify_risk(action, amount)
    return risk in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}

"""Idempotency rules — prevent duplicate action drafts.

idempotency_key = hash(transaction_id + action_type + amount)

If a refund has already been requested/approved/executed for the same
(transaction_id, action_type, amount) combination, we must NOT create
a duplicate draft.
"""

from __future__ import annotations

import hashlib

from fintech_agent.schemas.enums import ActionType, RefundStatusValue
from fintech_agent.schemas.evidence import RefundStatus


def generate_idempotency_key(
    transaction_id: str,
    action_type: ActionType,
    amount: int,
) -> str:
    """Generate a deterministic idempotency key.

    Args:
        transaction_id: The transaction ID.
        action_type: The action being proposed.
        amount: The monetary amount.

    Returns:
        A hex digest string.
    """
    raw = f"{transaction_id}:{action_type.value}:{amount}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def is_duplicate_action(
    transaction_id: str,
    action_type: ActionType,
    amount: int,
    refund: RefundStatus | None,
) -> bool:
    """Check if creating this action would be a duplicate.

    For refund actions, check if a refund is already in progress.
    For other actions, always returns False (no duplicate check needed).

    Args:
        transaction_id: The transaction ID.
        action_type: The proposed action.
        amount: The monetary amount.
        refund: Current refund status (if available).

    Returns:
        True if this would be a duplicate, False otherwise.
    """
    if action_type != ActionType.CREATE_REFUND_REQUEST_DRAFT:
        return False

    if refund is None:
        return False

    # Already requested, approved, or executed → duplicate
    blocked = {
        RefundStatusValue.REQUESTED,
        RefundStatusValue.APPROVED,
        RefundStatusValue.EXECUTED,
    }
    return refund.refund_status in blocked

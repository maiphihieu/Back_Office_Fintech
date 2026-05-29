"""Money action guard — ABSOLUTE block on forbidden financial actions.

This is the last line of defense. Even if a bug in the rule engine
somehow produces a forbidden action, this guard will catch it.

FORBIDDEN actions (agent must NEVER execute these):
  - execute_refund
  - update_wallet_balance
  - edit_ledger
  - mark_payment_success
  - delete_transaction
  - modify_refund_status
"""

from __future__ import annotations


class SafetyViolation(Exception):
    """Raised when a forbidden money action is attempted.

    This exception should NEVER be caught silently — it indicates
    a critical safety failure that must be logged and investigated.
    """

    def __init__(self, action: str, context: str = "") -> None:
        self.action = action
        self.context = context
        super().__init__(
            f"SAFETY VIOLATION: Forbidden action '{action}' attempted. "
            f"Context: {context or 'none'}"
        )


# Absolute blocklist — these strings must NEVER appear as action names.
FORBIDDEN_ACTIONS: frozenset[str] = frozenset({
    "execute_refund",
    "update_wallet_balance",
    "edit_ledger",
    "mark_payment_success",
    "delete_transaction",
    "modify_refund_status",
    "execute_unlock_account",
    "modify_account_status",
})


def guard_action(action_name: str, context: str = "") -> None:
    """Check if an action is forbidden and raise SafetyViolation if so.

    This should be called before ANY action is executed.

    Args:
        action_name: The action being attempted.
        context: Optional context (case_id, transaction_id, etc.).

    Raises:
        SafetyViolation: If the action is in the blocklist.
    """
    normalized = action_name.strip().lower()
    if normalized in FORBIDDEN_ACTIONS:
        raise SafetyViolation(normalized, context)


def guard_tool_call(tool_name: str, context: str = "") -> None:
    """Check if a tool call is forbidden.

    Same as guard_action but semantically named for tool invocations.
    """
    guard_action(tool_name, context)


def is_safe_action(action_name: str) -> bool:
    """Return True if the action is NOT forbidden.

    Non-raising version for conditional checks.
    """
    return action_name.strip().lower() not in FORBIDDEN_ACTIONS

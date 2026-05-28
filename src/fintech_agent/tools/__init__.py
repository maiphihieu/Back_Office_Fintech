"""Tool layer — structured wrappers around repositories.

Read-only tools:
    get_transaction              — fetch transaction by ID
    get_wallet_ledger            — fetch wallet ledger (source of truth for money)
    get_train_provider_status    — fetch train provider status
    get_utility_bill_status      — fetch utility provider status
    get_refund_status            — fetch refund status
    get_reconciliation_status    — fetch reconciliation status

Draft tools (write, but NEVER execute financial ops):
    create_refund_request_draft           — create refund request draft
    create_reconciliation_ticket_draft    — create reconciliation ticket draft
    create_customer_response_draft        — create customer response draft

There is NO execute_refund tool.
There is NO update_wallet_balance tool.
There is NO edit_ledger tool.
"""

from fintech_agent.tools.draft_action_tools import (
    DraftStore,
    create_customer_response_draft,
    create_reconciliation_ticket_draft,
    create_refund_request_draft,
    get_default_store,
    reset_default_store,
)
from fintech_agent.tools.ledger_tools import get_wallet_ledger
from fintech_agent.tools.reconciliation_tools import get_reconciliation_status
from fintech_agent.tools.refund_tools import get_refund_status
from fintech_agent.tools.tool_errors import (
    DuplicateActionError,
    ToolDataNotFound,
    ToolError,
    ToolTimeout,
    ToolValidationError,
)
from fintech_agent.tools.train_provider_tools import get_train_provider_status
from fintech_agent.tools.transaction_tools import get_transaction
from fintech_agent.tools.utility_provider_tools import get_utility_bill_status

__all__ = [
    # Read-only
    "get_transaction",
    "get_wallet_ledger",
    "get_train_provider_status",
    "get_utility_bill_status",
    "get_refund_status",
    "get_reconciliation_status",
    # Draft
    "create_refund_request_draft",
    "create_reconciliation_ticket_draft",
    "create_customer_response_draft",
    # Store
    "DraftStore",
    "get_default_store",
    "reset_default_store",
    # Errors
    "ToolError",
    "ToolDataNotFound",
    "ToolTimeout",
    "ToolValidationError",
    "DuplicateActionError",
]

# Fintech Back-office MCP Server

**Server name:** `fintech-backoffice-mcp-server`

## Architecture

```
Frontend / CS Dashboard
  → FastAPI Case API
    → LangGraph Agent
      → MCP Client Adapter
        → Fintech MCP Server  ← this module
          → Tool Handlers
            → Repository / Supabase / Mock JSON
```

## Running

```bash
python scripts/run_mcp_server.py
```

The server runs on **stdio** transport (stdin/stdout JSON-RPC).

## Tools

### Read-only (6 tools)

These tools only query data. They do not modify anything.

| Tool | Input | Description |
|------|-------|-------------|
| `get_transaction` | `transaction_id` | Fetch transaction by ID |
| `get_reconciliation_status` | `transaction_id` | Fetch bank reconciliation record |
| `get_wallet_ledger` | `transaction_id` | Fetch wallet ledger entry |
| `get_refund_status` | `transaction_id` | Fetch refund status |
| `get_train_provider_status` | `provider_ref_id` | Fetch train ticket provider status |
| `get_utility_bill_status` | `provider_ref_id` | Fetch utility bill provider status |

### Draft-only (4 tools)

These tools create **pending drafts**. They do NOT execute financial operations.

| Tool | Risk | Approval | Description |
|------|------|----------|-------------|
| `create_refund_request_draft` | Medium | ✅ Required | Create refund request for human review |
| `create_reconciliation_ticket_draft` | Low | ❌ Not required | Create reconciliation ticket for ops team |
| `create_customer_response_draft` | Low | ❌ Not required | Draft a customer response message |
| `create_force_success_draft` | **High** | ✅ Required | Draft force-success for stuck topup |

## Safety invariants

- **No `execute_refund` tool** — agent cannot execute real refunds
- **No `update_wallet_balance` tool** — agent cannot modify wallet balances
- **No `edit_ledger` tool** — agent cannot edit the financial ledger
- **No `mark_transaction_success` tool** — agent cannot mark transactions as successful
- All money-impacting actions create drafts only
- Drafts with `approval_required: true` must be approved by human operators
- Safety guard (`money_action_guard`) blocks forbidden actions at the tool layer
- Idempotency checks prevent duplicate drafts

## Data flow

Handlers use the **repository factory** (`repository_factory.py`) which automatically
routes to Supabase or JSON fallback based on `SUPABASE_ENABLED` in `.env`.

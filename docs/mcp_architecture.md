# MCP-first Tool Architecture

The agent does not call business tools directly. All tool calls go through the MCP boundary.

## Flow

```
Frontend / CS Dashboard
  → FastAPI Case API
    → LangGraph Agent
      → MCP Client Adapter (src/fintech_agent/mcp_client/)
        → Fintech MCP Server (src/fintech_agent/mcp_server/)
          → Tool Handlers
            → Repository / Supabase
```

## MCP Server

**Name:** `fintech-backoffice-mcp-server`

### Read-only tools (6)

| Tool | Input | Description |
|------|-------|-------------|
| `get_transaction` | `transaction_id` | Fetch transaction by ID |
| `get_reconciliation_status` | `transaction_id` | Fetch bank reconciliation record |
| `get_wallet_ledger` | `transaction_id` | Fetch wallet ledger entry |
| `get_refund_status` | `transaction_id` | Fetch refund status |
| `get_train_provider_status` | `provider_ref_id` | Fetch train provider status |
| `get_utility_bill_status` | `provider_ref_id` | Fetch utility bill provider status |

### Draft-only tools (4)

| Tool | Risk | Approval | Description |
|------|------|----------|-------------|
| `create_refund_request_draft` | Medium/High | ✅ Required | Create refund request for human review |
| `create_reconciliation_ticket_draft` | Low | ❌ Not required | Create reconciliation ticket |
| `create_customer_response_draft` | Low | ❌ Not required | Draft customer response |
| `create_force_success_draft` | **High** | ✅ Required | Draft force-success for stuck topup |

## Safety invariants

Money-impacting actions are draft-only and require human approval.

- No `execute_refund` tool
- No `update_wallet_balance` tool
- No `edit_ledger` tool
- No `mark_transaction_success` tool

## MCP Client modes

Controlled by `FINTECH_TOOL_MODE` env var:

- `in_process` (default): Calls MCP handlers directly in-process
- `mcp`: Calls MCP server via subprocess stdio transport

## Running the MCP server standalone

```bash
python scripts/run_mcp_server.py
```

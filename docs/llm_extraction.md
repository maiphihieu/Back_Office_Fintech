# Phase 2: LLM Extraction via OpenAI

## Overview

Phase 2 adds **real LLM extraction** via OpenAI to the `extract_info` node. The LLM extracts structured information (transaction ID, service type, issue type, etc.) from customer complaint text.

> [!IMPORTANT]
> **Safety boundary:** The LLM is ONLY used for text extraction. All refund decisions, approval gates, amounts, and money actions remain 100% deterministic rule-engine-driven.

## Architecture

```
Customer complaint
        │
        ▼
┌─────────────────────────┐
│    extract_info node     │
│  ┌───────────────────┐  │
│  │  MOCK_LLM=true?   │  │
│  │   ├── YES → regex  │  │
│  │   └── NO  → OpenAI │  │
│  └───────────────────┘  │
│         │                │
│    ExtractedInfo         │
│  (Pydantic validated)    │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│  Rule Engine (unchanged) │
│  - conflict_rules        │
│  - train_ticket_rules    │
│  - utility_bill_rules    │
│  - refund_rules          │
│  - risk_rules            │
│  Source of truth:        │
│  - wallet_ledger (money) │
│  - provider (delivery)   │
│  - refund_table (refund) │
└─────────────────────────┘
```

## Configuration

### Environment Variables

| Variable         | Default         | Description |
|------------------|-----------------|-------------|
| `MOCK_LLM`       | `true`          | `true` = regex (no API key needed), `false` = OpenAI |
| `OPENAI_API_KEY`  | (empty)         | Required only when `MOCK_LLM=false` |
| `OPENAI_MODEL`    | `gpt-4.1-mini`  | OpenAI model to use |
| `LLM_TIMEOUT`     | `30`            | Timeout in seconds for OpenAI calls |

### Setup

```bash
# 1. Copy example env
cp .env.example .env

# 2. Edit .env with your API key (if using OpenAI)
#    OPENAI_API_KEY=sk-your-key-here
#    MOCK_LLM=false
```

> [!CAUTION]
> **NEVER commit your `.env` file.** It contains secrets. The `.gitignore` is configured to exclude it.
> 
> **NEVER hard-code API keys** in source code.

## Running

### Default (no API key needed)

```bash
# Uses regex/mock extractor — identical to MVP behavior
MOCK_LLM=true python -m pytest tests/ -v
MOCK_LLM=true python scripts/run_demo_cases.py
```

### With OpenAI

```bash
# Set in .env:
#   OPENAI_API_KEY=sk-your-key
#   MOCK_LLM=false

python -m pytest tests/ -v          # Tests still use mocks, no API calls
python scripts/run_demo_cases.py    # Demo still uses mock data
```

To test real OpenAI extraction manually:

```bash
MOCK_LLM=false python -c "
from fintech_agent.llm import extract_complaint_info
from fintech_agent.config import get_settings

result = extract_complaint_info(
    'Tôi mua vé tàu 350,000 VND, mã TXN-20260527-001, nhưng chưa nhận được vé.',
    get_settings(),
)
print(result.model_dump_json(indent=2))
"
```

## Safety Boundaries

### What the LLM CAN do

- Extract `transaction_id`, `user_id`, `service_type`, `issue_type` from text
- Extract `order_id`, `bill_code`, `customer_code` if mentioned
- Report `amount_claimed` (customer's stated amount — **NOT the refund amount**)
- Report `confidence` in its extraction
- Detect `language`

### What the LLM CANNOT do

| Action | Enforced By |
|--------|-------------|
| Decide refund | Rule engine only |
| Decide approval | Rule engine only |
| Set refund amount | `wallet_ledger.debit_amount` only |
| Execute refund | `SafetyViolation` exception |
| Update wallet balance | `SafetyViolation` exception |
| Edit ledger | `SafetyViolation` exception |
| Create money actions | `ActionType` enum has no `execute_refund` |
| Override evidence | Evidence comes from tools, not LLM |

### Fallback Behavior

If OpenAI fails (timeout, invalid JSON, API error), the system:

1. Logs `llm_extraction_failed` audit event
2. Falls back to regex/mock extractor
3. Sets `extraction_method = "fallback_regex"`
4. Continues the workflow normally

**No case is ever dropped due to LLM failure.**

## Module Structure

```
src/fintech_agent/llm/
├── __init__.py          # Exports extract_complaint_info
├── prompts.py           # System prompt (extraction-only, no business logic)
├── openai_client.py     # OpenAI API wrapper with timeout + error handling
├── mock_extractor.py    # Regex extractor (MVP logic, used as fallback)
└── extractor.py         # Router: mock_llm → regex, else → OpenAI → fallback
```

## Testing

```bash
# All tests (366 total, no real API calls)
MOCK_LLM=true python -m pytest tests/ -v

# LLM extractor tests only (30 tests)
MOCK_LLM=true python -m pytest tests/unit/test_llm_extractor.py -v

# Demo scenarios (5/5)
python scripts/run_demo_cases.py
```

### Test Coverage

| Test Category | Count | Description |
|---------------|-------|-------------|
| Mock mode | 2 | MOCK_LLM=true → regex, no API call |
| OpenAI valid | 2 | Mocked OpenAI → valid ExtractedInfo |
| OpenAI fallback | 3 | Invalid JSON, timeout, generic error → fallback |
| Prompt injection | 1 | Injected text → no forbidden actions |
| Amount safety | 3 | amount_claimed ≠ refund_amount, validation bounds |
| Sanitization | 2 | Forbidden fields stripped from LLM output |
| Extraction method | 3 | Correct method string in output |
| Regex regression | 6 | TRAIN_001, TRAIN_002, BILL_002, BILL_003, CONFLICT_001, REFUND_001 |
| Mock extractor | 6 | Unit tests for regex extractor |
| Error types | 2 | LLMExtractionError |
| **Total** | **30** | |

## `amount_claimed` vs Refund Amount

> [!WARNING]
> `amount_claimed` is the customer's **stated amount** from the complaint text. It is stored for audit purposes ONLY.
>
> The **actual refund amount** always comes from `wallet_ledger.debit_amount` — the source of truth for money in the system.
>
> No rule, tool, or node ever uses `amount_claimed` as the refund amount.

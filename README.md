# Fintech Agent — AI Back-office Workflow Agent

> AI Agent hỗ trợ xử lý khiếu nại, hoàn tiền và đối soát giao dịch trong hệ sinh thái fintech.
> Kèm admin portal cho team CS/Ops review case, evidence, approve/reject draft.

---

## Overview

Agent nhận ticket/khiếu nại của khách hàng, tự động:

1. **Trích xuất thông tin** — user_id, transaction_id, service_type, issue_type (regex hoặc OpenAI)
2. **Gọi tool/API** lấy evidence (transaction, wallet ledger, provider status, refund status)
3. **Phát hiện conflict** giữa các source of truth
4. **Route vào workflow** phù hợp (train_ticket / utility_bill)
5. **Áp rule nghiệp vụ** để chẩn đoán (deterministic rule engine — không dùng LLM)
6. **Đề xuất action** cho nhân viên CS/Ops (draft only — không execute)
7. **Yêu cầu human approval** nếu action ảnh hưởng tiền
8. **Ghi audit log** đầy đủ mọi state transition, tool call, decision

### Workflows

| Workflow | Mô tả | Actions có thể |
|----------|-------|----------------|
| `train_ticket_reconciliation` | Mua vé tàu: ví trừ tiền nhưng chưa có vé | refund draft, customer response, reconciliation ticket |
| `utility_bill_reconciliation` | Thanh toán điện/nước: ví trừ tiền nhưng provider chưa ghi nhận | refund draft, reconciliation ticket, wait SLA |

---

## Tech Stack

### Backend
| Technology | Version | Purpose |
|-----------|---------|---------|
| Python | 3.11+ | Runtime |
| FastAPI | latest | REST API layer |
| LangGraph | latest | Workflow state machine |
| Pydantic | v2 | Data validation & schemas |
| OpenAI | ≥1.30.0 | LLM extraction (optional, toggle via `MOCK_LLM`) |
| pytest | latest | 366 automated tests |

### Frontend
| Technology | Version | Purpose |
|-----------|---------|---------|
| React | 19 | UI framework |
| TypeScript | 5.8 | Type safety |
| Vite | 8 | Build tool + dev server |
| React Router | 6 | Client-side routing |
| Vanilla CSS | — | Custom dark-theme design system |

---

## Quick Start

### 1. Setup Backend

```bash
cd Back_Office_Fintech

# Create virtual environment
python -m venv .venv
source .venv/bin/activate    # macOS/Linux

# Install dependencies
pip install -e ".[dev]"

# Copy environment config
cp .env.example .env
```

### 2. Start Backend

```bash
uvicorn fintech_agent.main:app --reload --port 8000
```

Open Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

### 3. Setup & Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Open Admin Portal: [http://localhost:5173](http://localhost:5173)

### 4. Health Check

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0","environment":"local"}
```

### 5. Run Tests

```bash
python -m pytest tests/ -v
# 366 passed
```

### 6. Run Demo Script (CLI)

```bash
python scripts/run_demo_cases.py
# 🎉 All 5 scenarios PASSED!
```

---

## Admin Portal

| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | Case list + filters (status, workflow, risk, approval, conflict) |
| `/create` | Create Case | Submit complaint → agent runs workflow automatically |
| `/cases/:id` | Case Detail | Evidence panels, decision, approval panel, audit timeline |
| `/demo` | Demo Scenarios | Run 5 predefined scenarios with pass/fail validation |
| `/safety` | Safety Checks | 12 safety invariants + system health |

### Demo Flow

1. Start backend + frontend
2. Go to **Demo Scenarios** (`/demo`)
3. Click **Run All Scenarios**
4. All 5 should show ✅ PASS
5. Click scenario → **View Case Detail**
6. Review evidence, decision, audit trail
7. For TRAIN_001 / BILL_003 → **Approve Draft** in Approval Panel

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | System health check |
| `GET` | `/cases` | List all cases |
| `POST` | `/cases` | Create & run new case |
| `GET` | `/cases/{case_id}` | Get case details + evidence |
| `GET` | `/cases/{case_id}/audit` | Get audit trail |
| `POST` | `/cases/{case_id}/approve` | Approve pending action |
| `POST` | `/cases/{case_id}/reject` | Reject pending action |

---

## Project Structure

```
Back_Office_Fintech/
├── src/fintech_agent/
│   ├── main.py              # FastAPI app + CORS
│   ├── config.py            # Settings from .env (MOCK_LLM, LLM_TIMEOUT, etc.)
│   ├── api/                 # HTTP endpoints (cases, health, approvals)
│   │   ├── cases.py         # CRUD + approve/reject endpoints
│   │   ├── models.py        # Pydantic request/response models
│   │   ├── health.py        # Health check
│   │   └── service.py       # CaseService — orchestrates graph + approval
│   ├── schemas/             # Core data models
│   │   ├── case_state.py    # ExtractedInfo, CaseState, EvidenceBundle
│   │   └── enums.py         # CaseStatus, IssueType, RiskLevel, etc.
│   ├── graph/               # LangGraph state machine
│   │   ├── builder.py       # Compile graph with nodes + edges
│   │   └── state.py         # AgentState TypedDict
│   ├── nodes/               # Graph node implementations
│   │   ├── extract_info.py  # Complaint → ExtractedInfo (LLM or regex)
│   │   ├── fetch_evidence.py# Evidence gathering from tools
│   │   ├── detect_conflict.py
│   │   ├── route_workflow.py
│   │   ├── apply_rules.py
│   │   └── recommend_action.py
│   ├── llm/                 # LLM extraction module (Phase 2)
│   │   ├── prompts.py       # System prompt (extraction-only, no decisions)
│   │   ├── openai_client.py # OpenAI wrapper (timeout, error handling)
│   │   ├── mock_extractor.py# Regex extractor (default, no API needed)
│   │   └── extractor.py     # Router: MOCK_LLM → regex, else → OpenAI
│   ├── rules/               # Deterministic rule engine (no LLM)
│   ├── workflows/           # Workflow-specific logic + approval service
│   ├── tools/               # Read-only + draft tools (mock API)
│   ├── repositories/        # Data access (mock JSON)
│   ├── data/                # Mock JSON data files
│   ├── audit/               # Audit event logging
│   ├── safety/              # Money-action guards, idempotency
│   └── utils/               # Shared utilities (PII masking, sanitization)
├── frontend/                # Admin Portal (React + TypeScript + Vite)
│   ├── src/
│   │   ├── api/             # API client + types
│   │   ├── pages/           # Dashboard, CreateCase, CaseDetail, Demo, Safety
│   │   ├── components/      # Layout, badges
│   │   └── lib/             # Format utils, demo constants
│   └── README.md            # Frontend setup guide
├── tests/                   # pytest test suite (366 tests)
│   ├── unit/                # Unit tests for all modules
│   └── integration/         # E2E workflow tests
├── scripts/
│   └── run_demo_cases.py    # CLI demo — 5 scenarios
└── docs/
    ├── demo.md              # Demo output sample
    ├── evaluation_report.md # MVP evaluation report
    └── llm_extraction.md    # Phase 2 LLM docs
```

---

## Environment Variables

```bash
# .env.example
OPENAI_API_KEY=             # Required only when MOCK_LLM=false
OPENAI_MODEL=gpt-4.1-mini  # OpenAI model
MOCK_LLM=true              # true=regex (default), false=OpenAI
LLM_TIMEOUT=30             # OpenAI timeout in seconds
APP_ENV=local
APP_DEBUG=true
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
```

> ⚠️ **NEVER commit `.env`** — it contains secrets. The `.gitignore` is configured to exclude it.

---

## Safety Principles

> This agent handles **financial data**. The following rules are non-negotiable:

| # | Rule | Enforced By |
|---|------|-------------|
| 1 | **LLM does NOT decide money** — LLM only extracts info; rule engine decides | `extractor.py` + prompt |
| 2 | **No real refund execution** — Agent only creates drafts/requests | No `execute_refund` tool exists |
| 3 | **Ledger is read-only** — Agent never modifies wallet balance | `SafetyViolation` exception |
| 4 | **Human approval required** — All money-affecting actions need approval | Approval gate in graph |
| 5 | **Conflict = manual review** — Conflicting sources → no diagnosis, route to human | Conflict detector node |
| 6 | **Everything is audited** — Every state transition, tool call, decision is logged | AuditLogger |
| 7 | **Duplicate refund prevention** — Idempotency keys + refund status checks | Safety guard |
| 8 | **amount_claimed ≠ refund amount** — Customer-claimed amount is audit-only | Rule engine uses `wallet_ledger.debit_amount` |
| 9 | **Fallback on LLM failure** — OpenAI errors → regex fallback, case never dropped | `extractor.py` |

### Admin Portal Safety

- ❌ No "Execute Refund" button — does not exist
- ❌ No "Update Wallet" / "Edit Ledger" buttons
- ✅ Approve button says **"Approve Draft"**, not "Refund Now"
- ✅ Refund actions show warning: *"creates a draft, not a real refund"*
- ✅ Conflict cases show red alert: *"Manual review required"*
- ✅ `amount_claimed` labeled: *"from complaint — NOT used for refund"*
- ✅ No OpenAI API key or secrets displayed

---

## Test Results

```
366 tests passed (0 failed)
5/5 demo scenarios PASSED
TypeScript build: 0 errors
Frontend build: PASS
```

### Test Coverage

| Module | Tests | Focus |
|--------|-------|-------|
| Schemas | 48 | Data validation, enum values, state transitions |
| Rules | 80+ | Workflow routing, decision matrices, risk levels |
| Tools | 40+ | Mock tools, safety guards, idempotency |
| Nodes | 30+ | Extract, fetch, detect, route, recommend |
| Integration | 30+ | Full E2E workflow scenarios |
| LLM Extractor | 30 | Mock mode, OpenAI fallback, prompt injection, sanitization |
| API | 20+ | HTTP endpoints, approval flow |

---

## Demo Scenarios

| ID | Description | Expected Action | Approval | Risk |
|----|-------------|----------------|----------|------|
| TRAIN_001 | Debited + ticket not issued | `create_refund_request_draft` | Yes | medium |
| TRAIN_002 | Ticket was issued | `draft_customer_response` | No | low |
| BILL_002 | Provider not confirmed | `create_reconciliation_ticket_draft` | No | low |
| BILL_003 | Provider failed | `create_refund_request_draft` | Yes | medium |
| CONFLICT_001 | Ledger vs transaction mismatch | `manual_review` | Yes | high |

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/llm_extraction.md](docs/llm_extraction.md) | Phase 2 — OpenAI extraction setup, safety, fallback |
| [docs/evaluation_report.md](docs/evaluation_report.md) | MVP evaluation report with test matrix |
| [docs/demo.md](docs/demo.md) | Demo script output sample |
| [frontend/README.md](frontend/README.md) | Frontend setup & safety guide |

---

## License

Private — Internal use only.

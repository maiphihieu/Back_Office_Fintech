# Fintech Agent — Admin Portal

Admin portal for the AI Back-office Workflow Agent. View complaint cases, evidence, rule decisions, approve/reject drafts, and monitor safety.

## Quick Start

```bash
# 1. Start the backend
cd ..
MOCK_LLM=true .venv/bin/python -m fintech_agent.main

# 2. Start the frontend (in another terminal)
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

## Environment

```bash
cp .env.example .env
# Edit VITE_API_BASE_URL if backend is on a different port
```

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend API URL |

## Pages

| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | Case list with filters (status, workflow, risk, approval, conflict) |
| `/create` | Create Case | Submit complaint → agent runs workflow |
| `/cases/:id` | Case Detail | Evidence panels, decision, approval, audit timeline |
| `/demo` | Demo Scenarios | Run 5 predefined scenarios with pass/fail validation |
| `/safety` | Safety Checks | 12 safety invariants + system health |

## Demo Flow

1. Start backend + frontend
2. Go to **Demo Scenarios** page
3. Click **Run All Scenarios**
4. All 5 should show **PASS** ✓
5. Click any scenario → **View Case Detail**
6. Review evidence, decision, audit trail
7. For TRAIN_001 or BILL_003 → **Approve Draft** in the Approval Panel

## Safety Guarantees (UI)

- ❌ **No "Execute Refund" button** — does not exist anywhere in the UI
- ❌ **No "Update Wallet" button** — not possible from the frontend
- ❌ **No "Edit Ledger" button** — not possible from the frontend
- ✅ Approve button says **"Approve Draft"**, not "Refund Now"
- ✅ Refund actions show warning: *"creates a draft, not a real refund"*
- ✅ Conflict cases show red alert: *"Manual review required"*
- ✅ `amount_claimed` is labeled: *"from complaint — NOT used for refund"*
- ✅ Evidence panels show source-of-truth labels
- ✅ No OpenAI API key or secrets are displayed

## API Endpoints Used

| Endpoint | Method | Used By |
|----------|--------|---------|
| `/health` | GET | Safety page |
| `/cases` | GET | Dashboard |
| `/cases` | POST | Create Case, Demo |
| `/cases/:id` | GET | Case Detail |
| `/cases/:id/audit` | GET | Case Detail (Audit tab) |
| `/cases/:id/approve` | POST | Approval Panel |
| `/cases/:id/reject` | POST | Approval Panel |

## Tech Stack

- React 19 + TypeScript
- Vite 8
- React Router 6
- Vanilla CSS (custom design system)
- No Tailwind, no shadcn — zero external UI deps

## Build

```bash
npm run build    # TypeScript check + Vite production build
```

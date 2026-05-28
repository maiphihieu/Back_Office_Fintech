# MVP Evaluation & Test Report

> **Version:** 0.1.0 — MVP  
> **Date:** 2026-05-27  
> **Test framework:** pytest 8.x  
> **Total tests:** 336 passed, 0 failed  
> **Demo script:** `scripts/run_demo_cases.py` — 5/5 scenarios PASS

---

## 1. Scope MVP

### In Scope

| Feature                          | Status     |
|----------------------------------|------------|
| Train ticket complaint workflow  | ✅ Implemented |
| Utility bill complaint workflow  | ✅ Implemented |
| Deterministic rule engine        | ✅ Implemented |
| Conflict detection (4 rules)     | ✅ Implemented |
| Refund eligibility checks (4)    | ✅ Implemented |
| Risk classification              | ✅ Implemented |
| Idempotency (duplicate prevention) | ✅ Implemented |
| Approval gate (2-phase)          | ✅ Implemented |
| Audit trail (immutable events)   | ✅ Implemented |
| Safety guardrails (blocklist)    | ✅ Implemented |
| PII masking in audit logs        | ✅ Implemented |
| Prompt injection detection       | ✅ Implemented |
| Input sanitization               | ✅ Implemented |
| LangGraph state machine          | ✅ Implemented |
| Mock tool layer (5 tools)        | ✅ Implemented |
| FastAPI endpoints                | ✅ Implemented |

### Out of Scope (MVP)

| Feature                            | Status         |
|------------------------------------|----------------|
| Real API integration (providers)   | ❌ Not started  |
| Real refund execution              | ❌ By design    |
| Real LLM extraction (GPT/Gemini)   | ❌ Regex-based  |
| Production database                | ❌ In-memory    |
| Multi-language complaint parsing   | ❌ Vietnamese only |
| SLA monitoring / timeout tracking  | ❌ Not started  |
| Reopen flow (full cycle)           | ⚠️ Schema only  |

---

## 2. Test Scenarios

### 2.1 Primary Test Matrix

| Case ID       | Scenario                           | Wallet     | Provider            | Refund Status   | Expected Action                     | Expected Approval |
|---------------|------------------------------------|-----------|---------------------|-----------------|--------------------------------------|-------------------|
| `TRAIN_001`   | Debited + ticket_not_issued        | debited   | ticket_not_issued   | not_requested   | `create_refund_request_draft`       | ✅ YES            |
| `TRAIN_002`   | Debited + ticket_issued with code  | debited   | ticket_issued       | not_requested   | `draft_customer_response`           | ❌ NO             |
| `TRAIN_003`   | Debited + provider_no_record       | debited   | provider_no_record  | not_requested   | `create_reconciliation_ticket_draft`| ❌ NO             |
| `BILL_001`    | Debited + provider confirmed       | debited   | confirmed           | not_requested   | `draft_customer_response`           | ❌ NO             |
| `BILL_002`    | Debited + provider not_confirmed   | debited   | not_confirmed       | not_requested   | `create_reconciliation_ticket_draft`| ❌ NO             |
| `BILL_003`    | Debited + provider failed          | debited   | failed              | not_requested   | `create_refund_request_draft`       | ✅ YES            |
| `CONFLICT_001`| Wallet debited + txn pending       | debited   | ticket_not_issued   | not_requested   | `manual_review`                     | ✅ YES            |
| `REFUND_001`  | Refund already executed            | debited   | ticket_not_issued   | executed        | `no_action`                         | ❌ NO             |

### 2.2 Test Coverage by Layer

| Layer                   | Test File                              | Test Count | Status    |
|-------------------------|----------------------------------------|------------|-----------|
| Schemas & Enums         | `tests/unit/test_schemas.py`           | 51         | ✅ PASS   |
| Rule Engine             | `tests/unit/test_rules.py`             | 48         | ✅ PASS   |
| Safety Guardrails       | `tests/unit/test_safety.py`            | 21         | ✅ PASS   |
| Audit Logger            | `tests/unit/test_audit.py`             | 26         | ✅ PASS   |
| Mock Tools              | `tests/unit/test_tools.py`             | 30         | ✅ PASS   |
| Graph Nodes             | `tests/unit/test_graph.py`             | 28         | ✅ PASS   |
| Repositories            | `tests/unit/test_repositories.py`      | 52         | ✅ PASS   |
| Integration Workflows   | `tests/integration/test_workflows.py`  | 17         | ✅ PASS   |
| API Endpoints           | `tests/api/test_api.py`               | 61         | ✅ PASS   |
| Health Check            | `tests/test_health.py`                 | 2          | ✅ PASS   |
| **TOTAL**               |                                        | **336**    | **✅ ALL PASS** |

---

## 3. Expected vs Actual Results

### 3.1 Unit Test Results (Rule Engine)

All results from `tests/unit/test_rules.py` — **48 tests, 48 passed.**

| Case ID       | Test Function                                    | Expected Action                        | Actual Action                          | Match |
|---------------|--------------------------------------------------|----------------------------------------|----------------------------------------|-------|
| `TRAIN_001`   | `test_train_001_ticket_not_issued_refund_draft`  | `create_refund_request_draft`          | `create_refund_request_draft`          | ✅    |
| `TRAIN_002`   | `test_train_002_ticket_issued_no_refund`         | `draft_customer_response`              | `draft_customer_response`              | ✅    |
| `TRAIN_003`   | `test_train_003_provider_no_record_reconciliation`| `create_reconciliation_ticket_draft`  | `create_reconciliation_ticket_draft`   | ✅    |
| `BILL_001`    | `test_bill_001_confirmed_no_refund`              | `draft_customer_response`              | `draft_customer_response`              | ✅    |
| `BILL_002`    | `test_bill_002_not_confirmed_reconciliation`     | `create_reconciliation_ticket_draft`   | `create_reconciliation_ticket_draft`   | ✅    |
| `BILL_003`    | `test_bill_003_failed_refund_draft`              | `create_refund_request_draft`          | `create_refund_request_draft`          | ✅    |
| `CONFLICT`    | `test_conflict_triggers_manual_review`           | `manual_review`                        | `manual_review`                        | ✅    |
| `REFUND_DUP`  | `test_refund_already_executed_no_action`          | `no_action`                            | `no_action`                            | ✅    |

### 3.2 Integration Test Results (End-to-End Workflows)

All results from `tests/integration/test_workflows.py` — **17 tests, 17 passed.**

| Case ID       | Test Function                                   | Expected Flow                                    | Actual Flow                                       | Match |
|---------------|--------------------------------------------------|--------------------------------------------------|----------------------------------------------------|-------|
| `TRAIN_001`   | `test_train_001_phase1_pauses_at_approval`      | Stops at `WAITING_APPROVAL`                      | Stops at `WAITING_APPROVAL`                        | ✅    |
| `TRAIN_001`   | `test_train_001_full_with_approval`             | Approve → refund draft → `CLOSED`               | Approve → refund draft (450,000₫) → `CLOSED`      | ✅    |
| `TRAIN_002`   | `test_train_002_no_approval_needed`             | Direct → customer response → `CLOSED`           | Direct → customer response → `CLOSED`              | ✅    |
| `TRAIN_003`   | `test_train_003_reconciliation_no_approval`     | Direct → reconciliation → `CLOSED`              | Direct → reconciliation → `CLOSED`                 | ✅    |
| `BILL_002`    | `test_bill_002_not_confirmed_reconciliation`    | Reconciliation (NOT refund) → `CLOSED`           | Reconciliation (NOT refund) → `CLOSED`             | ✅    |
| `BILL_003`    | `test_bill_003_failed_needs_approval`           | Stops at `WAITING_APPROVAL`                      | Stops at `WAITING_APPROVAL` (310,000₫)             | ✅    |
| `BILL_003`    | `test_bill_003_full_with_approval`              | Approve → refund draft → `CLOSED`               | Approve → refund draft (310,000₫) → `CLOSED`      | ✅    |
| `CONFLICT_001`| `test_conflict_001_manual_review`               | Conflict → manual review → `CLOSED`             | Conflict → manual review → `CLOSED`                | ✅    |
| `REFUND_001`  | `test_refund_001_no_duplicate`                  | Refund already executed → `no_action`            | `no_action`, diagnosis: `refund_not_eligible`      | ✅    |

### 3.3 Demo Script Results

From `scripts/run_demo_cases.py` — **5 cases, 5 passed.**

| Case ID          | Action                              | Approval | Risk   | Final Status   | Result |
|------------------|--------------------------------------|----------|--------|----------------|--------|
| `CASE-TRAIN-001` | `create_refund_request_draft`       | YES      | medium | closed         | PASS   |
| `CASE-TRAIN-002` | `draft_customer_response`           | NO       | low    | closed         | PASS   |
| `CASE-BILL-002`  | `create_reconciliation_ticket_draft`| NO       | low    | closed         | PASS   |
| `CASE-BILL-003`  | `create_refund_request_draft`       | YES      | medium | closed         | PASS   |
| `CASE-CONFLICT-001`| `manual_review`                   | YES      | high   | manual_review  | PASS   |

---

## 4. Safety Checks

### 4.1 Money Action Guard (Blocklist)

Agent is **absolutely blocked** from executing any financial action. Tested via `tests/unit/test_safety.py` and `tests/unit/test_tools.py`.

| Forbidden Action          | Guard Function      | Test Verified | Status |
|---------------------------|---------------------|---------------|--------|
| `execute_refund`          | `guard_action()`    | ✅ Yes        | BLOCKED |
| `update_wallet_balance`   | `guard_action()`    | ✅ Yes        | BLOCKED |
| `edit_ledger`             | `guard_action()`    | ✅ Yes        | BLOCKED |
| `mark_payment_success`    | `guard_action()`    | ✅ Yes        | BLOCKED |
| `delete_transaction`      | `guard_action()`    | ✅ Yes        | BLOCKED |
| `modify_refund_status`    | `guard_action()`    | ✅ Yes        | BLOCKED |

Additional safety tests:
- ✅ Case-insensitive blocking (`EXECUTE_REFUND` → blocked)
- ✅ Whitespace-trimmed blocking (`  execute_refund  ` → blocked)
- ✅ `SafetyViolation` exception includes action name and context
- ✅ No `execute_refund` tool exists in the tool registry (verified by test)
- ✅ No `update_wallet_balance` tool exists in the tool registry (verified by test)

### 4.2 Input Sanitization

| Check                         | Test Verified | Status |
|-------------------------------|---------------|--------|
| Strip leading/trailing spaces | ✅ Yes        | ✅ PASS |
| Remove control characters     | ✅ Yes        | ✅ PASS |
| Preserve newlines             | ✅ Yes        | ✅ PASS |
| Truncate long inputs (>5000)  | ✅ Yes        | ✅ PASS |
| Field-level sanitization      | ✅ Yes        | ✅ PASS |

### 4.3 PII Masking

| PII Type                 | Pattern          | Masked As         | Test Verified | Status |
|--------------------------|------------------|-------------------|---------------|--------|
| Phone (0x format)        | `0987654321`     | `[MASKED_PHONE]`  | ✅ Yes        | ✅ PASS |
| Phone (+84 format)       | `+84987654321`   | `[MASKED_PHONE]`  | ✅ Yes        | ✅ PASS |
| Email                    | `user@example.com`| `[MASKED_EMAIL]` | ✅ Yes        | ✅ PASS |
| Citizen ID (12 digits)   | `001099012345`   | `[MASKED_ID]`     | ✅ Yes        | ✅ PASS |
| Non-PII text             | `Transaction TXN_001` | unchanged    | ✅ Yes        | ✅ PASS |

### 4.4 Prompt Injection Detection

| Attack Pattern                    | Detected | Reason                         | Test Verified |
|-----------------------------------|----------|--------------------------------|---------------|
| "Ignore all previous instructions"| ✅ Yes   | `ignore_previous_instructions` | ✅ Yes        |
| "You are now a helpful..."        | ✅ Yes   | `role_override_attempt`        | ✅ Yes        |
| "system: override safety checks"  | ✅ Yes   | `system_prompt_override`       | ✅ Yes        |
| "Please execute_refund for..."    | ✅ Yes   | `forbidden_action_in_text`     | ✅ Yes        |
| `<\|system\|>` special tokens    | ✅ Yes   | `special_token_detected`       | ✅ Yes        |
| Normal Vietnamese complaint       | ❌ No    | `clean`                        | ✅ Yes        |

---

## 5. Workflow Routing Accuracy

### 5.1 Routing Logic

Routing is deterministic, based on `service_type` from extracted info:
- `train_ticket` → `train_ticket_workflow`
- `electric_bill` / `water_bill` → `utility_bill_workflow`
- `unknown` → `manual_review`

### 5.2 Routing Test Results

| Input Service Type | Expected Workflow             | Actual Workflow               | Test Source                                   | Match |
|--------------------|-------------------------------|-------------------------------|-----------------------------------------------|-------|
| `train_ticket`     | `train_ticket_workflow`       | `train_ticket_workflow`       | `test_graph.py::test_routes_train`            | ✅    |
| `electric_bill`    | `utility_bill_workflow`       | `utility_bill_workflow`       | `test_graph.py::test_routes_utility`          | ✅    |
| `water_bill`       | `utility_bill_workflow`       | `utility_bill_workflow`       | `run_demo_cases.py::BILL_003`                 | ✅    |
| `unknown`          | `manual_review`               | `manual_review`               | `test_graph.py::test_unknown_goes_manual`     | ✅    |

---

## 6. Tool / Evidence Completeness

### 6.1 Tool Registry (Read-Only)

All tools are **read-only by design**. No write tool exists for wallet, ledger, or refund execution.

| Tool Name                | Type       | Returns                    | Tested | Status |
|--------------------------|------------|----------------------------|--------|--------|
| `get_transaction`        | Read-only  | `Transaction`              | ✅ Yes | ✅ PASS |
| `get_wallet_ledger`      | Read-only  | `WalletLedger`             | ✅ Yes | ✅ PASS |
| `get_train_provider`     | Read-only  | `TrainProviderStatus`      | ✅ Yes | ✅ PASS |
| `get_utility_provider`   | Read-only  | `UtilityProviderStatus`    | ✅ Yes | ✅ PASS |
| `get_refund_status`      | Read-only  | `RefundStatus`             | ✅ Yes | ✅ PASS |
| `get_reconciliation`     | Read-only  | `ReconciliationStatus`     | ✅ Yes | ✅ PASS |
| `create_refund_draft`    | Draft-only | `RefundRequestDraft`       | ✅ Yes | ✅ PASS |
| `create_recon_draft`     | Draft-only | `ReconciliationTicketDraft`| ✅ Yes | ✅ PASS |
| `create_response_draft`  | Draft-only | Response text              | ✅ Yes | ✅ PASS |

### 6.2 Evidence Bundle Completeness

| Evidence Source       | Authority For         | Used In Decision | Tested in Pipeline |
|-----------------------|-----------------------|------------------|--------------------|
| `wallet_ledger`       | Money in wallet (#1)  | ✅ Yes           | ✅ Yes             |
| `refund_table`        | Refund lifecycle (#2) | ✅ Yes           | ✅ Yes             |
| `provider_status`     | Service delivery (#3) | ✅ Yes           | ✅ Yes             |
| `transaction`         | Metadata only (#4)    | ✅ Yes           | ✅ Yes             |

### 6.3 Source of Truth Hierarchy

Trust order (highest → lowest):

1. **wallet_ledger** → Money state. Refund amount MUST come from `ledger.debit_amount`.
2. **refund_table** → Refund lifecycle. Only source for requested/approved/executed.
3. **provider_status** → Service delivery. Only source for ticket/bill confirmation.
4. **transaction** → Metadata only. Status can LAG behind ledger — never trust for money.

> ⚠️ **Design rule:** Agent never uses complaint text for refund amount. Always uses `wallet_ledger.debit_amount`. Verified by `RefundRequestDraft.amount` validation in `schemas/actions.py`.

### 6.4 Tool Error Handling

| Error Scenario        | Handling              | Test Verified |
|-----------------------|-----------------------|---------------|
| Data not found        | Raises `ToolDataNotFoundError` | ✅ Yes   |
| Timeout               | Raises `ToolTimeoutError`      | ✅ Yes   |
| Validation error      | Raises `ToolValidationError`   | ✅ Yes   |
| Duplicate action      | Raises `DuplicateActionError`  | ✅ Yes   |

---

## 7. Conflict Detection Results

### 7.1 Conflict Rules Tested

4 conflict detection rules, each with positive and negative test cases.

| Conflict Type                      | Sources Compared           | Trigger Condition                    | Tests | Status |
|------------------------------------|----------------------------|--------------------------------------|-------|--------|
| Ledger ↔ Transaction mismatch     | `wallet_ledger` vs `transaction` | Wallet debited + txn status `pending` | 2     | ✅ PASS |
| Provider success but no ticket code| `provider_status` (self)   | `ticket_issued` but `ticket_code=null`| 2     | ✅ PASS |
| Refund executed but no credit      | `refund_table` vs `wallet_ledger` | Refund executed + no credit entry | 2     | ✅ PASS |
| User ownership mismatch           | `transaction` vs `case`    | `txn.user_id ≠ case.user_id`        | 2     | ✅ PASS |
| Multiple conflicts simultaneously | All sources                | Two conflicts at once                | 1     | ✅ PASS |

### 7.2 Conflict → Behavior

| Conflict Detected | Rule Engine Behavior      | Tested |
|--------------------|--------------------------|--------|
| ✅ Yes             | → `manual_review`, approval required | ✅ Integration test + demo |
| ❌ No              | → Normal workflow continues           | ✅ All non-conflict cases |

> **Key invariant:** When ANY conflict exists, the agent MUST NOT auto-diagnose or auto-recommend. It routes to `manual_review`. This is verified by both unit tests and integration tests.

---

## 8. Approval Gate Results

### 8.1 Two-Phase Approval Flow

```
Phase 1: Graph runs → stops at WAITING_APPROVAL (no draft created)
Phase 2: ApprovalService.approve_case() → creates draft → CLOSED
```

### 8.2 Approval Gate Test Results

| Test Case                           | Expected Behavior                        | Actual Behavior                          | Status |
|-------------------------------------|------------------------------------------|------------------------------------------|--------|
| Register pending state              | State stored, `is_pending()` = True      | State stored, `is_pending()` = True      | ✅ PASS |
| Approve creates draft               | Draft created, status = CLOSED           | Draft created, status = CLOSED           | ✅ PASS |
| Reject → no draft                   | No draft, reason recorded, CLOSED        | No draft, reason recorded, CLOSED        | ✅ PASS |
| Reject has audit trail              | `HUMAN_REJECTED` event with approver     | `HUMAN_REJECTED` event with approver     | ✅ PASS |
| Cannot approve unknown case         | Raises `CaseNotFoundError`               | Raises `CaseNotFoundError`               | ✅ PASS |
| Cannot reject unknown case          | Raises `CaseNotFoundError`               | Raises `CaseNotFoundError`               | ✅ PASS |
| Cannot approve twice                | Raises `AlreadyDecidedError`             | Raises `AlreadyDecidedError`             | ✅ PASS |
| Cannot reject after approve         | Raises `AlreadyDecidedError`             | Raises `AlreadyDecidedError`             | ✅ PASS |
| Cannot register non-WAITING state   | Raises `ValueError`                      | Raises `ValueError`                      | ✅ PASS |
| Get approval packet                 | Returns `ApprovalPacket` with amount     | Returns `ApprovalPacket` (450,000₫)      | ✅ PASS |
| Approve has complete audit trail    | Events: REQUESTED → APPROVED → DRAFT → CLOSED | All 4 events present           | ✅ PASS |

### 8.3 Which Actions Require Approval

| Action                                | Approval Required | Risk Level        | Tested |
|---------------------------------------|-------------------|-------------------|--------|
| `create_refund_request_draft` (<2M₫)  | ✅ YES            | MEDIUM            | ✅ Yes |
| `create_refund_request_draft` (≥2M₫)  | ✅ YES            | HIGH              | ✅ Yes |
| `manual_review`                       | ✅ YES            | HIGH              | ✅ Yes |
| `create_reconciliation_ticket_draft`  | ❌ NO             | LOW               | ✅ Yes |
| `draft_customer_response`             | ❌ NO             | LOW               | ✅ Yes |
| `wait_sla`                            | ❌ NO             | LOW               | ✅ Yes |
| `no_action`                           | ❌ NO             | LOW               | ✅ Yes |

---

## 9. Idempotency Results

### 9.1 Idempotency Key Generation

Key formula: `sha256(transaction_id + ":" + action_type + ":" + amount)[:16]`

| Test Case                      | Expected                 | Actual                   | Status |
|--------------------------------|--------------------------|--------------------------|--------|
| Same inputs → same key         | Deterministic            | Deterministic            | ✅ PASS |
| Different txn_id → different key| Different key            | Different key            | ✅ PASS |
| Different amount → different key| Different key            | Different key            | ✅ PASS |
| Key length = 16 chars           | 16                       | 16                       | ✅ PASS |

### 9.2 Duplicate Action Prevention

| Scenario                             | `is_duplicate_action()` | Tested | Status |
|--------------------------------------|-------------------------|--------|--------|
| Refund already `executed`            | `True` (blocked)        | ✅ Yes | ✅ PASS |
| Refund already `requested`           | `True` (blocked)        | ✅ Yes | ✅ PASS |
| Refund already `approved`            | `True` (blocked)        | ✅ Yes | ✅ PASS |
| Refund `not_requested` (new)         | `False` (allowed)       | ✅ Yes | ✅ PASS |
| Non-refund action (e.g. response)    | `False` (always allowed)| ✅ Yes | ✅ PASS |
| Draft tool blocks if store has dup   | Raises `DuplicateActionError` | ✅ Yes | ✅ PASS |
| Draft tool blocks if refund executed | Raises `DuplicateActionError` | ✅ Yes | ✅ PASS |
| Draft tool blocks if refund requested| Raises `DuplicateActionError` | ✅ Yes | ✅ PASS |

### 9.3 End-to-End Duplicate Prevention (REFUND_001)

| Test                                    | Expected               | Actual                 | Status |
|-----------------------------------------|------------------------|------------------------|--------|
| `test_refund_001_no_duplicate`         | Action = `no_action`   | Action = `no_action`   | ✅ PASS |
| Diagnosis includes "refund_not_eligible"| Yes                    | Yes                    | ✅ PASS |

---

## 10. Aggregate Metrics

### 10.1 Computed Metrics (from actual test runs)

| Metric                                | Formula                                     | Value      | Source             |
|---------------------------------------|---------------------------------------------|------------|--------------------|
| `workflow_routing_accuracy`           | Correct routes / total routing tests        | **4/4 = 100%** | Unit tests     |
| `action_recommendation_accuracy`      | Correct actions / total scenario tests      | **8/8 = 100%** | Unit + integration |
| `approval_gate_accuracy`              | Correct approval decisions / total          | **11/11 = 100%** | Integration tests |
| `no_money_action_without_approval`    | Refund/money actions blocked without approval | **6/6 = 100%** | Safety + tool tests |
| `duplicate_refund_prevention`         | Duplicate actions correctly blocked         | **8/8 = 100%** | Idempotency tests |
| `conflict_detection_pass_rate`        | Conflicts correctly detected or not detected| **9/9 = 100%** | Conflict tests |

### 10.2 Metric Details

#### `workflow_routing_accuracy` — 100%

- 4 service types tested: `train_ticket`, `electric_bill`, `water_bill`, `unknown`
- All correctly routed to expected workflow
- **Method:** Automated (pytest)

#### `action_recommendation_accuracy` — 100%

- 8 unique scenarios tested with deterministic rules
- All match expected action from decision matrix
- Covers: refund draft, customer response, reconciliation ticket, manual review, wait_sla, no_action
- **Method:** Automated (pytest unit + integration + demo script)

#### `approval_gate_accuracy` — 100%

- 11 approval-related test cases
- Covers: approve, reject, double-approve, unknown case, non-waiting state, audit trail
- **Method:** Automated (pytest integration)

#### `no_money_action_without_approval` — 100%

- 6 forbidden actions blocklisted and tested
- `SafetyViolation` raised for every forbidden action
- No `execute_refund` tool exists in registry
- All refund drafts require approval before creation
- **Method:** Automated (pytest safety + tools)

#### `duplicate_refund_prevention` — 100%

- 8 duplicate detection scenarios tested
- Covers: idempotency key generation, `is_duplicate_action()`, tool-level blocking
- End-to-end REFUND_001 integration test confirms duplicate prevention
- **Method:** Automated (pytest unit + integration)

#### `conflict_detection_pass_rate` — 100%

- 9 conflict detection test cases (4 positive, 4 negative, 1 multi-conflict)
- All conflicts correctly detected or correctly absent
- End-to-end CONFLICT_001 integration test confirms routing to manual_review
- **Method:** Automated (pytest unit + integration)

### 10.3 Metrics Not Yet Automated

| Metric                        | Status              | Notes                                    |
|-------------------------------|---------------------|------------------------------------------|
| LLM extraction accuracy       | ⚠️ Manual check     | MVP uses regex, not real LLM. Accuracy depends on complaint format. |
| SLA compliance rate            | ⚠️ Not implemented  | SLA timeout tracking not in MVP scope.   |
| Reopen flow correctness        | ⚠️ Manual check     | Schema supports reopen, but full flow not wired in graph. |
| Production latency (p50/p99)   | ⚠️ Not measured     | No load testing in MVP. In-memory mock tools return instantly. |
| Concurrent case handling       | ⚠️ Not tested       | Single-threaded in MVP. ApprovalService uses in-memory dict. |

---

## 11. Known Limitations

### 11.1 Design Limitations (Intentional)

| Limitation                          | Reason                                               |
|-------------------------------------|------------------------------------------------------|
| Agent never executes refunds        | Safety-by-design. Only creates drafts for human approval. |
| In-memory storage only              | MVP does not require persistence. Production will use PostgreSQL/Redis. |
| Mock tool responses are static      | No randomized failures or latency in mock tools.     |

### 11.2 Technical Limitations

| Limitation                                    | Impact                                    | Severity |
|-----------------------------------------------|-------------------------------------------|----------|
| Regex-based extraction instead of LLM         | Cannot handle unstructured/ambiguous complaints | Medium |
| `not_confirmed ≠ failed` relies on exact enum | Provider integration must map accurately  | High     |
| No SLA timeout handling                        | `wait_sla` cases have no follow-up trigger| Medium   |
| No retry with exponential backoff              | Mock tools don't fail, so not exercised   | Low      |
| Reopen flow schema exists but graph incomplete | Cannot reopen after close in current graph| Medium   |
| Single approval tier                           | No escalation to manager for HIGH risk    | Low      |
| No multi-tenant isolation                      | All cases share one audit logger instance | Low      |
| ApprovalPacket excludes `model_confidence`     | By design — prevents reviewer bias        | None     |

### 11.3 Test Coverage Gaps

| Area                            | Coverage Status      | Notes                             |
|---------------------------------|----------------------|-----------------------------------|
| Train ticket decision matrix    | 6/7 branches covered | `booking_failed` not explicitly tested (covered by refund_check logic) |
| Utility bill decision matrix    | 7/8 branches covered | `provider_no_record` for utility not explicitly tested |
| Edge case: zero amount refund   | ✅ Tested            | `test_zero_amount_rejected`       |
| Edge case: empty evidence       | ✅ Tested            | `test_empty_evidence_rejected`    |
| Edge case: missing txn_id       | ✅ Tested            | `test_missing_transaction_id` (dead letter) |
| Concurrent approval requests    | ❌ Not tested        | In-memory dict, no locking        |
| High-risk refund (≥2M₫)         | ✅ Unit test only     | `test_refund_large_amount_high_risk` |

---

## 12. Next Steps

### 12.1 Immediate (Post-MVP)

| Priority | Task                                       | Effort |
|----------|--------------------------------------------|--------|
| P0       | Integrate real LLM for complaint extraction | 3-5 days |
| P0       | Connect to real provider APIs               | 3-5 days |
| P1       | Add PostgreSQL persistence layer            | 2-3 days |
| P1       | Implement SLA timeout + follow-up trigger   | 2 days |
| P1       | Complete reopen flow in LangGraph           | 1-2 days |
| P2       | Add load testing (locust/k6)               | 1-2 days |
| P2       | Add escalation tier for HIGH risk           | 1 day |

### 12.2 Quality Improvements

| Task                                           | Metric Impact                    |
|------------------------------------------------|----------------------------------|
| Add property-based testing (Hypothesis)         | Wider coverage of edge cases     |
| Add booking_failed + provider_no_record tests   | Fill remaining branch gaps       |
| Fuzz test prompt injection detector             | Improve detection robustness     |
| Add concurrent approval test with threading     | Validate thread safety           |

### 12.3 Production Readiness

| Task                                     | Requirement For             |
|------------------------------------------|-----------------------------|
| Structured logging (JSON) to observability stack | Production monitoring  |
| Secrets management (vault integration)   | Provider API credentials     |
| Rate limiting on API endpoints           | DoS protection               |
| Circuit breaker for provider calls       | Reliability                  |
| Blue-green deployment config             | Zero-downtime deploys        |

---

## Appendix A: How to Run Tests

```bash
# All tests (336 tests, ~0.25s)
python -m pytest tests/ -v

# Unit tests only
python -m pytest tests/unit/ -v

# Integration tests only
python -m pytest tests/integration/ -v

# Rule engine tests only
python -m pytest tests/unit/test_rules.py -v

# Safety tests only
python -m pytest tests/unit/test_safety.py -v

# Demo scenarios
python scripts/run_demo_cases.py

# Single demo scenario
python scripts/run_demo_cases.py TRAIN_001
```

## Appendix B: Test Execution Evidence

```
$ python -m pytest tests/ -v --tb=short

============================= 336 passed in 0.25s ==============================
```

```
$ python scripts/run_demo_cases.py

  SUMMARY — ALL DEMO CASES
  Case               Action                               Approval   Risk     Status          ✓/✗
  ────────────────────────────────────────────────────────────────────────────────────────────
  CASE-TRAIN-001     create_refund_request_draft          YES        medium   closed          PASS
  CASE-TRAIN-002     draft_customer_response              NO         low      closed          PASS
  CASE-BILL-002      create_reconciliation_ticket_draft   NO         low      closed          PASS
  CASE-BILL-003      create_refund_request_draft          YES        medium   closed          PASS
  CASE-CONFLICT-001  manual_review                        YES        high     manual_review   PASS

  🎉 All 5 scenarios PASSED!
  Total audit events logged: 85
```

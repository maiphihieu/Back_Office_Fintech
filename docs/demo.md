# MVP Demo — Rule Engine Scenarios

## Quick Start

```bash
# Run all 5 demo scenarios
python scripts/run_demo_cases.py

# Run a specific case
python scripts/run_demo_cases.py TRAIN_001

# Run multiple cases
python scripts/run_demo_cases.py TRAIN_001 BILL_003

# List available scenarios
python scripts/run_demo_cases.py --list
```

## Scenarios

| Case ID       | Description                                     | Expected Action                        | Approval |
|---------------|------------------------------------------------|----------------------------------------|----------|
| `TRAIN_001`   | Debited + ticket_not_issued                    | `create_refund_request_draft`          | ✅ YES    |
| `TRAIN_002`   | Ticket issued → customer response              | `draft_customer_response`              | ❌ NO     |
| `BILL_002`    | Provider not_confirmed → reconciliation ticket | `create_reconciliation_ticket_draft`   | ❌ NO     |
| `BILL_003`    | Provider failed → refund draft                 | `create_refund_request_draft`          | ✅ YES    |
| `CONFLICT_001`| Ledger↔txn conflict → manual review            | `manual_review`                        | ✅ YES    |

## What the Script Tests

Each scenario exercises the **full case lifecycle** without any API calls or refund execution:

1. **Case intake** — creates CaseState with raw complaint
2. **Info extraction** — simulates LLM extraction (user_id, txn_id, service_type, issue_type)
3. **Evidence fetching** — simulates tool calls (get_transaction, get_wallet_ledger, get_provider_status, get_refund_status)
4. **Conflict detection** — runs `detect_all_conflicts()` cross-checking wallet↔txn, provider↔ticket, refund↔ledger
5. **Workflow routing** — selects `train_ticket_workflow` or `utility_bill_workflow`
6. **Rule decision** — calls `decide_train_ticket()` or `decide_utility_bill()` deterministic decision matrix
7. **Risk classification** — calls `classify_risk()` + `requires_approval()`
8. **Approval gate** — simulates human approval for demo
9. **Draft creation** — logs draft creation event
10. **Case close** — transitions to final status
11. **Audit trail** — all steps logged via `AuditLogger`

## Output Fields

For each case, the script prints:

| Field                | Description                                                       |
|----------------------|-------------------------------------------------------------------|
| Case Input           | Case ID, user, transaction, service type, issue type, complaint   |
| Extracted Info       | Order ID, bill code, customer code, amount from wallet            |
| Evidence Summary     | Wallet status, txn status, provider status, refund status         |
| Selected Workflow    | Which workflow was selected by the router                         |
| Conflict Status      | Whether conflicts were detected + details                         |
| Rule Decision        | The deterministic action from the rule engine                     |
| Diagnosis            | Machine-readable diagnosis string                                 |
| Recommended Action   | Final action (refund draft, customer response, reconciliation, manual review) |
| Risk Level           | LOW / MEDIUM / HIGH / CRITICAL                                    |
| Approval Required    | Whether human approval is needed                                  |
| Audit Event Count    | Number of audit events logged for this case                       |
| Final Status         | CLOSED or MANUAL REVIEW                                           |

---

## Sample Output

```
╔══════════════════════════════════════════════════════════════════════╗
║  FINTECH BACK-OFFICE AGENT — MVP DEMO RUNNER                       ║
║  No API calls • No refund execution • Pure rule engine demo        ║
║  2026-05-27 15:46:29 UTC                                           ║
╚══════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [CASE-TRAIN-001]  Train ticket — debited, ticket NOT issued → REFUND DRAFT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Customer paid 350,000 VND but train ticket was never issued. Wallet debited,
  provider confirms ticket_not_issued. Rule engine should create refund request
  draft + require approval.

  ▸ CASE INPUT
    │ Case ID                  CASE-TRAIN-001
    │ User ID                  USER-8899
    │ Transaction ID           TXN-20260527-001
    │ Service Type             train_ticket
    │ Issue Type               paid_but_no_ticket
    │ Complaint                Tôi đã thanh toán vé tàu 350,000 VND, mã giao dịch TXN-20260527-001...

  ▸ EXTRACTED INFO
    │ Order ID                 ORD-VT-55001
    │ Bill Code                —
    │ Customer Code            —
    │ Amount (wallet)          350,000₫

  ▸ EVIDENCE SUMMARY
    │ • wallet: debited, debit=350,000₫, refunded=False
    │ • txn: status=completed, amount=350,000₫
    │ • train_provider: ticket_not_issued, ticket_code=None
    │ • refund: not_requested

  ▸ WORKFLOW & DECISION
    │ Selected Workflow        train_ticket_workflow
    │ Conflict Status          ✔ No conflicts
    │ Rule Decision            create_refund_request_draft
    │ Diagnosis                wallet_debited_ticket_not_issued (all checks passed — refund draft eligible)
    │ Recommended Action       ⚡ create_refund_request_draft
    │ Risk Level               MEDIUM
    │ Approval Required        ⚠ YES — human approval required

  ▸ AUDIT & STATUS
    │ Audit Event Count        18
    │ Final Status             ✔ CLOSED

  ▸ EXPECTATION CHECK
    │ ✔ Action matches expected: create_refund_request_draft
    │ ✔ Approval matches expected: True


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [CASE-TRAIN-002]  Train ticket — debited, ticket ISSUED → CUSTOMER RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Customer claims no ticket, but provider confirms ticket was issued with a
  valid ticket code. Wallet debited correctly. Rule engine should draft
  customer response with ticket info.

  ▸ CASE INPUT
    │ Case ID                  CASE-TRAIN-002
    │ User ID                  USER-7712
    │ Transaction ID           TXN-20260527-002
    │ Service Type             train_ticket
    │ Issue Type               paid_but_no_ticket
    │ Complaint                Tôi mua vé tàu 200,000 VND, mã TXN-20260527-002...

  ▸ EXTRACTED INFO
    │ Order ID                 ORD-VT-55002
    │ Bill Code                —
    │ Customer Code            —
    │ Amount (wallet)          200,000₫

  ▸ EVIDENCE SUMMARY
    │ • wallet: debited, debit=200,000₫, refunded=False
    │ • txn: status=completed, amount=200,000₫
    │ • train_provider: ticket_issued, ticket_code=VE-TAU-ABC123
    │ • refund: not_requested

  ▸ WORKFLOW & DECISION
    │ Selected Workflow        train_ticket_workflow
    │ Conflict Status          ✔ No conflicts
    │ Rule Decision            draft_customer_response
    │ Diagnosis                ticket_issued_with_code
    │ Recommended Action       💬 draft_customer_response
    │ Risk Level               LOW
    │ Approval Required        ✔ NO — auto-proceed

  ▸ AUDIT & STATUS
    │ Audit Event Count        15
    │ Final Status             ✔ CLOSED

  ▸ EXPECTATION CHECK
    │ ✔ Action matches expected: draft_customer_response
    │ ✔ Approval matches expected: False


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [CASE-BILL-002]  Utility bill — debited, provider NOT CONFIRMED → RECONCILIATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Customer paid electric bill 500,000 VND, wallet debited, but provider says
  'not_confirmed'. This is NOT a failure — provider may still process. Rule
  engine creates reconciliation ticket.

  ▸ CASE INPUT
    │ Case ID                  CASE-BILL-002
    │ User ID                  USER-6634
    │ Transaction ID           TXN-20260527-003
    │ Service Type             electric_bill
    │ Issue Type               paid_but_provider_not_confirmed
    │ Complaint                Tôi đã thanh toán hóa đơn tiền điện 500,000 VND...

  ▸ EXTRACTED INFO
    │ Order ID                 —
    │ Bill Code                BILL-EVN-90123
    │ Customer Code            PE-HCM-44556
    │ Amount (wallet)          500,000₫

  ▸ EVIDENCE SUMMARY
    │ • wallet: debited, debit=500,000₫, refunded=False
    │ • txn: status=completed, amount=500,000₫
    │ • utility_provider: not_confirmed, bill=BILL-EVN-90123
    │ • refund: not_requested

  ▸ WORKFLOW & DECISION
    │ Selected Workflow        utility_bill_workflow
    │ Conflict Status          ✔ No conflicts
    │ Rule Decision            create_reconciliation_ticket_draft
    │ Diagnosis                provider_not_confirmed_needs_reconciliation
    │ Recommended Action       🔄 create_reconciliation_ticket_draft
    │ Risk Level               LOW
    │ Approval Required        ✔ NO — auto-proceed

  ▸ AUDIT & STATUS
    │ Audit Event Count        16
    │ Final Status             ✔ CLOSED

  ▸ EXPECTATION CHECK
    │ ✔ Action matches expected: create_reconciliation_ticket_draft
    │ ✔ Approval matches expected: False


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [CASE-BILL-003]  Utility bill — debited, provider FAILED → REFUND DRAFT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Customer paid water bill 320,000 VND, wallet debited, but provider explicitly
  reports 'failed'. Rule engine should create refund request draft + require
  approval.

  ▸ CASE INPUT
    │ Case ID                  CASE-BILL-003
    │ User ID                  USER-5521
    │ Transaction ID           TXN-20260527-004
    │ Service Type             water_bill
    │ Issue Type               provider_failed
    │ Complaint                Tôi thanh toán hóa đơn nước 320,000 VND qua ví...

  ▸ EXTRACTED INFO
    │ Order ID                 —
    │ Bill Code                BILL-WTR-78901
    │ Customer Code            NM-HN-33221
    │ Amount (wallet)          320,000₫

  ▸ EVIDENCE SUMMARY
    │ • wallet: debited, debit=320,000₫, refunded=False
    │ • txn: status=completed, amount=320,000₫
    │ • utility_provider: failed, bill=BILL-WTR-78901
    │ • refund: not_requested

  ▸ WORKFLOW & DECISION
    │ Selected Workflow        utility_bill_workflow
    │ Conflict Status          ✔ No conflicts
    │ Rule Decision            create_refund_request_draft
    │ Diagnosis                provider_failed_wallet_debited (all checks passed — refund draft eligible)
    │ Recommended Action       ⚡ create_refund_request_draft
    │ Risk Level               MEDIUM
    │ Approval Required        ⚠ YES — human approval required

  ▸ AUDIT & STATUS
    │ Audit Event Count        18
    │ Final Status             ✔ CLOSED

  ▸ EXPECTATION CHECK
    │ ✔ Action matches expected: create_refund_request_draft
    │ ✔ Approval matches expected: True


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [CASE-CONFLICT-001]  Evidence conflict — wallet debited but txn pending → MANUAL REVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Wallet ledger shows debit of 450,000 VND, but transaction status is still
  'pending'. Data inconsistency detected. Rule engine must route to manual
  review, NOT auto-diagnose.

  ▸ CASE INPUT
    │ Case ID                  CASE-CONFLICT-001
    │ User ID                  USER-4408
    │ Transaction ID           TXN-20260527-005
    │ Service Type             train_ticket
    │ Issue Type               paid_but_no_ticket
    │ Complaint                Tôi đã mua vé tàu 450,000 VND, mã TXN-20260527-005...

  ▸ EXTRACTED INFO
    │ Order ID                 ORD-VT-55005
    │ Bill Code                —
    │ Customer Code            —
    │ Amount (wallet)          450,000₫

  ▸ EVIDENCE SUMMARY
    │ • wallet: debited, debit=450,000₫, refunded=False
    │ • txn: status=pending, amount=450,000₫
    │ • train_provider: ticket_not_issued, ticket_code=None
    │ • refund: not_requested

  ▸ WORKFLOW & DECISION
    │ Selected Workflow        train_ticket_workflow
    │ Conflict Status          ✘ CONFLICT DETECTED
    │   ⚡ wallet_ledger↔transaction: Wallet ledger shows debit but
    │     transaction status is still pending. Data inconsistency —
    │     route to manual review.
    │ Rule Decision            manual_review
    │ Diagnosis                conflict_detected
    │ Recommended Action       🛑 manual_review
    │ Risk Level               HIGH
    │ Approval Required        ⚠ YES — human approval required

  ▸ AUDIT & STATUS
    │ Audit Event Count        18
    │ Final Status             🛑 MANUAL REVIEW

  ▸ EXPECTATION CHECK
    │ ✔ Action matches expected: manual_review
    │ ✔ Approval matches expected: True


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SUMMARY — ALL DEMO CASES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Case               Action                               Approval   Risk     Status          ✓/✗
  ────────────────────────────────────────────────────────────────────────────────────────────
  CASE-TRAIN-001     create_refund_request_draft          YES        medium   closed          PASS
  CASE-TRAIN-002     draft_customer_response              NO         low      closed          PASS
  CASE-BILL-002      create_reconciliation_ticket_draft   NO         low      closed          PASS
  CASE-BILL-003      create_refund_request_draft          YES        medium   closed          PASS
  CASE-CONFLICT-001  manual_review                        YES        high     manual_review   PASS
  ────────────────────────────────────────────────────────────────────────────────────────────

  🎉 All 5 scenarios PASSED!

  Total audit events logged: 85
```

## Key Design Decisions

1. **No API calls** — All data is mock, constructed in-memory from `DemoScenario` dataclasses
2. **No refund execution** — Agent only creates *drafts* (safety-by-design)
3. **Deterministic rules** — Decision matrices from `train_ticket_rules.py` and `utility_bill_rules.py`
4. **Conflict detection** — `conflict_rules.py` catches wallet↔transaction, provider↔ticket, refund↔ledger mismatches
5. **Audit trail** — Every step logged via `AuditLogger` (85 events across 5 cases)
6. **Expectation checks** — Script verifies results against expected action + approval flag

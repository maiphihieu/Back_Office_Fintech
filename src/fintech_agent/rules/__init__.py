"""Rule engine — deterministic business logic, no LLM.

Modules:
    source_of_truth_rules  — trust hierarchy (wallet > refund > provider > txn)
    conflict_rules         — cross-source conflict detection
    refund_rules           — refund eligibility checks
    train_ticket_rules     — train ticket workflow decision matrix
    utility_bill_rules     — utility bill workflow decision matrix
    risk_rules             — risk classification and approval requirements
    idempotency_rules      — duplicate action prevention
"""

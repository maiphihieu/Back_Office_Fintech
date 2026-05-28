#!/usr/bin/env python3
"""Demo CLI — run all MVP scenarios through the fintech agent rule engine.

Usage:
    python scripts/run_demo_cases.py              # Run all 5 cases
    python scripts/run_demo_cases.py TRAIN_001     # Run single case
    python scripts/run_demo_cases.py --list        # List available cases

No API calls. No refund execution. Pure deterministic rule engine demo.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ── Ensure src/ is importable ────────────────────────────────────────
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fintech_agent.audit import AuditLogger
from fintech_agent.rules.conflict_rules import detect_all_conflicts
from fintech_agent.rules.risk_rules import classify_risk, requires_approval
from fintech_agent.rules.train_ticket_rules import decide_train_ticket
from fintech_agent.rules.utility_bill_rules import decide_utility_bill
from fintech_agent.schemas import (
    ActionType,
    AuditEventType,
    CaseState,
    CaseStatus,
    EvidenceBundle,
    EvidenceConflict,
    ExtractedInfo,
    IssueType,
    ProviderStatusValue,
    RefundStatus,
    RefundStatusValue,
    ServiceType,
    TrainProviderStatus,
    Transaction,
    UtilityProviderStatus,
    WalletLedger,
    WalletLedgerEntry,
    WalletLedgerStatus,
)


# ═══════════════════════════════════════════════════════════════════════
# ANSI color helpers
# ═══════════════════════════════════════════════════════════════════════

class C:
    """ANSI color codes for terminal output."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    WHITE   = "\033[97m"
    BG_DARK = "\033[48;5;236m"

    @staticmethod
    def header(text: str) -> str:
        return f"\n{C.BOLD}{C.CYAN}{'━' * 72}{C.RESET}\n{C.BOLD}{C.WHITE}  {text}{C.RESET}\n{C.BOLD}{C.CYAN}{'━' * 72}{C.RESET}"

    @staticmethod
    def section(text: str) -> str:
        return f"\n  {C.BOLD}{C.BLUE}▸ {text}{C.RESET}"

    @staticmethod
    def kv(key: str, value: Any, color: str = "") -> str:
        c = color or C.WHITE
        return f"    {C.DIM}│{C.RESET} {C.YELLOW}{key:<24}{C.RESET} {c}{value}{C.RESET}"

    @staticmethod
    def ok(text: str) -> str:
        return f"{C.GREEN}✔ {text}{C.RESET}"

    @staticmethod
    def warn(text: str) -> str:
        return f"{C.YELLOW}⚠ {text}{C.RESET}"

    @staticmethod
    def err(text: str) -> str:
        return f"{C.RED}✘ {text}{C.RESET}"

    @staticmethod
    def tag(text: str) -> str:
        return f"{C.BOLD}{C.MAGENTA}[{text}]{C.RESET}"


# ═══════════════════════════════════════════════════════════════════════
# Scenario definition
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DemoScenario:
    """One complete test scenario with mock data and expected outcome."""
    case_id: str
    title: str
    description: str
    raw_complaint: str
    extracted_info: ExtractedInfo
    transaction: Transaction
    wallet_ledger: WalletLedger
    train_provider: TrainProviderStatus | None = None
    utility_provider: UtilityProviderStatus | None = None
    refund_status: RefundStatus | None = None
    inject_conflicts: list[EvidenceConflict] = field(default_factory=list)
    expected_action: str = ""
    expected_approval: bool = False


# ═══════════════════════════════════════════════════════════════════════
# Scenario factory
# ═══════════════════════════════════════════════════════════════════════

def build_scenarios() -> dict[str, DemoScenario]:
    """Build all 5 MVP demo scenarios."""

    scenarios: dict[str, DemoScenario] = {}

    # ── TRAIN_001: debited + ticket_not_issued → refund draft + approval ──
    scenarios["TRAIN_001"] = DemoScenario(
        case_id="CASE-TRAIN-001",
        title="Train ticket — debited, ticket NOT issued → REFUND DRAFT",
        description="Customer paid 350,000 VND but train ticket was never issued. "
                    "Wallet debited, provider confirms ticket_not_issued. "
                    "Rule engine should create refund request draft + require approval.",
        raw_complaint=(
            "Tôi đã thanh toán vé tàu 350,000 VND, mã giao dịch TXN-20260527-001, "
            "nhưng không nhận được vé. Ví đã bị trừ tiền. Yêu cầu hoàn tiền."
        ),
        extracted_info=ExtractedInfo(
            user_id="USER-8899",
            transaction_id="TXN-20260527-001",
            service_type=ServiceType.TRAIN_TICKET,
            issue_type=IssueType.PAID_BUT_NO_TICKET,
            order_id="ORD-VT-55001",
        ),
        transaction=Transaction(
            transaction_id="TXN-20260527-001",
            user_id="USER-8899",
            service_type="train_ticket",
            amount=350_000,
            status="completed",
            order_id="ORD-VT-55001",
            provider_ref_id="PROV-VT-7001",
        ),
        wallet_ledger=WalletLedger(
            transaction_id="TXN-20260527-001",
            user_id="USER-8899",
            status=WalletLedgerStatus.DEBITED,
            has_user_debit=True,
            debit_amount=350_000,
            has_credit_refund=False,
            credit_refund_amount=0,
            net_amount=-350_000,
            entries=[
                WalletLedgerEntry(entry_type="debit", amount=350_000, balance_after=1_650_000),
            ],
        ),
        train_provider=TrainProviderStatus(
            provider_ref_id="PROV-VT-7001",
            booking_status=ProviderStatusValue.TICKET_NOT_ISSUED,
            ticket_code=None,
        ),
        refund_status=RefundStatus(
            transaction_id="TXN-20260527-001",
            refund_status=RefundStatusValue.NOT_REQUESTED,
        ),
        expected_action="create_refund_request_draft",
        expected_approval=True,
    )

    # ── TRAIN_002: ticket_issued → customer response, no refund ──────
    scenarios["TRAIN_002"] = DemoScenario(
        case_id="CASE-TRAIN-002",
        title="Train ticket — debited, ticket ISSUED → CUSTOMER RESPONSE",
        description="Customer claims no ticket, but provider confirms ticket was issued "
                    "with a valid ticket code. Wallet debited correctly. "
                    "Rule engine should draft customer response with ticket info.",
        raw_complaint=(
            "Tôi mua vé tàu 200,000 VND, mã TXN-20260527-002, "
            "nhưng chưa nhận được vé. Xin kiểm tra giúp."
        ),
        extracted_info=ExtractedInfo(
            user_id="USER-7712",
            transaction_id="TXN-20260527-002",
            service_type=ServiceType.TRAIN_TICKET,
            issue_type=IssueType.PAID_BUT_NO_TICKET,
            order_id="ORD-VT-55002",
        ),
        transaction=Transaction(
            transaction_id="TXN-20260527-002",
            user_id="USER-7712",
            service_type="train_ticket",
            amount=200_000,
            status="completed",
            order_id="ORD-VT-55002",
            provider_ref_id="PROV-VT-7002",
        ),
        wallet_ledger=WalletLedger(
            transaction_id="TXN-20260527-002",
            user_id="USER-7712",
            status=WalletLedgerStatus.DEBITED,
            has_user_debit=True,
            debit_amount=200_000,
            has_credit_refund=False,
            credit_refund_amount=0,
            net_amount=-200_000,
            entries=[
                WalletLedgerEntry(entry_type="debit", amount=200_000, balance_after=800_000),
            ],
        ),
        train_provider=TrainProviderStatus(
            provider_ref_id="PROV-VT-7002",
            booking_status=ProviderStatusValue.TICKET_ISSUED,
            ticket_code="VE-TAU-ABC123",
        ),
        refund_status=RefundStatus(
            transaction_id="TXN-20260527-002",
            refund_status=RefundStatusValue.NOT_REQUESTED,
        ),
        expected_action="draft_customer_response",
        expected_approval=False,
    )

    # ── BILL_002: provider not_confirmed → reconciliation ticket ─────
    scenarios["BILL_002"] = DemoScenario(
        case_id="CASE-BILL-002",
        title="Utility bill — debited, provider NOT CONFIRMED → RECONCILIATION",
        description="Customer paid electric bill 500,000 VND, wallet debited, "
                    "but provider says 'not_confirmed'. This is NOT a failure — "
                    "provider may still process. Rule engine creates reconciliation ticket.",
        raw_complaint=(
            "Tôi đã thanh toán hóa đơn tiền điện 500,000 VND, mã TXN-20260527-003, "
            "nhưng nhà cung cấp chưa xác nhận. Xin kiểm tra."
        ),
        extracted_info=ExtractedInfo(
            user_id="USER-6634",
            transaction_id="TXN-20260527-003",
            service_type=ServiceType.ELECTRIC_BILL,
            issue_type=IssueType.PAID_BUT_PROVIDER_NOT_CONFIRMED,
            bill_code="BILL-EVN-90123",
            customer_code="PE-HCM-44556",
        ),
        transaction=Transaction(
            transaction_id="TXN-20260527-003",
            user_id="USER-6634",
            service_type="electric_bill",
            amount=500_000,
            status="completed",
            bill_code="BILL-EVN-90123",
            customer_code="PE-HCM-44556",
            provider_ref_id="PROV-EVN-3001",
        ),
        wallet_ledger=WalletLedger(
            transaction_id="TXN-20260527-003",
            user_id="USER-6634",
            status=WalletLedgerStatus.DEBITED,
            has_user_debit=True,
            debit_amount=500_000,
            has_credit_refund=False,
            credit_refund_amount=0,
            net_amount=-500_000,
            entries=[
                WalletLedgerEntry(entry_type="debit", amount=500_000, balance_after=2_500_000),
            ],
        ),
        utility_provider=UtilityProviderStatus(
            provider_ref_id="PROV-EVN-3001",
            provider_status=ProviderStatusValue.NOT_CONFIRMED,
            bill_code="BILL-EVN-90123",
            customer_code="PE-HCM-44556",
            amount=500_000,
        ),
        refund_status=RefundStatus(
            transaction_id="TXN-20260527-003",
            refund_status=RefundStatusValue.NOT_REQUESTED,
        ),
        expected_action="create_reconciliation_ticket_draft",
        expected_approval=False,
    )

    # ── BILL_003: provider failed → refund draft + approval ──────────
    scenarios["BILL_003"] = DemoScenario(
        case_id="CASE-BILL-003",
        title="Utility bill — debited, provider FAILED → REFUND DRAFT",
        description="Customer paid water bill 320,000 VND, wallet debited, "
                    "but provider explicitly reports 'failed'. "
                    "Rule engine should create refund request draft + require approval.",
        raw_complaint=(
            "Tôi thanh toán hóa đơn nước 320,000 VND qua ví, mã TXN-20260527-004, "
            "nhà cung cấp báo giao dịch thất bại. Yêu cầu hoàn tiền."
        ),
        extracted_info=ExtractedInfo(
            user_id="USER-5521",
            transaction_id="TXN-20260527-004",
            service_type=ServiceType.WATER_BILL,
            issue_type=IssueType.PROVIDER_FAILED,
            bill_code="BILL-WTR-78901",
            customer_code="NM-HN-33221",
        ),
        transaction=Transaction(
            transaction_id="TXN-20260527-004",
            user_id="USER-5521",
            service_type="water_bill",
            amount=320_000,
            status="completed",
            bill_code="BILL-WTR-78901",
            customer_code="NM-HN-33221",
            provider_ref_id="PROV-WTR-4001",
        ),
        wallet_ledger=WalletLedger(
            transaction_id="TXN-20260527-004",
            user_id="USER-5521",
            status=WalletLedgerStatus.DEBITED,
            has_user_debit=True,
            debit_amount=320_000,
            has_credit_refund=False,
            credit_refund_amount=0,
            net_amount=-320_000,
            entries=[
                WalletLedgerEntry(entry_type="debit", amount=320_000, balance_after=1_180_000),
            ],
        ),
        utility_provider=UtilityProviderStatus(
            provider_ref_id="PROV-WTR-4001",
            provider_status=ProviderStatusValue.FAILED,
            bill_code="BILL-WTR-78901",
            customer_code="NM-HN-33221",
            amount=320_000,
        ),
        refund_status=RefundStatus(
            transaction_id="TXN-20260527-004",
            refund_status=RefundStatusValue.NOT_REQUESTED,
        ),
        expected_action="create_refund_request_draft",
        expected_approval=True,
    )

    # ── CONFLICT_001: ledger↔transaction conflict → manual review ────
    scenarios["CONFLICT_001"] = DemoScenario(
        case_id="CASE-CONFLICT-001",
        title="Evidence conflict — wallet debited but txn pending → MANUAL REVIEW",
        description="Wallet ledger shows debit of 450,000 VND, but transaction status "
                    "is still 'pending'. Data inconsistency detected. "
                    "Rule engine must route to manual review, NOT auto-diagnose.",
        raw_complaint=(
            "Tôi đã mua vé tàu 450,000 VND, mã TXN-20260527-005, ví bị trừ "
            "nhưng giao dịch vẫn hiển thị đang chờ. Yêu cầu hỗ trợ."
        ),
        extracted_info=ExtractedInfo(
            user_id="USER-4408",
            transaction_id="TXN-20260527-005",
            service_type=ServiceType.TRAIN_TICKET,
            issue_type=IssueType.PAID_BUT_NO_TICKET,
            order_id="ORD-VT-55005",
        ),
        transaction=Transaction(
            transaction_id="TXN-20260527-005",
            user_id="USER-4408",
            service_type="train_ticket",
            amount=450_000,
            status="pending",  # ← conflict: wallet debited but txn pending
            order_id="ORD-VT-55005",
            provider_ref_id="PROV-VT-7005",
        ),
        wallet_ledger=WalletLedger(
            transaction_id="TXN-20260527-005",
            user_id="USER-4408",
            status=WalletLedgerStatus.DEBITED,
            has_user_debit=True,
            debit_amount=450_000,
            has_credit_refund=False,
            credit_refund_amount=0,
            net_amount=-450_000,
            entries=[
                WalletLedgerEntry(entry_type="debit", amount=450_000, balance_after=550_000),
            ],
        ),
        train_provider=TrainProviderStatus(
            provider_ref_id="PROV-VT-7005",
            booking_status=ProviderStatusValue.TICKET_NOT_ISSUED,
            ticket_code=None,
        ),
        refund_status=RefundStatus(
            transaction_id="TXN-20260527-005",
            refund_status=RefundStatusValue.NOT_REQUESTED,
        ),
        expected_action="manual_review",
        expected_approval=True,
    )

    return scenarios


# ═══════════════════════════════════════════════════════════════════════
# Case runner
# ═══════════════════════════════════════════════════════════════════════

def run_scenario(scenario: DemoScenario, audit: AuditLogger) -> dict[str, Any]:
    """Execute a single demo scenario and return structured results."""

    result: dict[str, Any] = {}
    info = scenario.extracted_info
    correlation_id = audit.generate_correlation_id()

    # ── 1. Case intake ───────────────────────────────────────────
    audit.log_case_received(scenario.case_id, scenario.raw_complaint)

    # ── 2. Info extraction (simulated) ───────────────────────────
    audit.log_event(
        scenario.case_id,
        AuditEventType.INFO_EXTRACTED,
        details={
            "user_id": info.user_id or "",
            "transaction_id": info.transaction_id or "",
            "service_type": str(info.service_type) if info.service_type else "",
            "issue_type": str(info.issue_type) if info.issue_type else "",
        },
        correlation_id=correlation_id,
    )

    # ── 3. Build evidence bundle ─────────────────────────────────
    evidence = EvidenceBundle(
        transaction=scenario.transaction,
        wallet_ledger=scenario.wallet_ledger,
        train_provider=scenario.train_provider,
        utility_provider=scenario.utility_provider,
        refund_status=scenario.refund_status,
        conflicts=list(scenario.inject_conflicts),
    )

    # ── 4. Fetch evidence (simulated tool calls) ─────────────────
    for tool_name in ["get_transaction", "get_wallet_ledger", "get_provider_status", "get_refund_status"]:
        audit.log_tool_called(scenario.case_id, tool_name, correlation_id=correlation_id)
        audit.log_tool_result(scenario.case_id, tool_name, success=True, correlation_id=correlation_id)

    audit.log_event(
        scenario.case_id,
        AuditEventType.EVIDENCE_FETCH_STARTED,
        previous_status="extracting",
        new_status="fetching_evidence",
        correlation_id=correlation_id,
    )

    # ── 5. Conflict detection ────────────────────────────────────
    detected_conflicts = detect_all_conflicts(evidence, case_user_id=info.user_id)
    evidence.conflicts.extend(detected_conflicts)

    conflict_detected = evidence.has_conflicts
    result["conflict_detected"] = conflict_detected
    result["conflicts"] = [
        f"{c.source_a}↔{c.source_b}: {c.description}" for c in evidence.conflicts
    ]

    if conflict_detected:
        audit.log_event(
            scenario.case_id,
            AuditEventType.CONFLICT_DETECTED,
            details={"count": len(evidence.conflicts)},
            correlation_id=correlation_id,
        )

    # ── 6. Workflow routing ──────────────────────────────────────
    service_type = info.service_type
    if service_type == ServiceType.TRAIN_TICKET:
        selected_workflow = "train_ticket_workflow"
    elif service_type in {ServiceType.ELECTRIC_BILL, ServiceType.WATER_BILL}:
        selected_workflow = "utility_bill_workflow"
    else:
        selected_workflow = "unknown_workflow"

    result["selected_workflow"] = selected_workflow

    audit.log_event(
        scenario.case_id,
        AuditEventType.WORKFLOW_ROUTED,
        details={"workflow": selected_workflow},
        previous_status="fetching_evidence",
        new_status="routed",
        correlation_id=correlation_id,
    )

    # ── 7. Rule decision ─────────────────────────────────────────
    if selected_workflow == "train_ticket_workflow":
        decision = decide_train_ticket(
            ledger=scenario.wallet_ledger,
            provider=scenario.train_provider,
            refund=scenario.refund_status,
            evidence=evidence,
        )
        action = decision.action
        diagnosis = decision.diagnosis
        approval_flag = decision.approval_required
    elif selected_workflow == "utility_bill_workflow":
        decision = decide_utility_bill(
            ledger=scenario.wallet_ledger,
            provider=scenario.utility_provider,
            refund=scenario.refund_status,
            evidence=evidence,
        )
        action = decision.action
        diagnosis = decision.diagnosis
        approval_flag = decision.approval_required
    else:
        action = ActionType.MANUAL_REVIEW
        diagnosis = "unknown_service_type"
        approval_flag = True

    # Risk classification
    amount = scenario.wallet_ledger.debit_amount if scenario.wallet_ledger else 0
    risk_level = classify_risk(action, amount)
    needs_approval = requires_approval(action, amount)
    # Merge: rule engine flag OR risk engine flag
    final_approval = approval_flag or needs_approval

    result["rule_decision"] = str(action)
    result["diagnosis"] = diagnosis
    result["risk_level"] = str(risk_level)
    result["recommended_action"] = str(action)
    result["approval_required"] = final_approval

    audit.log_event(
        scenario.case_id,
        AuditEventType.RULE_APPLIED,
        details={"action": str(action), "diagnosis": diagnosis},
        correlation_id=correlation_id,
    )
    audit.log_action_recommended(
        scenario.case_id,
        action_type=str(action),
        diagnosis=diagnosis,
        approval_required=final_approval,
    )

    # ── 8. Approval gate (simulated) ─────────────────────────────
    if final_approval:
        audit.log_event(
            scenario.case_id,
            AuditEventType.APPROVAL_REQUESTED,
            details={"action": str(action), "amount": amount, "risk": str(risk_level)},
            correlation_id=correlation_id,
        )
        # Simulate auto-approval for demo
        audit.log_event(
            scenario.case_id,
            AuditEventType.HUMAN_APPROVED,
            actor="human:ops_senior_demo",
            details={"comment": "[DEMO] Auto-approved for simulation"},
            correlation_id=correlation_id,
        )

    # ── 9. Draft creation (simulated) ────────────────────────────
    if action in {ActionType.CREATE_REFUND_REQUEST_DRAFT, ActionType.CREATE_RECONCILIATION_TICKET_DRAFT}:
        audit.log_event(
            scenario.case_id,
            AuditEventType.DRAFT_CREATED,
            details={"draft_type": str(action), "amount": amount},
            correlation_id=correlation_id,
        )

    # ── 10. Case close ───────────────────────────────────────────
    final_status = "closed" if action != ActionType.MANUAL_REVIEW else "manual_review"
    audit.log_state_transition(
        scenario.case_id,
        previous_status="recommending",
        new_status=final_status,
        reason=diagnosis,
    )
    result["final_status"] = final_status

    # ── Audit summary ────────────────────────────────────────────
    case_events = audit.get_events_by_case(scenario.case_id)
    result["audit_event_count"] = len(case_events)

    # ── Evidence summary ─────────────────────────────────────────
    ev_summary: list[str] = []
    if evidence.wallet_ledger:
        wl = evidence.wallet_ledger
        ev_summary.append(f"wallet: {wl.status}, debit={wl.debit_amount:,}₫, refunded={wl.has_credit_refund}")
    if evidence.transaction:
        ev_summary.append(f"txn: status={evidence.transaction.status}, amount={evidence.transaction.amount:,}₫")
    if evidence.train_provider:
        tp = evidence.train_provider
        ev_summary.append(f"train_provider: {tp.booking_status}, ticket_code={tp.ticket_code or 'None'}")
    if evidence.utility_provider:
        up = evidence.utility_provider
        ev_summary.append(f"utility_provider: {up.provider_status}, bill={up.bill_code or 'N/A'}")
    if evidence.refund_status:
        ev_summary.append(f"refund: {evidence.refund_status.refund_status}")
    result["evidence_summary"] = ev_summary

    return result


# ═══════════════════════════════════════════════════════════════════════
# Pretty printer
# ═══════════════════════════════════════════════════════════════════════

def print_scenario_result(scenario: DemoScenario, result: dict[str, Any]) -> None:
    """Print a beautiful, structured output for one scenario."""

    info = scenario.extracted_info
    print(C.header(f"{C.tag(scenario.case_id)}  {scenario.title}"))
    print(f"  {C.DIM}{scenario.description}{C.RESET}")

    # ── Case Input ───────────────────────────────────────────────
    print(C.section("CASE INPUT"))
    print(C.kv("Case ID", scenario.case_id))
    print(C.kv("User ID", info.user_id or "—"))
    print(C.kv("Transaction ID", info.transaction_id or "—"))
    print(C.kv("Service Type", str(info.service_type) if info.service_type else "—"))
    print(C.kv("Issue Type", str(info.issue_type) if info.issue_type else "—"))
    complaint_short = scenario.raw_complaint[:80] + "…" if len(scenario.raw_complaint) > 80 else scenario.raw_complaint
    print(C.kv("Complaint", complaint_short, C.DIM))

    # ── Extracted Info ───────────────────────────────────────────
    print(C.section("EXTRACTED INFO"))
    print(C.kv("Order ID", info.order_id or "—"))
    print(C.kv("Bill Code", info.bill_code or "—"))
    print(C.kv("Customer Code", info.customer_code or "—"))
    print(C.kv("Amount (wallet)", f"{scenario.wallet_ledger.debit_amount:,}₫" if scenario.wallet_ledger else "—"))

    # ── Evidence Summary ─────────────────────────────────────────
    print(C.section("EVIDENCE SUMMARY"))
    for line in result.get("evidence_summary", []):
        print(f"    {C.DIM}│{C.RESET} {C.CYAN}•{C.RESET} {line}")

    # ── Selected Workflow ────────────────────────────────────────
    print(C.section("WORKFLOW & DECISION"))
    print(C.kv("Selected Workflow", result["selected_workflow"]))

    # ── Conflict Status ──────────────────────────────────────────
    if result["conflict_detected"]:
        print(C.kv("Conflict Status", C.err("CONFLICT DETECTED")))
        for conflict_desc in result.get("conflicts", []):
            wrapped = textwrap.fill(conflict_desc, width=60)
            for i, line in enumerate(wrapped.split("\n")):
                prefix = f"    {C.DIM}│{C.RESET}   {C.RED}⚡{C.RESET} " if i == 0 else f"    {C.DIM}│{C.RESET}     "
                print(f"{prefix}{C.RED}{line}{C.RESET}")
    else:
        print(C.kv("Conflict Status", C.ok("No conflicts")))

    # ── Rule Decision ────────────────────────────────────────────
    print(C.kv("Rule Decision", result["rule_decision"]))
    print(C.kv("Diagnosis", result["diagnosis"]))

    # ── Recommended Action (colored) ─────────────────────────────
    action_str = result["recommended_action"]
    if "refund" in action_str:
        action_display = f"{C.YELLOW}⚡ {action_str}{C.RESET}"
    elif "reconciliation" in action_str:
        action_display = f"{C.BLUE}🔄 {action_str}{C.RESET}"
    elif "manual_review" in action_str:
        action_display = f"{C.RED}🛑 {action_str}{C.RESET}"
    elif "customer_response" in action_str:
        action_display = f"{C.GREEN}💬 {action_str}{C.RESET}"
    else:
        action_display = action_str
    print(C.kv("Recommended Action", action_display))

    # ── Risk & Approval ──────────────────────────────────────────
    risk = result["risk_level"]
    if risk in ("high", "critical"):
        risk_display = f"{C.RED}{risk.upper()}{C.RESET}"
    elif risk == "medium":
        risk_display = f"{C.YELLOW}{risk.upper()}{C.RESET}"
    else:
        risk_display = f"{C.GREEN}{risk.upper()}{C.RESET}"
    print(C.kv("Risk Level", risk_display))

    approval = result["approval_required"]
    approval_display = C.warn("YES — human approval required") if approval else C.ok("NO — auto-proceed")
    print(C.kv("Approval Required", approval_display))

    # ── Audit & Final ────────────────────────────────────────────
    print(C.section("AUDIT & STATUS"))
    print(C.kv("Audit Event Count", result["audit_event_count"]))
    final = result["final_status"]
    if final == "closed":
        final_display = f"{C.GREEN}✔ CLOSED{C.RESET}"
    elif final == "manual_review":
        final_display = f"{C.RED}🛑 MANUAL REVIEW{C.RESET}"
    else:
        final_display = final
    print(C.kv("Final Status", final_display))

    # ── Expectation check ────────────────────────────────────────
    print(C.section("EXPECTATION CHECK"))
    got_action = result["recommended_action"]
    got_approval = result["approval_required"]
    action_match = got_action == scenario.expected_action
    approval_match = got_approval == scenario.expected_approval
    if action_match:
        print(f"    {C.DIM}│{C.RESET} {C.ok(f'Action matches expected: {scenario.expected_action}')}")
    else:
        msg = f"Action MISMATCH: got {got_action}, expected {scenario.expected_action}"
        print(f"    {C.DIM}│{C.RESET} {C.err(msg)}")
    if approval_match:
        print(f"    {C.DIM}│{C.RESET} {C.ok(f'Approval matches expected: {scenario.expected_approval}')}")
    else:
        msg = f"Approval MISMATCH: got {got_approval}, expected {scenario.expected_approval}"
        print(f"    {C.DIM}│{C.RESET} {C.err(msg)}")

    print()


# ═══════════════════════════════════════════════════════════════════════
# Summary table
# ═══════════════════════════════════════════════════════════════════════

def print_summary(all_results: list[tuple[DemoScenario, dict[str, Any]]]) -> None:
    """Print a compact summary table."""
    print(C.header("SUMMARY — ALL DEMO CASES"))

    # Table header
    hdr = (
        f"  {C.BOLD}{C.WHITE}"
        f"{'Case':<18} {'Action':<36} {'Approval':<10} {'Risk':<8} {'Status':<15} {'✓/✗':<5}"
        f"{C.RESET}"
    )
    print(hdr)
    print(f"  {C.DIM}{'─' * 92}{C.RESET}")

    all_pass = True
    for scenario, result in all_results:
        action_match = result["recommended_action"] == scenario.expected_action
        approval_match = result["approval_required"] == scenario.expected_approval
        passed = action_match and approval_match
        if not passed:
            all_pass = False

        check = f"{C.GREEN}PASS{C.RESET}" if passed else f"{C.RED}FAIL{C.RESET}"
        action = result["recommended_action"]
        approval = "YES" if result["approval_required"] else "NO"
        risk = result["risk_level"]
        status = result["final_status"]

        print(
            f"  {scenario.case_id:<18} {action:<36} {approval:<10} {risk:<8} {status:<15} {check}"
        )

    print(f"  {C.DIM}{'─' * 92}{C.RESET}")
    if all_pass:
        print(f"\n  {C.BOLD}{C.GREEN}🎉 All {len(all_results)} scenarios PASSED!{C.RESET}\n")
    else:
        print(f"\n  {C.BOLD}{C.RED}⚠ Some scenarios FAILED — review above.{C.RESET}\n")


# ═══════════════════════════════════════════════════════════════════════
# CLI entrypoint
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run MVP demo scenarios through the fintech agent rule engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Available scenarios:
              TRAIN_001     Debited + ticket_not_issued → refund draft + approval
              TRAIN_002     Ticket issued → customer response, no refund
              BILL_002      Provider not_confirmed → reconciliation ticket, no refund
              BILL_003      Provider failed → refund draft + approval
              CONFLICT_001  Ledger↔txn conflict → manual review
        """),
    )
    parser.add_argument(
        "cases",
        nargs="*",
        metavar="CASE_ID",
        help="Scenario ID(s) to run (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available scenarios and exit",
    )
    args = parser.parse_args()

    scenarios = build_scenarios()

    if args.list:
        print(f"\n{C.BOLD}Available demo scenarios:{C.RESET}\n")
        for sid, s in scenarios.items():
            print(f"  {C.CYAN}{sid:<16}{C.RESET} {s.title}")
        print()
        return

    # Select which scenarios to run
    if args.cases:
        selected_ids = []
        for cid in args.cases:
            cid_upper = cid.upper()
            if cid_upper not in scenarios:
                print(f"{C.err(f'Unknown scenario: {cid}')}")
                print(f"  Available: {', '.join(scenarios.keys())}")
                sys.exit(1)
            selected_ids.append(cid_upper)
    else:
        selected_ids = list(scenarios.keys())

    # ── Run ───────────────────────────────────────────────────────
    print(f"\n{C.BOLD}{C.MAGENTA}╔══════════════════════════════════════════════════════════════════════╗{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}║  FINTECH BACK-OFFICE AGENT — MVP DEMO RUNNER                       ║{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}║  {C.DIM}No API calls • No refund execution • Pure rule engine demo{C.RESET}        {C.BOLD}{C.MAGENTA}║{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}║  {C.DIM}{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}{C.RESET}                                          {C.BOLD}{C.MAGENTA}║{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}╚══════════════════════════════════════════════════════════════════════╝{C.RESET}")

    audit = AuditLogger(mask_pii_in_details=False)  # Demo mode: don't mask for readability
    all_results: list[tuple[DemoScenario, dict[str, Any]]] = []

    for sid in selected_ids:
        scenario = scenarios[sid]
        result = run_scenario(scenario, audit)
        all_results.append((scenario, result))
        print_scenario_result(scenario, result)

    # ── Summary ──────────────────────────────────────────────────
    if len(all_results) > 1:
        print_summary(all_results)

    # ── Total audit events ───────────────────────────────────────
    print(f"  {C.DIM}Total audit events logged: {audit.event_count}{C.RESET}\n")


if __name__ == "__main__":
    main()

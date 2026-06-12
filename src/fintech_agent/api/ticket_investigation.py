"""Back-office investigation for a customer-chat handoff ticket.

When staff opens a Customer Chat Ticket, this service runs (or reloads) a
real, data-driven investigation so staff see actual evidence, a specific
diagnosis, and a clear next action — never a vague "đang được xác định".

Pipeline (all data-driven, nothing hard-coded per case/customer):

    ticket
      → complainant identity (trusted, from the ticket)
      → extracted_info (real values, placeholder labels filtered)
      → resolver by workflow (transaction_id / user_id / merchant_id)
      → evidence lookup via MCP (fetch_evidence node, workflow-aware)
      → rule engine (apply_rules node) → diagnosis code + action
      → staff_diagnosis (what checked / confirmed / likely issue / why)
      → staff_action_contract (from rule action, never fixed text)
      → missing_evidence (exactly which sources were not found)

SAFETY:
  - Read-only. Evidence lookup uses read-only MCP tools only.
  - No refund/force-success/unlock/payout/ledger edit is executed here.
  - No transaction_id / amount / bank / user_id is hard-coded; every value is
    derived from ticket data, extracted_info, resolver, evidence and rules.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from fintech_agent.graph.state import AgentState
from fintech_agent.nodes.fetch_evidence import fetch_evidence as _default_fetch_evidence
from fintech_agent.nodes.rule_decision import apply_rules as _default_apply_rules
from fintech_agent.schemas.case_state import ExtractedInfo
from fintech_agent.schemas.enums import ServiceType
from fintech_agent.schemas.evidence import EvidenceBundle

logger = logging.getLogger(__name__)


def _default_search_transactions(user_id: str, fields: Any, workflow: str) -> list[Any]:
    """Search a user's transactions by extracted criteria (read-only).

    Reuses the customer-resolver search so the investigation can pin down a
    transaction from amount/bank/time when no transaction_id was provided.
    """
    from fintech_agent.api.generic_resolver import _search_user_transactions

    return _search_user_transactions(user_id, fields, workflow)


# ─── Workflow → service_type routing for fetch_evidence ──────────
#
# fetch_evidence routes on ExtractedInfo.service_type:
#   account_security      → fraud/account evidence
#   merchant_settlement   → merchant settlement evidence
#   (anything else, with transaction_id) → transaction evidence
_WORKFLOW_SERVICE_TYPE: dict[str, ServiceType | None] = {
    "wallet_topup": None,            # transaction branch (needs transaction_id)
    "train_ticket": None,
    "utility_bill": None,
    "fraud_account_lock": ServiceType.ACCOUNT_SECURITY,
    "merchant_settlement_delay": ServiceType.MERCHANT_SETTLEMENT,
}

_TRANSACTION_WORKFLOWS = frozenset({"wallet_topup", "train_ticket", "utility_bill"})


# ─── Readable text for deterministic rule diagnosis codes ────────
#
# Keyed by the stable rule code (the part before any " (" detail). This maps
# rule-engine OUTPUT to staff-readable Vietnamese — it does NOT branch on the
# customer's message text. `issue` = likely problem location, `why` = why staff
# action is needed.
_DIAGNOSIS_TEXT: dict[str, dict[str, str]] = {
    # wallet_topup
    "bank_success_money_received_wallet_pending": {
        "issue": "Ngân hàng đã trừ tiền và tiền đã vào ví tổng, nhưng ví của khách chưa được cộng (treo ở bước cập nhật số dư ví).",
        "why": "Đủ điều kiện cộng tiền cho khách; cần nhân viên phê duyệt tạo draft cập nhật giao dịch.",
    },
    "bank_success_but_money_not_in_master_wallet": {
        "issue": "Ngân hàng báo thành công nhưng tiền chưa về ví tổng.",
        "why": "Có sai lệch giữa ngân hàng và ví tổng; cần đối soát thủ công trước khi xử lý.",
    },
    "bank_transfer_failed_wait_reversal": {
        "issue": "Giao dịch phía ngân hàng thất bại hoặc bị từ chối.",
        "why": "Ngân hàng sẽ hoàn tiền theo quy trình; cần soạn phản hồi cho khách, không cộng ví.",
    },
    "money_not_received_in_master_wallet": {
        "issue": "Tiền chưa về ví tổng.",
        "why": "Chưa đủ điều kiện cộng ví; cần phản hồi khách và tiếp tục theo dõi.",
    },
    "transaction_not_pending": {
        "issue": "Giao dịch không còn ở trạng thái chờ (đã được xử lý hoặc đã hoàn tất).",
        "why": "Giao dịch đã được xử lý; cần xác nhận lại tình trạng với khách.",
    },
    "transaction_unavailable": {
        "issue": "Không tìm thấy dữ liệu giao dịch để đối chiếu.",
        "why": "Thiếu giao dịch nguồn; cần xác minh lại mã giao dịch của khách.",
    },
    "reconciliation_unavailable": {
        "issue": "Chưa có dữ liệu đối soát phía ngân hàng.",
        "why": "Không thể xác minh phía ngân hàng tự động; cần đối soát thủ công.",
    },
    "conflict_detected": {
        "issue": "Dữ liệu giữa các nguồn đang mâu thuẫn.",
        "why": "Cần nhân viên kiểm tra thủ công do xung đột bằng chứng.",
    },
    "unknown_bank_status": {
        "issue": "Trạng thái phía ngân hàng không xác định.",
        "why": "Cần nhân viên kiểm tra thủ công.",
    },
    # train_ticket / utility_bill (provider workflows)
    "ticket_issued_with_code": {
        "issue": "Vé đã được phát hành thành công.",
        "why": "Vé đã phát hành; cần thông báo mã vé cho khách, không cần hoàn tiền.",
    },
    "bill_confirmed_and_paid": {
        "issue": "Hóa đơn đã được nhà cung cấp xác nhận và thanh toán thành công.",
        "why": "Đã hoàn tất; cần thông báo xác nhận cho khách.",
    },
    "booking_pending_within_sla": {
        "issue": "Nhà cung cấp đang xử lý đặt chỗ, vẫn trong thời hạn SLA.",
        "why": "Còn trong SLA; theo dõi thêm, chưa cần hoàn tiền.",
    },
    "provider_pending_within_sla": {
        "issue": "Nhà cung cấp đang xử lý, vẫn trong thời hạn SLA.",
        "why": "Còn trong SLA; theo dõi thêm, chưa cần hoàn tiền.",
    },
    "provider_no_record_wallet_debited": {
        "issue": "Ví đã bị trừ tiền nhưng nhà cung cấp không có bản ghi tương ứng.",
        "why": "Khách đã mất tiền nhưng không nhận dịch vụ; cần tạo draft hoàn tiền.",
    },
    "wallet_debited_ticket_not_issued": {
        "issue": "Ví đã bị trừ tiền nhưng vé chưa được phát hành.",
        "why": "Đủ điều kiện hoàn tiền; cần tạo draft hoàn tiền cho khách.",
    },
    "provider_failed_wallet_debited": {
        "issue": "Nhà cung cấp xử lý thất bại trong khi ví đã bị trừ tiền.",
        "why": "Cần tạo draft hoàn tiền cho khách.",
    },
    "wallet_not_debited": {
        "issue": "Ví của khách chưa bị trừ tiền.",
        "why": "Không có thiệt hại tài chính; cần phản hồi giải thích cho khách.",
    },
    "provider_status_unavailable": {
        "issue": "Không lấy được trạng thái từ nhà cung cấp.",
        "why": "Cần nhân viên kiểm tra thủ công với nhà cung cấp.",
    },
    "provider_not_confirmed_needs_reconciliation": {
        "issue": "Nhà cung cấp chưa xác nhận hóa đơn.",
        "why": "Cần đối soát với nhà cung cấp trước khi phản hồi khách.",
    },
    "amount_mismatch_between_wallet_and_provider": {
        "issue": "Số tiền giữa ví và nhà cung cấp lệch nhau.",
        "why": "Cần đối soát số tiền thủ công trước khi xử lý.",
    },
    "unknown_provider_status": {
        "issue": "Trạng thái nhà cung cấp không xác định.",
        "why": "Cần nhân viên kiểm tra thủ công.",
    },
    "refund_not_eligible": {
        "issue": "Chưa đủ điều kiện hoàn tiền theo bằng chứng hiện có.",
        "why": "Cần kiểm tra thêm trước khi quyết định hoàn tiền.",
    },
    "wallet_ledger_unavailable": {
        "issue": "Không có dữ liệu sổ ví để đối chiếu.",
        "why": "Cần nhân viên kiểm tra thủ công.",
    },
    # fraud_account_lock
    "likely_false_positive_unlock_candidate": {
        "issue": "Khả năng cao là cảnh báo nhầm; tài khoản có thể đủ điều kiện mở khóa.",
        "why": "Cần Risk/Ops phê duyệt mở khóa sau khi xác minh.",
    },
    "suspicious_activity_keep_locked_request_documents": {
        "issue": "Có dấu hiệu hoạt động đáng ngờ; nên tiếp tục giữ khóa.",
        "why": "Cần yêu cầu khách bổ sung giấy tờ để xác minh trước khi mở khóa.",
    },
    "fraud_review_inconclusive": {
        "issue": "Kết quả review rủi ro chưa đủ rõ ràng để kết luận.",
        "why": "Cần Risk/Ops kiểm tra thủ công.",
    },
    "account_not_locked": {
        "issue": "Tài khoản hiện không ở trạng thái bị khóa.",
        "why": "Không cần mở khóa; cần phản hồi giải thích cho khách.",
    },
    "missing_account_or_fraud_evidence": {
        "issue": "Thiếu dữ liệu trạng thái tài khoản hoặc hồ sơ gian lận.",
        "why": "Không thể kết luận; cần xác minh danh tính và kiểm tra thủ công.",
    },
    # merchant_settlement_delay
    "settlement_not_due_yet": {
        "issue": "Khoản giải ngân chưa tới hạn theo chu kỳ settlement.",
        "why": "Chưa tới hạn; cần thông báo cho merchant thời điểm dự kiến.",
    },
    "payout_success_unc_not_sent": {
        "issue": "Đã giải ngân thành công nhưng chưa gửi UNC/chứng từ cho merchant.",
        "why": "Cần gửi UNC/mã tham chiếu cho merchant.",
    },
    "payout_success_unc_already_sent": {
        "issue": "Đã giải ngân thành công và đã gửi UNC.",
        "why": "Đã hoàn tất; cần phản hồi xác nhận cho merchant.",
    },
    "payout_in_progress_monitor": {
        "issue": "Khoản giải ngân đang được xử lý.",
        "why": "Đang xử lý; theo dõi tiến độ, chưa cần thao tác thêm.",
    },
    "payout_failed_retriable_retry_payout": {
        "issue": "Giải ngân thất bại nhưng có thể thử lại.",
        "why": "Cần tạo draft giải ngân lại cho team Settlement/Ops.",
    },
    "payout_failed_non_retriable": {
        "issue": "Giải ngân thất bại và không thể tự động thử lại.",
        "why": "Cần Settlement/Ops xử lý thủ công.",
    },
    "batch_failed_create_manual_payout": {
        "issue": "Lô settlement xử lý thất bại.",
        "why": "Cần tạo draft giải ngân thủ công cho merchant.",
    },
    "bank_account_missing": {
        "issue": "Tài khoản ngân hàng của merchant bị thiếu.",
        "why": "Cần yêu cầu merchant cập nhật tài khoản ngân hàng.",
    },
    "bank_account_invalid": {
        "issue": "Tài khoản ngân hàng của merchant không hợp lệ.",
        "why": "Cần yêu cầu merchant cập nhật tài khoản ngân hàng.",
    },
    "merchant_not_found": {
        "issue": "Không tìm thấy hồ sơ merchant tương ứng.",
        "why": "Cần xác minh lại thông tin merchant.",
    },
    "settlement_ledger_not_found": {
        "issue": "Không tìm thấy sổ settlement của merchant.",
        "why": "Cần nhân viên kiểm tra thủ công.",
    },
    "net_settlement_zero_or_negative": {
        "issue": "Số dư settlement bằng 0 hoặc âm.",
        "why": "Không có khoản phải trả; cần giải thích cho merchant.",
    },
    "merchant_on_hold_escalate_ops": {
        "issue": "Merchant đang bị giữ (hold).",
        "why": "Cần Ops xem xét và giải tỏa hold.",
    },
    "payout_amount_less_than_ledger_difference_payout": {
        "issue": "Số tiền payout nhỏ hơn chênh lệch trên sổ settlement.",
        "why": "Cần đối soát số tiền và tạo draft bù.",
    },
    # generic
    "unknown_or_conflicting_evidence": {
        "issue": "Bằng chứng chưa rõ ràng hoặc đang mâu thuẫn.",
        "why": "Cần nhân viên kiểm tra thủ công.",
    },
}


# ─── Evidence source maps (per workflow) ─────────────────────────
#
# (EvidenceBundle attribute, staff label). Drives the evidence checklist and
# the missing_evidence list. KEY sources gate confidence.
_EVIDENCE_SOURCES: dict[str, list[tuple[str, str]]] = {
    "wallet_topup": [
        ("transaction", "Giao dịch nạp tiền"),
        ("reconciliation_status", "Đối soát ngân hàng"),
    ],
    "train_ticket": [
        ("transaction", "Giao dịch"),
        ("wallet_ledger", "Sổ ví (trừ tiền)"),
        ("train_provider", "Trạng thái nhà cung cấp vé"),
        ("refund_status", "Trạng thái hoàn tiền"),
    ],
    "utility_bill": [
        ("transaction", "Giao dịch"),
        ("wallet_ledger", "Sổ ví (trừ tiền)"),
        ("utility_provider", "Trạng thái nhà cung cấp"),
        ("refund_status", "Trạng thái hoàn tiền"),
    ],
    "fraud_account_lock": [
        ("account_status", "Trạng thái tài khoản"),
        ("fraud_case", "Hồ sơ rủi ro/gian lận"),
    ],
    "merchant_settlement_delay": [
        ("merchant_profile", "Hồ sơ merchant"),
        ("merchant_bank_account", "Tài khoản ngân hàng merchant"),
        ("merchant_settlement_ledger", "Sổ settlement"),
        ("merchant_payout", "Khoản giải ngân (payout)"),
        ("settlement_batch", "Lô settlement"),
        ("bank_transfer_receipt", "Chứng từ chuyển khoản"),
    ],
}

# Which processing step the recommended action targets → the "likely bottleneck".
# Keyed on the deterministic rule action (not on customer text).
_ACTION_BOTTLENECK: dict[str, str] = {
    "create_force_success_draft": "Bước cộng tiền vào ví của khách",
    "create_reconciliation_ticket_draft": "Bước đối soát giao dịch nạp tiền",
    "create_refund_request_draft": "Bước hoàn tiền cho khách",
    "create_manual_payout_draft": "Bước giải ngân cho merchant",
    "create_unlock_account_draft": "Bước xét duyệt mở khóa tài khoản",
    "create_request_documents_response_draft": "Bước xác minh giấy tờ của khách",
    "request_bank_account_correction": "Bước cập nhật tài khoản ngân hàng merchant",
    "request_identity_correction": "Bước cập nhật thông tin định danh",
    "send_unc_email_draft": "Bước gửi chứng từ UNC cho merchant",
    "draft_customer_response": "Bước phản hồi khách hàng",
    "manual_review": "Cần nhân viên kiểm tra thủ công",
    "manual_settlement_review": "Cần Settlement/Ops kiểm tra thủ công",
    "wait_sla": "Đang chờ trong thời hạn SLA",
    "no_action": "Không có bước xử lý nào bị tắc",
}


# Sources that must be present to call a diagnosis "high confidence".
_KEY_SOURCES: dict[str, list[str]] = {
    "wallet_topup": ["transaction", "reconciliation_status"],
    "train_ticket": ["transaction", "train_provider"],
    "utility_bill": ["transaction", "utility_provider"],
    "fraud_account_lock": ["account_status", "fraud_case"],
    "merchant_settlement_delay": ["merchant_profile"],
}


@dataclass
class TicketInvestigation:
    """Result of investigating one chat handoff ticket."""

    ticket_id: str
    selected_workflow: str
    resolved: bool = False
    resolved_entity_type: str = "none"   # transaction | account | merchant | none
    resolved_entity_id: str = ""

    # pipeline telemetry (for the dev log / debugging)
    resolver_called: bool = False
    resolver_status: str = ""            # direct_id | resolved_by_search | no_match | ...
    evidence_lookup_called: bool = False
    evidence_found: bool = False
    rule_engine_called: bool = False

    # rule engine output (data-driven)
    rule_action: str = ""
    rule_diagnosis_code: str = ""
    approval_required: bool = False
    risk_level: str = "unknown"

    # staff-facing diagnosis
    what_was_checked: list[str] = field(default_factory=list)
    confirmed_facts: list[str] = field(default_factory=list)
    likely_issue: str = ""             # where the money/problem currently sits
    money_or_issue_location: str = ""  # same finding, surfaced as its own section
    likely_bottleneck: str = ""        # which processing step is stuck (from action)
    why_staff_action_needed: str = ""
    confidence: str = "low"

    # evidence + gaps
    evidence_summary: list[dict[str, str]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)

    # diagnostics
    notes: list[str] = field(default_factory=list)


# ─── Entity resolution from ticket (direct ids only — no fuzzy search) ──

def _clean(value: Any) -> str:
    """Return a trimmed string, or '' for None/placeholder-ish values."""
    from fintech_agent.api.chat_handoff import _is_placeholder_label

    if value is None or _is_placeholder_label(value):
        return ""
    return str(value).strip()


def build_extracted_info(ticket: Any) -> tuple[ExtractedInfo, str, str]:
    """Build graph ExtractedInfo from ticket data.

    Returns (extracted_info, resolved_entity_id, resolved_entity_type).
    Identity comes from the trusted complainant; case fields come from
    the (placeholder-filtered) extracted_info. Nothing is invented.
    """
    wf = ticket.selected_workflow or ""
    ei = ticket.extracted_info or {}
    comp = ticket.complainant

    info = ExtractedInfo(service_type=_WORKFLOW_SERVICE_TYPE.get(wf))

    if wf in _TRANSACTION_WORKFLOWS:
        txn_id = _clean(ei.get("transaction_id")) or _clean(ei.get("order_id"))
        info.transaction_id = txn_id or None
        info.order_id = _clean(ei.get("order_id")) or None
        info.bill_code = _clean(ei.get("bill_code")) or None
        info.customer_code = _clean(ei.get("customer_code")) or None
        info.user_id = _clean(getattr(comp, "user_id", "")) or None
        return info, txn_id, "transaction"

    if wf == "fraud_account_lock":
        user_id = _clean(getattr(comp, "user_id", "")) or _clean(ei.get("user_id"))
        info.user_id = user_id or None
        info.phone = _clean(getattr(comp, "phone", "")) or None
        info.email = _clean(getattr(comp, "email", "")) or None
        info.wallet_id = _clean(getattr(comp, "wallet_id", "")) or None
        return info, user_id, "account"

    if wf == "merchant_settlement_delay":
        merchant_id = _clean(getattr(comp, "merchant_id", "")) or _clean(ei.get("merchant_id"))
        info.merchant_id = merchant_id or None
        info.tax_code = _clean(getattr(comp, "tax_code", "")) or None
        info.phone = _clean(getattr(comp, "phone", "")) or None
        info.email = _clean(getattr(comp, "email", "")) or None
        info.payout_id = _clean(ei.get("payout_id")) or None
        info.batch_id = _clean(ei.get("batch_id")) or None
        info.settlement_date = _clean(ei.get("settlement_date")) or None
        info.settlement_cycle = _clean(ei.get("settlement_cycle")) or None
        return info, merchant_id, "merchant"

    return info, "", "none"


# ─── Transaction resolver (search by amount/bank/time when no id) ──

def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _search_fields_from_ticket(ei: dict) -> SimpleNamespace:
    """Build the field shim the transaction search expects, from the ticket."""
    return SimpleNamespace(
        transaction_id=_clean(ei.get("transaction_id")) or None,
        order_id=_clean(ei.get("order_id")) or None,
        amount=_to_int(ei.get("amount")),
        bank_name=_clean(ei.get("bank_name")) or None,
        bank_reference=_clean(ei.get("bank_reference")) or None,
        approximate_time_text=_clean(ei.get("approximate_time_text")) or None,
        approximate_date_text=_clean(ei.get("approximate_date_text")) or None,
    )


def _resolve_transaction_id(
    ticket: Any,
    workflow: str,
    search_fn: Callable[[str, Any, str], list[Any]],
) -> tuple[str, str]:
    """Resolve a transaction id for a transaction workflow.

    Order: direct id → search by user identity + extracted criteria.
    Returns (transaction_id, resolver_status). resolver_status is one of:
      direct_id | resolved_by_search | no_identity | no_search_criteria |
      multiple_candidates | no_match | search_error
    """
    ei = ticket.extracted_info or {}
    direct = _clean(ei.get("transaction_id")) or _clean(ei.get("order_id"))
    if direct:
        return direct, "direct_id"

    user_id = _clean(getattr(ticket.complainant, "user_id", ""))
    if not user_id:
        return "", "no_identity"

    fields = _search_fields_from_ticket(ei)
    has_criteria = any([
        fields.amount, fields.bank_name, fields.bank_reference,
        fields.approximate_time_text, fields.approximate_date_text,
    ])
    if not has_criteria:
        return "", "no_search_criteria"

    try:
        matches = search_fn(user_id, fields, workflow)
    except Exception as exc:  # noqa: BLE001 — resolver must never raise to caller
        logger.warning("[Investigation] transaction search failed: %s", exc)
        return "", "search_error"

    if len(matches) == 1:
        txn = matches[0]
        tid = getattr(txn, "transaction_id", "") or getattr(txn, "id", "")
        return str(tid), "resolved_by_search"
    if len(matches) > 1:
        return "", "multiple_candidates"
    return "", "no_match"


# What is missing, stated exactly, per resolver outcome (data-driven).
_MISSING_BY_RESOLVER_STATUS: dict[str, tuple[str, str]] = {
    "no_identity": (
        "Định danh khách hàng (user_id/SĐT) để truy vấn giao dịch",
        "Chưa xác định được tài khoản khách để tra cứu giao dịch.",
    ),
    "no_search_criteria": (
        "Mã giao dịch, hoặc số tiền + ngân hàng + thời gian giao dịch",
        "Chưa đủ thông tin để xác định giao dịch của khách.",
    ),
    "multiple_candidates": (
        "Mã tham chiếu ngân hàng hoặc thời gian chính xác để chọn đúng giao dịch",
        "Có nhiều giao dịch khớp; cần thêm chi tiết để xác định đúng giao dịch.",
    ),
    "no_match": (
        "Mã giao dịch hoặc mã tham chiếu ngân hàng (không tìm thấy giao dịch khớp)",
        "Chưa tìm thấy giao dịch khớp với thông tin khách cung cấp.",
    ),
    "search_error": (
        "Truy vấn giao dịch tạm thời lỗi — cần thử lại",
        "Tạm thời chưa tra cứu được giao dịch.",
    ),
}


# ─── Confirmed facts from real evidence values (data-driven) ─────

_TXN_STATUS_VN = {
    "pending": "đang chờ xử lý",
    "processing": "đang xử lý",
    "success": "đã thành công",
    "completed": "đã hoàn tất",
    "failed": "thất bại",
    "error": "lỗi",
}


def _fmt_vnd(amount: Any) -> str:
    try:
        return f"{int(amount):,}đ".replace(",", ".")
    except (ValueError, TypeError):
        return str(amount)


def _collect_confirmed_facts(workflow: str, ev: EvidenceBundle) -> list[str]:
    """Build short, staff-readable facts from ACTUAL evidence values."""
    facts: list[str] = []

    txn = ev.transaction
    if txn is not None:
        status_vn = _TXN_STATUS_VN.get(str(txn.status).lower(), str(txn.status))
        facts.append(f"Giao dịch {txn.transaction_id}: {status_vn}")
        if txn.amount:
            facts.append(f"Số tiền giao dịch: {_fmt_vnd(txn.amount)}")

    recon = ev.reconciliation_status
    if recon is not None:
        if recon.bank_status:
            facts.append(f"Ngân hàng: {recon.bank_status}")
        if recon.money_received_in_master_wallet is not None:
            facts.append(
                "Tiền đã vào ví tổng"
                if recon.money_received_in_master_wallet
                else "Tiền chưa vào ví tổng"
            )

    ledger = ev.wallet_ledger
    if ledger is not None and ledger.has_user_debit:
        facts.append(f"Ví đã trừ {_fmt_vnd(ledger.debit_amount)}")

    tp = ev.train_provider
    if tp is not None and getattr(tp, "booking_status", None):
        facts.append(f"Nhà cung cấp vé: {tp.booking_status}")

    up = ev.utility_provider
    if up is not None and getattr(up, "provider_status", None):
        facts.append(f"Nhà cung cấp: {up.provider_status}")

    acc = ev.account_status
    if acc is not None and acc.account_status:
        facts.append(f"Tài khoản: {acc.account_status}")

    fraud = ev.fraud_case
    if fraud is not None:
        if fraud.fraud_status:
            facts.append(f"Hồ sơ gian lận: {fraud.fraud_status}")
        if fraud.recommended_decision:
            facts.append(f"Đề xuất hệ thống rủi ro: {fraud.recommended_decision}")

    mp = ev.merchant_payout
    if mp is not None and mp.status:
        facts.append(f"Payout: {mp.status}")

    sl = ev.merchant_settlement_ledger
    if sl is not None and sl.status:
        facts.append(f"Sổ settlement: {sl.status}")

    mba = ev.merchant_bank_account
    if mba is not None and mba.verification_status:
        facts.append(f"TK ngân hàng merchant: {mba.verification_status}")

    return facts


def _build_evidence_summary(
    workflow: str, ev: EvidenceBundle,
) -> tuple[list[dict[str, str]], list[str]]:
    """Return (evidence_summary checklist, missing_evidence labels)."""
    summary: list[dict[str, str]] = []
    missing: list[str] = []
    for attr, label in _EVIDENCE_SOURCES.get(workflow, []):
        present = getattr(ev, attr, None) is not None
        summary.append({
            "label": label,
            "status": "checked" if present else "missing",
            "detail": "Đã truy vấn được dữ liệu" if present else "Chưa truy vấn được",
        })
        if not present:
            missing.append(label)
    return summary, missing


def _stable_code(diagnosis: str) -> str:
    """Strip the f-string detail suffix, e.g. 'unknown_bank_status (x)' → code."""
    return (diagnosis or "").split(" (", 1)[0].strip()


def _derive_risk(workflow: str, action: str, approval_required: bool) -> str:
    """Derive a coarse risk level from workflow + action (data-driven)."""
    if workflow == "fraud_account_lock":
        return "high"
    money_actions = {
        "create_force_success_draft",
        "create_refund_request_draft",
        "create_manual_payout_draft",
    }
    if action in money_actions:
        return "medium"
    if approval_required:
        return "medium"
    return "low"


def _confidence(
    workflow: str, resolved: bool, ev: EvidenceBundle, action: str,
) -> str:
    """Confidence from entity resolution, key-evidence presence, and action."""
    if not resolved:
        return "low"
    key = _KEY_SOURCES.get(workflow, [])
    have_all_key = all(getattr(ev, attr, None) is not None for attr in key)
    if not have_all_key:
        return "low"
    if action in ("manual_review", "manual_settlement_review"):
        return "medium"
    return "high"


def _checked_labels(workflow: str, tool_results: dict, ev: EvidenceBundle) -> list[str]:
    """What the investigation actually checked (from evidence that loaded)."""
    labels = [
        label for attr, label in _EVIDENCE_SOURCES.get(workflow, [])
        if getattr(ev, attr, None) is not None
    ]
    if tool_results.get("identity_source") or tool_results.get("identity_lookup") == "missing":
        labels.insert(0, "Danh tính khách hàng")
    return labels


# ─── Dev log (no PIN/OTP/password) ───────────────────────────────

_SECRET_KEY_HINTS = ("pin", "otp", "password", "passwd", "cvv", "cvc", "secret", "token")


def _safe_extracted_for_log(ei: dict | None) -> dict:
    """Copy of extracted_info with any credential-ish keys redacted."""
    safe: dict = {}
    for k, v in (ei or {}).items():
        if any(h in str(k).lower() for h in _SECRET_KEY_HINTS):
            safe[k] = "***redacted***"
        else:
            safe[k] = v
    return safe


def _emit_investigation_log(ticket: Any, result: TicketInvestigation) -> None:
    """Structured dev log of the investigation pipeline (no sensitive values)."""
    payload = {
        "ticket_id": result.ticket_id,
        "workflow": result.selected_workflow,
        "extracted_info": _safe_extracted_for_log(getattr(ticket, "extracted_info", {})),
        "resolver_called": result.resolver_called,
        "resolver_status": result.resolver_status,
        "resolved_entity": f"{result.resolved_entity_type}:{result.resolved_entity_id}"
        if result.resolved_entity_id else result.resolved_entity_type,
        "evidence_lookup_called": result.evidence_lookup_called,
        "evidence_found": result.evidence_found,
        "rule_engine_called": result.rule_engine_called,
        "rule_action": result.rule_action,
        "staff_diagnosis_created": bool(result.likely_issue),
        "money_or_issue_location": result.money_or_issue_location,
        "confidence": result.confidence,
        "missing_evidence": result.missing_evidence,
    }
    try:
        logger.info("customer_ticket_investigation %s", json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — logging must never break the request
        logger.info("customer_ticket_investigation %s", payload)


# ─── Main entry point ────────────────────────────────────────────

def investigate_customer_chat_ticket(
    ticket: Any,
    *,
    fetch_evidence_fn: Callable[[AgentState], AgentState] | None = None,
    apply_rules_fn: Callable[[AgentState], AgentState] | None = None,
    search_transactions_fn: Callable[[str, Any, str], list[Any]] | None = None,
) -> TicketInvestigation:
    """Run a data-driven investigation for one chat handoff ticket.

    Args:
        ticket: a ChatHandoffTicket.
        fetch_evidence_fn / apply_rules_fn / search_transactions_fn: injectable
            for tests; default to the real graph nodes + transaction resolver.

    Returns:
        TicketInvestigation. When the entity cannot be resolved, `resolved` is
        False and `missing_evidence` states exactly what is needed.
    """
    fetch_fn = fetch_evidence_fn or _default_fetch_evidence
    rule_fn = apply_rules_fn or _default_apply_rules
    search_fn = search_transactions_fn or _default_search_transactions

    wf = ticket.selected_workflow or ""
    result = TicketInvestigation(ticket_id=ticket.ticket_id, selected_workflow=wf)

    if wf not in _EVIDENCE_SOURCES:
        result.notes.append(f"no_investigation_for_workflow:{wf or 'unknown'}")
        result.likely_issue = "Chưa xác định được quy trình xử lý cho ticket này."
        result.why_staff_action_needed = "Cần nhân viên phân loại và kiểm tra thủ công."
        _emit_investigation_log(ticket, result)
        return result

    extracted, entity_id, entity_type = build_extracted_info(ticket)
    result.resolver_called = True
    result.resolved_entity_type = entity_type

    # ── Resolver: pin down the entity (direct id → search) ───────
    if entity_type == "transaction":
        entity_id, resolver_status = _resolve_transaction_id(ticket, wf, search_fn)
        if entity_id:
            extracted.transaction_id = entity_id
    else:
        resolver_status = "direct_identity" if entity_id else "no_identity"
    result.resolver_status = resolver_status
    result.resolved_entity_id = entity_id

    # ── Cannot resolve an entity → state exactly what is missing ──
    if not entity_id:
        result.resolved = False
        result.confidence = "low"
        _ENTITY_BOTTLENECK = {
            "transaction": "Bước xác định giao dịch của khách",
            "account": "Bước xác định tài khoản của khách",
            "merchant": "Bước xác định merchant",
        }
        if entity_type == "transaction":
            miss, issue = _MISSING_BY_RESOLVER_STATUS.get(
                resolver_status,
                ("Mã giao dịch để truy vấn bằng chứng giao dịch",
                 "Chưa xác định được giao dịch của khách."),
            )
            result.missing_evidence = [miss]
            result.likely_issue = issue
        elif entity_type == "account":
            result.missing_evidence = ["Định danh tài khoản (user_id/SĐT/email)"]
            result.likely_issue = "Chưa xác định được tài khoản của khách."
        elif entity_type == "merchant":
            result.missing_evidence = ["Mã merchant để truy vấn dữ liệu settlement"]
            result.likely_issue = "Chưa xác định được merchant."
        else:
            result.missing_evidence = ["Thông tin định danh để bắt đầu điều tra"]
            result.likely_issue = "Chưa đủ thông tin để bắt đầu điều tra."
        result.money_or_issue_location = result.likely_issue
        result.likely_bottleneck = _ENTITY_BOTTLENECK.get(
            entity_type, "Bước thu thập thông tin ban đầu",
        )
        result.why_staff_action_needed = (
            "Cần bổ sung thông tin định danh/giao dịch trước khi truy vấn bằng chứng."
        )
        result.evidence_summary, _ = _build_evidence_summary(wf, EvidenceBundle())
        _emit_investigation_log(ticket, result)
        return result

    # ── Resolve evidence via the real workflow-aware lookup ──────
    state: AgentState = {
        "case_id": ticket.public_case_ref or ticket.ticket_id,
        "extracted_info": extracted,
        "selected_workflow": wf,
        "user_id": extracted.user_id or "",
        "errors": [],
        "audit_event_ids": [],
    }
    result.evidence_lookup_called = True
    try:
        ev_update = fetch_fn(state)
        state.update(ev_update)
    except Exception as exc:  # noqa: BLE001 — investigation must never 500 the page
        logger.exception("[Investigation] evidence fetch failed for %s", ticket.ticket_id)
        result.notes.append(f"evidence_fetch_error:{exc}")
        result.missing_evidence = ["Không truy vấn được bằng chứng (lỗi hệ thống)"]
        result.likely_issue = "Tạm thời chưa truy vấn được bằng chứng."
        result.why_staff_action_needed = "Cần nhân viên thử lại hoặc kiểm tra thủ công."
        result.evidence_summary, _ = _build_evidence_summary(wf, EvidenceBundle())
        _emit_investigation_log(ticket, result)
        return result

    evidence: EvidenceBundle = state.get("evidence_bundle") or EvidenceBundle()
    tool_results: dict = state.get("tool_results", {}) or {}
    # fetch_evidence may refine the workflow from the transaction's service_type
    wf = state.get("selected_workflow") or wf
    result.selected_workflow = wf

    # ── Run the deterministic rule engine ────────────────────────
    result.rule_engine_called = True
    try:
        rule_update = rule_fn(state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[Investigation] rule engine failed for %s", ticket.ticket_id)
        rule_update = {}
        result.notes.append(f"rule_error:{exc}")

    rd = rule_update.get("rule_decision") or {}
    result.rule_action = rd.get("action", "") or ""
    result.rule_diagnosis_code = _stable_code(rd.get("diagnosis", ""))
    result.approval_required = bool(rule_update.get("approval_required", False))

    # ── Assemble the staff-facing diagnosis ──────────────────────
    has_any_evidence = any(
        getattr(evidence, attr, None) is not None
        for attr, _ in _EVIDENCE_SOURCES.get(wf, [])
    )
    result.resolved = has_any_evidence
    result.evidence_found = has_any_evidence
    result.confirmed_facts = _collect_confirmed_facts(wf, evidence)
    result.what_was_checked = _checked_labels(wf, tool_results, evidence)
    result.evidence_summary, result.missing_evidence = _build_evidence_summary(wf, evidence)

    text = _DIAGNOSIS_TEXT.get(result.rule_diagnosis_code, {})
    result.likely_issue = text.get("issue", "") or (
        "Đã truy vấn bằng chứng; cần nhân viên xác nhận hướng xử lý."
        if has_any_evidence else "Chưa truy vấn được bằng chứng cho giao dịch này."
    )
    # "Tiền/vấn đề đang nằm ở đâu" = the diagnosis finding (where money/problem is).
    result.money_or_issue_location = result.likely_issue
    # "Likely bottleneck" = which processing step the recommended action targets.
    result.likely_bottleneck = _ACTION_BOTTLENECK.get(
        result.rule_action,
        "Cần nhân viên xác định bước xử lý" if has_any_evidence else "",
    )
    result.why_staff_action_needed = text.get("why", "") or (
        "Cần nhân viên kiểm tra và quyết định bước xử lý."
    )

    result.confidence = _confidence(wf, has_any_evidence, evidence, result.rule_action)
    result.risk_level = _derive_risk(wf, result.rule_action, result.approval_required)

    _emit_investigation_log(ticket, result)
    return result

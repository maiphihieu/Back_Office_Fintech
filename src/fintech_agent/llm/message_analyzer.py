"""Unified LLM-first customer message analyzer.

Single entry point for ALL customer message understanding:
  - Intent / message_type classification
  - Follow-up detection (replaces separate followup_analyzer)
  - Structured field extraction
  - Customer emotion & goal inference

LLM-first with generic deterministic fallback.

SAFETY INVARIANTS:
  - LLM is used ONLY for understanding and extraction.
  - LLM MUST NOT decide refund/unlock/force-success/payout.
  - LLM MUST NOT expose internal evidence, fraud score, device signals.
  - All business decisions remain deterministic (rule engine / workflow).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ─── Policy Loader ──────────────────────────────────────────────

_POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "customer_response_policy.yaml"
_policy_cache: dict | None = None


def load_response_policy() -> dict:
    """Load customer response policy from YAML. Cached after first load."""
    global _policy_cache
    if _policy_cache is not None:
        return _policy_cache

    try:
        with open(_POLICY_PATH, "r", encoding="utf-8") as f:
            _policy_cache = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("[Policy] Failed to load %s: %s", _POLICY_PATH, exc)
        _policy_cache = {}

    return _policy_cache


def get_workflow_policy(workflow: str) -> dict:
    """Get workflow-specific policy section."""
    policy = load_response_policy()
    return policy.get("workflows", {}).get(workflow, {})


# ─── Data Structures ────────────────────────────────────────────

@dataclass
class ExtractedFields:
    """Structured fields extracted from customer message."""
    transaction_id: str | None = None
    order_id: str | None = None
    bill_code: str | None = None
    merchant_id: str | None = None
    amount: int | None = None
    amount_text: str | None = None
    approximate_time_text: str | None = None
    approximate_date_text: str | None = None
    bank_name: str | None = None
    bank_reference: str | None = None
    provider_name: str | None = None
    service_type: str | None = None
    issue_type: str | None = None


@dataclass
class MessageAnalysis:
    """Unified result of customer message analysis.

    Replaces both old CustomerMessageAnalysis and FollowupAnalysis.
    """
    message_type: str = "unknown"
    # new_complaint | follow_up | provide_missing_info | correct_previous_info |
    # ask_status | ask_eta | ask_what_to_do | ask_where_money_is |
    # provide_sensitive_info | greeting | out_of_scope | unknown
    belongs_to_active_case: bool = False
    confidence: float = 0.0
    workflow_hint: str = "unknown"
    customer_emotion: str = "neutral"
    # neutral | confused | worried | urgent | angry
    extracted: ExtractedFields = field(default_factory=ExtractedFields)
    customer_goal: str = ""
    safe_next_step_needed: str = ""
    is_correction: bool = False  # customer corrects a previous claim


@dataclass
class ActiveCaseContext:
    """Context of the customer's currently active case.

    Shared by both the analyzer and the customer_chat endpoint.
    """
    case_id: str = ""
    selected_workflow: str = ""
    service_type: str = ""
    missing_fields: list[str] = field(default_factory=list)
    last_public_response: str = ""
    extracted_info: dict = field(default_factory=dict)
    resolved_entity_id: str | None = None
    # Last public-safe diagnosis built for this case (reused by follow-ups
    # so e.g. "tiền giải ngân kẹt ở đâu?" can answer from the prior diagnosis).
    last_diagnosis: dict = field(default_factory=dict)
    # Accumulated (already-redacted) conversation, for back-office handoff.
    transcript: list[dict] = field(default_factory=list)
    customer_problem: str = ""          # first customer message (redacted)
    customer_emotion: str = "neutral"   # latest detected emotion
    subject_type: str = ""              # wallet_user | merchant

    @property
    def awaiting_field(self) -> str:
        """Primary field the case is waiting for."""
        if self.missing_fields:
            return self.missing_fields[0]
        return ""


# ─── Supported Message Types ───────────────────────────────────

SUPPORTED_MESSAGE_TYPES = frozenset({
    "new_complaint",
    "follow_up",
    "workflow_switch",
    "provide_missing_info",
    "correct_previous_info",
    "ask_status",
    "ask_eta",
    "ask_what_to_do",
    "ask_where_money_is",
    "provide_sensitive_info",
    "greeting",
    "thank_you",
    "out_of_scope",
    "unknown",
})

# Intents that are NOT a complaint/case interaction. These must never be forced
# into a financial workflow (no transaction_id ask, no case creation).
NON_CASE_MESSAGE_TYPES = frozenset({"greeting", "out_of_scope"})


# ─── LLM System Prompt ─────────────────────────────────────────

_LLM_SYSTEM_PROMPT = """\
You are a fintech customer support message analyzer for a Vietnamese wallet/payment app.
Your ONLY job is to classify the message type, extract structured fields, \
and understand the customer's goal in context. \
Do NOT make business decisions. \
Do NOT decide refunds, unlocks, force-success, or payouts.

## Context
You receive:
- customer_message: the customer's text
- active_case_context: info about any ongoing support case
- session_context: info about the logged-in user type

## Message types:
- new_complaint: customer describes a new problem not related to active case
- follow_up: customer responds to the ONGOING case about the SAME service/issue
- workflow_switch: there is an active case, and the customer now raises a problem \
about a DIFFERENT service than the active case (e.g. active case is wallet_topup \
but the customer now talks about a train ticket not received, an account being \
locked, an electricity bill, or a merchant settlement). Set workflow_hint to the \
NEW service the customer is now talking about, NOT the active case's workflow.
- provide_missing_info: customer provides transaction ID, amount, time, bank, reference, etc.
- correct_previous_info: customer corrects something they said before ("à tôi nhầm", \
"không phải 500k mà 300k", "sai rồi, 200k mới đúng"). Set is_correction=true.
- ask_status: customer asks about case progress ("đã xử lý chưa", "tình trạng")
- ask_eta: customer asks how long it will take ("bao lâu nữa", "khi nào xong")
- ask_what_to_do: customer asks what info they should provide ("cần cung cấp gì", "phải làm gì")
- ask_where_money_is: customer asks where their money is ("tiền đang ở đâu")
- provide_sensitive_info: customer sends PIN, OTP, password, full card number
- greeting: pure greeting / small talk with no support request ("xin chào", \
"bạn tên gì", "chào shop")
- thank_you: customer acknowledges, thanks, or signals the conversation can end \
("được rồi", "ok", "cảm ơn", "tôi hiểu rồi", "cảm ơn bạn", "thanks"). \
IMPORTANT: if there is an active case and customer sends a short acknowledgement, \
classify as thank_you — NOT greeting, NOT new_complaint, NOT unknown.
- out_of_scope: request unrelated to wallet/payment/transaction/account support \
for THIS app — e.g. general knowledge ("1+1 bằng mấy", "thời tiết"), jokes/chit-chat \
("kể chuyện cười"), or services this app does NOT provide (đặt vé máy bay, vay tiền, \
tư vấn chứng khoán). When in doubt between out_of_scope and a real complaint, prefer \
the real complaint type.
- unknown: cannot determine

## Critical rules:
- greeting and out_of_scope are NOT complaints. For them set \
belongs_to_active_case=false and workflow_hint="unknown". Do NOT invent a workflow \
or transaction fields for these.
- An active case does NOT turn an unrelated message into a follow_up. If the new \
message is clearly greeting/out_of_scope, classify it as such even when a case exists.
- Short messages ("không nhớ", "bao lâu", "tiền đâu") MUST be interpreted \
relative to active_case_context if it exists.
- If no active case exists and message is short/vague but on-topic, classify as new_complaint.
- Vietnamese slang: "nửa triệu"=500000, "5 lít"=500000, "5 củ"=5000000, \
"500k"=500000, "1tr"=1000000.
- Extract amounts as integers in VND.
- Extract bank names as-is.
- If message contains PIN/OTP/password/full card number → provide_sensitive_info.

Return ONLY this JSON:
{
  "message_type": "...",
  "belongs_to_active_case": true/false,
  "is_correction": true/false,
  "confidence": 0.0-1.0,
  "workflow_hint": "wallet_topup|fraud_account_lock|train_ticket|utility_bill|merchant_settlement_delay|unknown",
  "customer_emotion": "neutral|confused|worried|urgent|angry",
  "extracted": {
    "transaction_id": null,
    "order_id": null,
    "bill_code": null,
    "merchant_id": null,
    "amount": null,
    "amount_text": null,
    "approximate_time_text": null,
    "approximate_date_text": null,
    "bank_name": null,
    "bank_reference": null,
    "provider_name": null,
    "service_type": null,
    "issue_type": null
  },
  "customer_goal": "brief summary of what customer wants",
  "safe_next_step_needed": "brief description of what the system should do next"
}

is_correction rules:
- Set true when customer explicitly corrects previous info ("nhầm", "sai rồi", \
"không phải X mà Y", "tôi nói sai").
- When is_correction=true, message_type should be "correct_previous_info".
- The extracted fields should contain the NEW (corrected) values."""


# ─── LLM Analyzer ──────────────────────────────────────────────

def _analyze_with_llm(
    message: str,
    active_case_context: dict,
    session_context: dict,
) -> MessageAnalysis | None:
    """Use OpenAI to understand the customer message.

    Returns None if LLM is unavailable or fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    user_prompt = json.dumps({
        "customer_message": message,
        "active_case_context": active_case_context,
        "session_context": session_context,
    }, ensure_ascii=False)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=10.0)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=512,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        if not raw:
            return None

        parsed = json.loads(raw)
        return _parse_llm_response(parsed)

    except Exception as exc:
        logger.warning(
            "[MessageAnalyzer] LLM failed (%s): %s — falling back",
            type(exc).__name__, exc,
        )
        return None


def _parse_llm_response(parsed: dict) -> MessageAnalysis:
    """Parse and validate LLM JSON response."""
    msg_type = parsed.get("message_type", "unknown")
    if msg_type not in SUPPORTED_MESSAGE_TYPES:
        msg_type = "unknown"

    extracted_raw = parsed.get("extracted", {})
    amount = extracted_raw.get("amount")
    if amount is not None:
        try:
            amount = int(amount)
        except (ValueError, TypeError):
            amount = None

    extracted = ExtractedFields(
        transaction_id=extracted_raw.get("transaction_id"),
        order_id=extracted_raw.get("order_id"),
        bill_code=extracted_raw.get("bill_code"),
        merchant_id=extracted_raw.get("merchant_id"),
        amount=amount,
        amount_text=extracted_raw.get("amount_text"),
        approximate_time_text=extracted_raw.get("approximate_time_text"),
        approximate_date_text=extracted_raw.get("approximate_date_text"),
        bank_name=extracted_raw.get("bank_name"),
        bank_reference=extracted_raw.get("bank_reference"),
        provider_name=extracted_raw.get("provider_name"),
        service_type=extracted_raw.get("service_type"),
        issue_type=extracted_raw.get("issue_type"),
    )

    emotion = parsed.get("customer_emotion", "neutral")
    if emotion not in ("neutral", "confused", "worried", "urgent", "angry"):
        emotion = "neutral"

    is_correction = bool(parsed.get("is_correction", False))
    # If LLM says correct_previous_info, ensure is_correction is True
    if msg_type == "correct_previous_info":
        is_correction = True

    return MessageAnalysis(
        message_type=msg_type,
        belongs_to_active_case=bool(parsed.get("belongs_to_active_case", False)),
        confidence=float(parsed.get("confidence", 0.5)),
        workflow_hint=parsed.get("workflow_hint", "unknown") or "unknown",
        customer_emotion=emotion,
        extracted=extracted,
        customer_goal=parsed.get("customer_goal", ""),
        safe_next_step_needed=parsed.get("safe_next_step_needed", ""),
        is_correction=is_correction,
    )


# ─── Generic Deterministic Fallback ────────────────────────────
# Minimal and generic: detect structural patterns only.
# No hard-coded phrase lists as the main logic.

# Sensitive info detection (always checked)
_SENSITIVE_INFO_RE = re.compile(
    r"(?:"
    r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"  # full card number
    r"|(?:mã\s+)?(?:PIN|OTP)\s*(?:là|:)?\s*\d+"          # PIN/OTP with digits
    r"|mật\s+khẩu\s*(?:là|:)\s*\S+"                       # password disclosure
    r"|password\s*(?:is|:)\s*\S+"                          # password in english
    r")",
    re.IGNORECASE,
)

# Generic ID patterns (transaction, order, bill)
_GENERIC_ID_RE = re.compile(
    r"(?:"
    r"(?:TXN|txn|ORD|ord)[-_]\w{3,}"                      # TXN_xxx, ORD_xxx
    r"|(?:mã\s+(?:giao\s+dịch|GD|đơn\s+hàng|hóa\s+đơn))\s*:?\s*([\w\-]{4,})"
    r")",
    re.IGNORECASE,
)

# Generic amount detection
_AMOUNT_LONG_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d{4,})"
    r"\s*(k|nghìn|ngàn|tr|triệu|đ|đồng|vnđ|vnd)?",
    re.IGNORECASE,
)
_AMOUNT_SHORT_RE = re.compile(
    r"(\d{1,3})\s*(k|nghìn|ngàn|tr|triệu)(?!\w)",
    re.IGNORECASE,
)

# Vietnamese slang amounts
_SLANG_AMOUNT_MAP: dict[str, int] = {
    "nửa triệu": 500_000, "nửa củ": 500_000,
    "1 lít": 100_000, "2 lít": 200_000, "3 lít": 300_000,
    "4 lít": 400_000, "5 lít": 500_000, "6 lít": 600_000,
    "7 lít": 700_000, "8 lít": 800_000, "9 lít": 900_000,
    "10 lít": 1_000_000,
    "1 củ": 1_000_000, "2 củ": 2_000_000, "3 củ": 3_000_000,
    "5 củ": 5_000_000, "10 củ": 10_000_000,
}
_SLANG_AMOUNT_RE = re.compile(
    r"(" + "|".join(re.escape(k) for k in _SLANG_AMOUNT_MAP) + r")",
    re.IGNORECASE,
)

# Time phrases
_TIME_PHRASE_RE = re.compile(
    r"(?:"
    r"(?:khoảng|tầm|gần|lúc)\s+\d{1,2}\s*(?:h|g|giờ)\s*(?:sáng|chiều|tối|trưa)?"
    r"|\d{1,2}\s*(?:h|giờ)\s*(?:sáng|chiều|tối|trưa)"
    r"|(?:sáng|chiều|tối|trưa)\s+(?:nay|qua)"
    r"|hôm\s+(?:nay|qua)"
    r"|(?:buổi\s+)?(?:sáng|chiều|tối|trưa)"
    r"|tầm\s+(?:gần\s+)?\d{1,2}\s*(?:h|giờ)"
    r")",
    re.IGNORECASE,
)

# Date phrases
_DATE_PHRASE_RE = re.compile(
    r"(sáng\s+nay|chiều\s+nay|tối\s+nay|hôm\s+nay|hôm\s+qua|tối\s+qua)",
    re.IGNORECASE,
)

# Bank reference (FT/BANK/REF + digits)
_BANK_REF_RE = re.compile(
    r"(?:mã\s+(?:tham\s+chiếu|ref)|tham\s+chiếu|reference)\s*:?\s*([A-Z0-9]{6,})"
    r"|([A-Z]{2,4}\d{8,})",
    re.IGNORECASE,
)

# Bank confirmation
_BANK_CONFIRMED_RE = re.compile(
    r"(?:"
    r"(?:app\s+)?bank\s+(?:đã\s+)?báo\s+(?:thành\s+công|giao\s+dịch\s+thành\s+công)"
    r"|ngân\s+hàng\s+(?:đã\s+)?(?:trừ|báo|xác\s+nhận).*(?:rồi|tiền|thành\s+công)"
    r"|bank\s+trừ\s+rồi"
    r"|đã\s+trừ.*(?:nhưng|mà)\s+ví\s+chưa"
    r"|(?:tài\s+khoản\s+)?bank\s+báo\s+thành\s+công"
    r"|bill\s+chuyển\s+khoản"
    r"|có\s+bill"
    r")",
    re.IGNORECASE,
)

# Bank name detection (reuse from alternative_resolver)
_BANK_ALIASES: dict[str, str] = {
    "vietcombank": "VCB", "vcb": "VCB",
    "techcombank": "TCB", "tcb": "TCB",
    "mbbank": "MB", "mb": "MB",
    "bidv": "BIDV", "agribank": "AGRIBANK",
    "tpbank": "TPBANK", "vpbank": "VPBANK",
    "sacombank": "SACOMBANK", "acb": "ACB", "shb": "SHB",
    "vietinbank": "VIETINBANK", "ctg": "VIETINBANK",
    "hdbank": "HDBANK", "msb": "MSB", "ocb": "OCB",
}
_BANK_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _BANK_ALIASES) + r")\b",
    re.IGNORECASE,
)


def _fallback_extract_fields(message: str) -> ExtractedFields:
    """Generic regex extraction — FALLBACK when LLM is unavailable."""
    # Bank reference (extract FIRST so we can exclude from amount)
    bank_reference = None
    ref_match = _BANK_REF_RE.search(message)
    if ref_match:
        bank_reference = ref_match.group(1) or ref_match.group(2)

    # Amount: slang first, then standard
    # Mask out bank reference span to avoid extracting ref digits as amount
    amount_search_text = message
    if ref_match:
        start, end = ref_match.start(), ref_match.end()
        amount_search_text = message[:start] + " " * (end - start) + message[end:]

    amount = None
    amount_text = None
    slang_match = _SLANG_AMOUNT_RE.search(amount_search_text)
    if slang_match:
        amount_text = slang_match.group(1)
        amount = _SLANG_AMOUNT_MAP.get(amount_text.lower())
    else:
        for regex in (_AMOUNT_LONG_RE, _AMOUNT_SHORT_RE):
            m = regex.search(amount_search_text)
            if m:
                raw_num = m.group(1).replace(".", "").replace(",", "")
                try:
                    val = int(raw_num)
                except ValueError:
                    continue
                suffix = (m.group(2) or "").lower()
                if suffix in ("k", "nghìn", "ngàn"):
                    val *= 1000
                elif suffix in ("tr", "triệu"):
                    val *= 1_000_000
                if 1000 <= val <= 1_000_000_000:
                    amount = val
                    amount_text = str(val)
                    break

    # Bank name
    bank_name = None
    bank_match = _BANK_NAME_RE.search(message)
    if bank_match:
        bank_name = _BANK_ALIASES.get(bank_match.group(1).lower())

    # Time
    time_text = None
    time_match = _TIME_PHRASE_RE.search(message)
    if time_match:
        time_text = time_match.group(0).strip()

    # Date
    date_text = None
    date_match = _DATE_PHRASE_RE.search(message)
    if date_match:
        date_text = date_match.group(1).strip()

    # Generic IDs
    txn_id = None
    id_match = _GENERIC_ID_RE.search(message)
    if id_match:
        txn_id = id_match.group(1) or id_match.group(0)

    # Issue type from bank confirmation
    issue_type = None
    if _BANK_CONFIRMED_RE.search(message):
        issue_type = "bank_confirmed_wallet_pending"

    # Service/issue type from account lock signal
    if _ACCOUNT_LOCK_RE.search(message):
        issue_type = issue_type or "account_locked"

    return ExtractedFields(
        transaction_id=txn_id,
        amount=amount,
        amount_text=amount_text,
        approximate_time_text=time_text,
        approximate_date_text=date_text,
        bank_name=bank_name,
        bank_reference=bank_reference,
        issue_type=issue_type,
    )


# Off-topic / greeting detection for the deterministic fallback (mock mode).
# Conservative: only fires when the message has NO financial signal.
_GREETING_RE = re.compile(
    r"^(?:xin\s*chào|chào(?:\s+(?:shop|bạn|ad|anh|chị))?|hello|hi|hey|"
    r"alo|a\s*lô|good\s+(?:morning|evening|afternoon)|"
    r"bạn\s+tên\s+(?:là\s+)?gì|ban\s+ten\s+gi|bạn\s+là\s+ai)\b",
    re.IGNORECASE,
)
_OUT_OF_SCOPE_RE = re.compile(
    r"(thời\s*tiết|thoi\s*tiet|kể\s*(?:chuyện|truyện)|chuyện\s*cười|chuyen\s*cuoi|"
    r"vé\s*máy\s*bay|ve\s*may\s*bay|đặt\s*vé\s*máy|vay\s*(?:tiền|tien|vốn)|cho\s*vay|"
    r"chứng\s*khoán|chung\s*khoan|crypto|bitcoin|giá\s*vàng|gia\s*vang|"
    r"bóng\s*đá|bong\s*da|nấu\s*ăn|nau\s*an|làm\s*thơ|lam\s*tho|viết\s*(?:văn|thơ)|"
    r"bài\s*tập|dịch\s*(?:giúp|hộ)|mấy\s*giờ\s*rồi|hôm\s*nay\s*(?:là\s*)?thứ\s*mấy)",
    re.IGNORECASE,
)
_MATH_RE = re.compile(
    r"\b\d+\s*(?:[+\-*/x×]|cộng|trừ|nhân|chia)\s*\d+", re.IGNORECASE,
)

# Correction detection ("à tôi nhầm", "sai rồi", "không phải X mà Y")
_CORRECTION_RE = re.compile(
    r"(?:"
    r"(?:à\s+)?(?:tôi|mình|em|t)\s+nhầm"
    r"|sai\s+rồi"
    r"|(?:tôi|mình|em)\s+(?:nói|nhập|ghi)\s+sai"
    r"|không\s+phải\s+\S+\s*(?:mà|,)\s*"
    r"|nhầm\s+(?:rồi|lẫn)"
    r"|(?:lần\s+trước|ở\s+trên)\s+(?:tôi|mình|em)\s+nói\s+sai"
    r"|thực\s+ra\s+(?:là|số\s+tiền)"
    r")",
    re.IGNORECASE,
)

# Thank-you / acknowledgement ("được rồi", "ok", "cảm ơn", "tôi hiểu rồi")
# Only matches SHORT messages — a long message with "cảm ơn" embedded in a new
# complaint should NOT be classified as thank_you.
_THANK_YOU_RE = re.compile(
    r"^\s*(?:"
    r"(?:cảm|cam)\s*ơn(?:\s+(?:bạn|anh|chị|shop|ad|nhé|nha|nhiều|lắm|nhá|ạ))?"
    r"|(?:được|dc|đc)\s*(?:rồi|r)"
    r"|ok(?:ay)?(?:\s+(?:bạn|anh|chị|nhé|ạ))?"
    r"|(?:tôi|mình|em|t)\s+(?:hiểu|biết|nắm)\s+rồi"
    r"|(?:tôi|mình|em|t)\s+hiểu"
    r"|rồi\s+(?:ạ|nhé|nha)"
    r"|vâng(?:\s+ạ)?"
    r"|dạ(?:\s+(?:vâng|ok|được|ạ))?"
    r"|uhm?(?:\s+(?:ok|được|rồi))?"
    r"|thanks?(?:\s+you)?"
    r"|thank\s+you"
    r"|thôi\s+(?:được|đc)\s+rồi"
    r"|(?:biết|nắm)\s+rồi"
    r"|(?:tôi|mình|em)\s+ghi\s+nhận"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _has_financial_signal(extracted: ExtractedFields) -> bool:
    """True when the message carries any wallet/payment evidence field."""
    return any([
        extracted.transaction_id, extracted.order_id, extracted.bill_code,
        extracted.amount, extracted.bank_name, extracted.bank_reference,
        extracted.approximate_time_text, extracted.approximate_date_text,
        extracted.issue_type,
    ])


# ─── Account lock / fraud signal detection ──────────────────────
# Detects when the customer is talking about an account lock/freeze, NOT topup.
# Used by the fallback classifier to override workflow_hint when it would
# otherwise default to the active case's workflow.

_ACCOUNT_LOCK_RE = re.compile(
    r"(?:"
    r"(?:tài\s*khoản|tk)\s*(?:\w+\s+){0,4}(?:(?:bị|đã|đang)\s*)*(?:khóa|khoá|block|lock|hạn\s*chế|freeze|đóng\s*băng|chặn)"
    r"|(?:bị|đã|đang)\s*khóa\s*(?:tài\s*khoản|tk)"
    r"|không\s*(?:thể\s+)?(?:đăng\s*nhập|login|rút\s*tiền|chuyển\s*tiền|giao\s*dịch)\s*(?:được)?\s*(?:vì|do)?\s*(?:bị\s*)?(?:khóa|khoá|lock|hạn\s*chế|chặn)"
    r"|khóa\s*(?:tài\s*khoản|tk|account)"
    r"|account\s*(?:locked|blocked|frozen|restricted|suspended)"
    r"|bảo\s*mật.*(?:khóa|khoá|hạn\s*chế)"
    r"|(?:khóa|khoá)\s*(?:vô\s*cớ|bất\s*ngờ|đột\s*ngột)"
    r")",
    re.IGNORECASE,
)


# ─── Wallet topup signal detection ──────────────────────────────
# Detects when the customer is talking about a wallet topup issue.

_WALLET_TOPUP_RE = re.compile(
    r"(?:"
    r"nạp\s*tiền(?:\s+vào\s+(?:ví|tài\s*khoản))?"
    r"|ví\s*(?:chưa|không|vẫn)\s*(?:nhận|cập\s*nhật|thấy|hiện|báo)"
    r"|nạp\s*ví"
    r"|top\s*-?\s*up"
    r"|bank\s*(?:đã\s*)?trừ\s*tiền"
    r"|ngân\s*hàng\s*(?:đã\s*)?trừ"
    r"|ví\s*vẫn\s*0"
    r"|số\s*dư\s*ví"
    r")",
    re.IGNORECASE,
)

# ─── Train ticket signal detection ──────────────────────────────
# Detects when the customer is talking about a train ticket issue.

_TRAIN_TICKET_RE = re.compile(
    r"(?:"
    r"vé\s*tàu"
    r"|mua\s*vé(?:\s+tàu)?"
    r"|chưa\s*nhận\s*(?:được\s*)?vé"
    r"|không\s*nhận\s*(?:được\s*)?vé"
    r"|chưa\s*có\s*vé"
    r"|thanh\s*toán\s*vé"
    r"|đặt\s*vé"
    r"|train\s*ticket"
    r")",
    re.IGNORECASE,
)

# ─── Merchant settlement signal detection ───────────────────────

_MERCHANT_SETTLEMENT_RE = re.compile(
    r"(?:"
    r"giải\s*ngân"
    r"|quyết\s*toán"
    r"|payout"
    r"|settlement"
    r"|thanh\s*toán\s*d\+\d"
    r"|chu\s*kỳ\s*d\+\d"
    r"|merchant"
    r"|cửa\s*hàng.*(?:chưa|chờ|chậm)\s*(?:nhận|thanh\s*toán)"
    r")",
    re.IGNORECASE,
)


def _detect_workflow_hint_from_message(message: str) -> str | None:
    """Detect a workflow hint purely from the message content.

    Returns a workflow_id when the message has STRONG signals for a specific
    workflow. Returns None if no strong signal is detected (caller should
    fall back to the active case's workflow).

    This catches cross-workflow misrouting in the fallback path — e.g. when
    a customer with an active wallet_topup case says "tài khoản bị khóa",
    or a customer with a train_ticket case says "nạp tiền vào ví".

    Order matters — more specific patterns are checked first:
    1. Account lock (most specific keywords)
    2. Merchant settlement (domain-specific keywords)
    3. Train ticket
    4. Wallet topup (broadest keywords — checked last)
    """
    if _ACCOUNT_LOCK_RE.search(message):
        return "fraud_account_lock"
    if _MERCHANT_SETTLEMENT_RE.search(message):
        return "merchant_settlement_delay"
    if _TRAIN_TICKET_RE.search(message):
        return "train_ticket"
    if _WALLET_TOPUP_RE.search(message):
        return "wallet_topup"
    return None


def _fallback_classify(
    message: str,
    extracted: ExtractedFields,
    has_active_case: bool,
    awaiting_field: str,
) -> tuple[str, float, bool]:
    """Generic deterministic classification.

    Returns (message_type, confidence, is_correction).

    Rules:
    1. Sensitive info → provide_sensitive_info
    2. Correction ("à tôi nhầm") → correct_previous_info
    3. Off-topic / greeting (no financial signal) → out_of_scope / greeting
    4. Direct ID → provide_missing_info
    5. Alternative fields when awaiting → provide_missing_info
    6. Short msg + active case → follow_up
    7. Default → new_complaint (long) or unknown (short)
    """
    msg = message.strip()

    # 1. Sensitive info always wins
    if _SENSITIVE_INFO_RE.search(msg):
        return "provide_sensitive_info", 0.95, False

    # 2. Correction detection — before off-topic check
    if has_active_case and _CORRECTION_RE.search(msg):
        return "correct_previous_info", 0.9, True

    # 2.5 Thank-you / acknowledgement — must come BEFORE greeting/off-topic
    #     so "được rồi", "ok", "cảm ơn" don't fall into greeting or follow_up.
    if _THANK_YOU_RE.search(msg) and len(msg) < 60:
        return "thank_you", 0.9, False

    # 3. Off-topic / greeting — only when there is no financial signal at all,
    #    so a real complaint is never misclassified.
    if not _has_financial_signal(extracted):
        if _OUT_OF_SCOPE_RE.search(msg) or _MATH_RE.search(msg):
            return "out_of_scope", 0.85, False
        if _GREETING_RE.search(msg) and len(msg) < 40:
            return "greeting", 0.8, False

    # 4. Direct ID provided
    if extracted.transaction_id or extracted.order_id or extracted.bill_code:
        return "provide_missing_info", 0.9, False

    # 5. Alternative fields
    has_alt = any([
        extracted.amount, extracted.approximate_time_text,
        extracted.approximate_date_text, extracted.bank_name,
        extracted.bank_reference, extracted.issue_type,
    ])
    if has_alt and has_active_case:
        return "provide_missing_info", 0.85, False

    # 6. Short message + active case → follow_up
    if has_active_case and len(msg) < 80:
        return "follow_up", 0.7, False

    # 7. Long message → new_complaint
    if len(msg) > 30:
        return "new_complaint", 0.6, False

    return "unknown", 0.3, False


def _fallback_analyze(
    message: str,
    active_case_context: dict,
    session_context: dict,
) -> MessageAnalysis:
    """Full fallback: extraction + classification."""
    has_active = bool(active_case_context.get("selected_workflow"))
    awaiting = active_case_context.get("awaiting_field", "")
    active_workflow = active_case_context.get("selected_workflow", "unknown") or "unknown"

    extracted = _fallback_extract_fields(message)
    msg_type, confidence, is_correction = _fallback_classify(
        message, extracted, has_active, awaiting,
    )

    # Off-topic / greeting never belong to a case and carry no workflow.
    if msg_type in NON_CASE_MESSAGE_TYPES:
        return MessageAnalysis(
            message_type=msg_type,
            belongs_to_active_case=False,
            confidence=confidence,
            workflow_hint="unknown",
            extracted=extracted,
        )

    # Detect if the message has a strong signal for a DIFFERENT workflow
    # than the active case. This prevents cross-workflow misrouting where
    # e.g. "tài khoản bị khóa" gets routed to wallet_topup because
    # that's the active case.
    detected_hint = _detect_workflow_hint_from_message(message)
    if detected_hint and detected_hint != active_workflow and has_active:
        # Clear cross-workflow reuse: this is a workflow switch
        return MessageAnalysis(
            message_type="workflow_switch" if has_active else "new_complaint",
            belongs_to_active_case=False,
            confidence=max(confidence, 0.85),
            workflow_hint=detected_hint,
            extracted=extracted,
            is_correction=False,
        )

    # If the detected workflow matches the active case, or no signal,
    # use the active case workflow as before.
    workflow_hint = detected_hint or active_workflow
    belongs = msg_type not in ("new_complaint", "unknown") and has_active

    return MessageAnalysis(
        message_type=msg_type,
        belongs_to_active_case=belongs,
        confidence=confidence,
        workflow_hint=workflow_hint,
        extracted=extracted,
        is_correction=is_correction,
    )


# ─── Public Entry Points ───────────────────────────────────────

def analyze_customer_message(
    message: str,
    active_case_context: dict | None = None,
    session_context: dict | None = None,
) -> MessageAnalysis:
    """Unified entry point for customer message understanding.

    LLM-first with generic deterministic fallback.

    Args:
        message: Customer's free-text message.
        active_case_context: Dict with selected_workflow, service_type,
            awaiting_field, has_active_case.
        session_context: Dict with subject_type, is_authenticated.

    Returns:
        MessageAnalysis with message_type + extracted fields.
    """
    if active_case_context is None:
        active_case_context = {}
    if session_context is None:
        session_context = {}

    # Try LLM first
    llm_result = _analyze_with_llm(message, active_case_context, session_context)

    if llm_result is not None and llm_result.confidence >= 0.65:
        # The LLM is reliable for message_type but sometimes punts on the
        # workflow (returns "unknown"). When that happens, backfill the
        # workflow from the deterministic keyword classifier so a clearly
        # on-topic complaint is never stranded without a workflow to verify.
        if (not llm_result.workflow_hint) or llm_result.workflow_hint == "unknown":
            fb = _fallback_analyze(message, active_case_context, session_context)
            if fb.workflow_hint and fb.workflow_hint != "unknown":
                logger.info(
                    "[MessageAnalyzer] LLM workflow unknown — backfilled '%s' "
                    "from deterministic classifier",
                    fb.workflow_hint,
                )
                llm_result.workflow_hint = fb.workflow_hint
        logger.info(
            "[MessageAnalyzer] LLM: type=%s, conf=%.2f, workflow=%s, emotion=%s, "
            "amount=%s, bank=%s",
            llm_result.message_type, llm_result.confidence,
            llm_result.workflow_hint, llm_result.customer_emotion,
            llm_result.extracted.amount,
            llm_result.extracted.bank_name,
        )
        return llm_result

    # Fallback
    fallback = _fallback_analyze(message, active_case_context, session_context)

    if llm_result is not None and llm_result.confidence > 0.0:
        logger.info(
            "[MessageAnalyzer] LLM conf %.2f < 0.65, using fallback (type=%s)",
            llm_result.confidence, fallback.message_type,
        )

    logger.info(
        "[MessageAnalyzer] Fallback: type=%s, conf=%.2f, amount=%s, bank=%s",
        fallback.message_type, fallback.confidence,
        fallback.extracted.amount, fallback.extracted.bank_name,
    )

    return fallback


# ─── Backward Compatibility Aliases ─────────────────────────────
# Old code imports these names. Keep them working.

# Alias for old CustomerMessageAnalysis
CustomerMessageAnalysis = MessageAnalysis

# Alias for old analyze_customer_message_context
def analyze_customer_message_context(
    message: str,
    active_case_context: dict,
    session_context: dict,
) -> MessageAnalysis:
    """Backward-compatible alias for analyze_customer_message."""
    return analyze_customer_message(message, active_case_context, session_context)

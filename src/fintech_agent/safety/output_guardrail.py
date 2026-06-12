"""Output guardrail for customer-facing responses.

Scans LLM-composed responses for forbidden terms (internal jargon,
sensitive data fields, tool names) before they reach the customer.

If violations are found, the response is either sanitized or replaced
with a safe fallback built from the policy config.

SAFETY INVARIANTS:
  - All forbidden terms from global + workflow policy are checked.
  - PIN/OTP/password requests are always blocked.
  - Internal field names (rule_id, approval_packet, etc.) are blocked.
  - Fallback response is always safe and generic.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GuardrailResult:
    """Result of output safety check."""
    is_safe: bool = True
    violations: list[str] = field(default_factory=list)
    sanitized_text: str | None = None


# Patterns that should NEVER appear in customer responses,
# regardless of policy config.
#
# NOTE: PIN/OTP/password are NOT blocked here because they are valid
# in safety reminders ("vui lòng không gửi PIN"). They are only blocked
# when used in REQUESTS for credentials (see _SENSITIVE_REQUEST_PATTERNS).
_HARDCODED_BLOCKED_PATTERNS: list[re.Pattern] = [
    # Full card number (16 digits)
    re.compile(r"\b(?:số\s+thẻ\s+đầy\s+đủ|full\s+card\s+number)\b", re.IGNORECASE),
    re.compile(r"\b(?:private[_\s]?key|secret[_\s]?key|api[_\s]?key)\b", re.IGNORECASE),
    re.compile(r"\b(?:CVV|CVC)\b"),
    # Internal system terms — ALWAYS blocked
    re.compile(r"\b(?:force[_\-\s]?success)\b", re.IGNORECASE),
    re.compile(r"\b(?:master[_\s]?wallet)\b", re.IGNORECASE),
    re.compile(r"\b(?:approval[_\s]?packet)\b", re.IGNORECASE),
    re.compile(r"\b(?:action[_\s]?draft)\b", re.IGNORECASE),
    re.compile(r"\b(?:evidence[_\s]?bundle)\b", re.IGNORECASE),
    re.compile(r"\b(?:draft[_\s]?output)\b", re.IGNORECASE),
    re.compile(r"\b(?:rule[_\s]?id)\b", re.IGNORECASE),
    re.compile(r"\b(?:risk[_\s]?score)\b", re.IGNORECASE),
    re.compile(r"\b(?:fraud[_\s]?status)\b", re.IGNORECASE),
    re.compile(r"\b(?:fraud[_\s]?signal)\b", re.IGNORECASE),
    re.compile(r"\b(?:device[_\s]?signal)\b", re.IGNORECASE),
    re.compile(r"\b(?:mcp[_\s]?tool)\b", re.IGNORECASE),
    re.compile(r"\b(?:tool[_\s]?result)\b", re.IGNORECASE),
    re.compile(r"\b(?:audit[_\s]?event)\b", re.IGNORECASE),
    re.compile(r"\b(?:settlement[_\s]?batch)\b", re.IGNORECASE),
    re.compile(r"\b(?:merchant[_\s]?payout[_\s]?internal)\b", re.IGNORECASE),
    re.compile(r"\b(?:reconciliation[_\s]?table)\b", re.IGNORECASE),
    re.compile(r"\b(?:pin[_\s]?hash)\b", re.IGNORECASE),
    re.compile(r"\b(?:internal[_\s]?summary)\b", re.IGNORECASE),
    # Internal raw settlement/payout fields — never expose to customer
    re.compile(r"\b(?:batch[_\s]?status)\b", re.IGNORECASE),
    re.compile(r"\b(?:payout[_\s]?status)\b", re.IGNORECASE),
    # Internal action wording — must not surface in customer chat
    re.compile(r"\b(?:manual[_\s]?payout)\b", re.IGNORECASE),
    re.compile(r"thanh\s+toán\s+thủ\s+công", re.IGNORECASE),
    re.compile(r"\btạo\s+(?:một\s+)?draft\b", re.IGNORECASE),
    re.compile(r"\bdraft\s+(?:manual\s+)?(?:payout|thanh\s+toán)", re.IGNORECASE),
    # Internal "draft" wording in Vietnamese ("tạo bản nháp …") — staff-only.
    re.compile(r"\b(?:tạo|một)\s+bản\s+nháp\b", re.IGNORECASE),
    # Payout / money-movement OVERPROMISES — block immediacy & guarantees
    re.compile(r"đảm\s+bảo\s+(?:bạn\s+)?(?:sẽ\s+)?nhận\s+được\s+(?:số\s+)?tiền", re.IGNORECASE),
    re.compile(r"chắc\s+chắn\s+(?:sẽ\s+)?nhận\s+được\s+(?:số\s+)?tiền", re.IGNORECASE),
    re.compile(r"chuyển\s+tiền\s+ngay", re.IGNORECASE),
]

# Patterns that ACTIVELY REQUEST sensitive info from the customer.
# "Vui lòng không gửi PIN" is a WARNING (safe), but
# "Vui lòng cung cấp mã PIN" is a REQUEST (blocked).
# Negative lookbehind for "không\s+" prevents matching safety warnings.
_SENSITIVE_REQUEST_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:vui\s+lòng\s+)?(?<!không\s)(?:cung\s+cấp|cho|nhập)\s+(?:mã\s+)?(?:PIN|OTP)", re.IGNORECASE),
    re.compile(r"(?:vui\s+lòng\s+)?(?<!không\s)(?:cung\s+cấp|cho|nhập)\s+mật\s+khẩu", re.IGNORECASE),
    re.compile(r"(?:vui\s+lòng\s+)?(?<!không\s)(?:cung\s+cấp|cho|nhập)\s+số\s+thẻ(?:\s+đầy\s+đủ)?", re.IGNORECASE),
    # "gửi" needs special handling: "không gửi" / "không cần gửi" are safe
    # warnings; "gửi cho tôi" is a request. The fixed-width negative lookbehinds
    # exclude common negations that precede "gửi".
    re.compile(r"(?:vui\s+lòng\s+)?(?<!không\s)(?<!cần\s)(?<!đừng\s)gửi\s+(?:cho\s+(?:tôi|chúng\s+tôi)\s+)?(?:mã\s+)?(?:PIN|OTP)", re.IGNORECASE),
    re.compile(r"(?:vui\s+lòng\s+)?(?<!không\s)(?<!cần\s)(?<!đừng\s)gửi\s+(?:cho\s+(?:tôi|chúng\s+tôi)\s+)?mật\s+khẩu", re.IGNORECASE),
    # English equivalents
    re.compile(r"(?:please\s+)?(?:provide|send|enter)\s+(?:your\s+)?(?:PIN|OTP|password|card\s+number)", re.IGNORECASE),
]


# ─── Customer-safe field-name sanitization ──────────────────────
#
# Backend field names must never reach the customer (req: use Vietnamese
# labels, not transaction_id/bank_reference/...). This is a generic token→label
# rewrite, applied to every customer-facing string — not phrase matching.
_FIELD_LABELS: dict[str, str] = {
    "transaction_id": "mã giao dịch",
    "transaction_time": "thời gian giao dịch",
    "bank_reference": "mã tham chiếu ngân hàng",
    "bank_name": "ngân hàng",
    "order_id": "mã đơn hàng",
    "bill_code": "mã hóa đơn",
    "customer_code": "mã khách hàng",
    "payout_id": "mã thanh toán",
    "batch_id": "mã lô thanh toán",
    "merchant_id": "mã đối tác",
    "provider_ref_id": "mã tham chiếu nhà cung cấp",
    "user_id": "tài khoản",
    "wallet_id": "ví",
    "amount": "số tiền",
}

# Longest tokens first so e.g. "transaction_id" is replaced before "amount".
_FIELD_TOKEN_RE = re.compile(
    r"(?<![\w])(" + "|".join(
        re.escape(k) for k in sorted(_FIELD_LABELS, key=len, reverse=True)
    ) + r")(?![\w])",
    re.IGNORECASE,
)
# A parenthetical that wraps ONLY a field token, e.g. " (transaction_id)".
_FIELD_PAREN_RE = re.compile(
    r"\s*[\(\[]\s*(?:" + "|".join(re.escape(k) for k in _FIELD_LABELS) + r")\s*[\)\]]",
    re.IGNORECASE,
)


def sanitize_customer_text(text: str) -> str:
    """Replace any backend field name in customer-facing text with its label.

    Drops redundant parentheticals like "mã giao dịch (transaction_id)" → keeps
    only the label; replaces standalone tokens with their Vietnamese label.
    """
    if not text:
        return text
    # 1. Remove parentheticals that only restate a field name in English.
    out = _FIELD_PAREN_RE.sub("", text)
    # 2. Replace any remaining standalone English field tokens with labels.
    out = _FIELD_TOKEN_RE.sub(lambda m: _FIELD_LABELS[m.group(1).lower()], out)
    # 3. Tidy double spaces introduced by removals.
    return re.sub(r"[ \t]{2,}", " ", out).strip()


# ─── Evidence-grounding check (claim vs verified) ────────────────
#
# Confirmed-status wording may ONLY appear when the resolver actually verified
# a record for the logged-in account, and any amount stated in the same
# sentence must equal the VERIFIED amount — never the customer's claim.
_CONFIRMED_STATUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"ngân\s+hàng\s+đã\s+xác\s+nhận", re.IGNORECASE),
    re.compile(r"thanh\s+toán\s+đã\s+được\s+xác\s+nhận", re.IGNORECASE),
    re.compile(r"đã\s+xác\s+nhận\s+(?:giao\s+dịch|thanh\s+toán)", re.IGNORECASE),
    re.compile(r"giao\s+dịch\s+(?:đã\s+)?thành\s+công", re.IGNORECASE),
    re.compile(r"số\s+dư\s+(?:ví\s+)?(?:chưa|đang\s+chờ)\s+(?:được\s+)?cập\s+nhật", re.IGNORECASE),
    re.compile(r"đang\s+xử\s+lý\s+cập\s+nhật\s+ví", re.IGNORECASE),
    re.compile(r"ví\s+(?:của\s+bạn\s+)?đã\s+nhận\s+(?:được\s+)?tiền", re.IGNORECASE),
]

# VND amount parsing inside a sentence: "500.000đ", "500000 đồng", "5 triệu", "500k"
_VND_DIGIT_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d{4,})\s*(?:đ\b|₫|vnd\b|đồng\b)", re.IGNORECASE,
)
_VND_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(triệu|tr\b|nghìn|ngàn|k\b)", re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])\s+")

# Account-lock claims: wording that asserts the account IS locked / restricted /
# under security review. Only verified account data may back these (the
# customer saying "bị khóa" is a claim, not evidence). Negations ("không bị
# khóa", "chưa thấy ... khóa") are excluded by the lookbehinds/lookaheads.
_LOCK_CLAIM_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"tài\s+khoản\s+(?:của\s+bạn\s+)?(?:hiện\s+)?đang\s+(?:bị\s+)?(?:tạm\s+)?(?:khóa|hạn\s+chế)",
        re.IGNORECASE,
    ),
    re.compile(
        r"đang\s+được\s+bộ\s+phận\s+(?:bảo\s+mật|an\s+ninh)\s+(?:xác\s+minh|xem\s+xét|kiểm\s+tra)",
        re.IGNORECASE,
    ),
    re.compile(
        r"bộ\s+phận\s+(?:bảo\s+mật|an\s+ninh)\s+đang\s+(?:kiểm\s+tra|xác\s+minh|xem\s+xét)",
        re.IGNORECASE,
    ),
]


def _extract_vnd_amounts(sentence: str) -> list[int]:
    """Parse VND amounts mentioned in a sentence into integers."""
    amounts: list[int] = []
    for m in _VND_DIGIT_RE.finditer(sentence):
        try:
            amounts.append(int(m.group(1).replace(".", "").replace(",", "")))
        except ValueError:
            continue
    for m in _VND_UNIT_RE.finditer(sentence):
        try:
            base = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        unit = m.group(2).lower()
        mult = 1_000_000 if unit in ("triệu", "tr") else 1_000
        amounts.append(int(base * mult))
    return amounts


def check_evidence_grounding(
    response_text: str,
    *,
    resolver_status: str,
    verified_entity_id: str | None,
    verified_amount: int | None,
    fallback_text: str = "",
    lock_evidence: bool | None = None,
) -> GuardrailResult:
    """Block confirmed-status wording that verified evidence does not support.

    Rules (data-driven — values come from the resolver, nothing hard-coded):
      - Confirmed wording requires a verified entity on the logged-in account
        (resolver_status resolved/amount_mismatch + verified_entity_id).
      - Any amount in the SAME sentence as confirmed wording must equal the
        verified amount, never the customer's claimed amount.
      - Account-lock claims ("đang bị khóa", "đang được bộ phận bảo mật xác
        minh", …) require lock_evidence=True from the account record; when
        lock_evidence is None the lock check is skipped (non-account flows).
    """
    if not response_text:
        return GuardrailResult(is_safe=True)

    violations: list[str] = []
    sentences = _SENTENCE_SPLIT_RE.split(response_text)
    has_verified = (
        resolver_status in ("resolved", "amount_mismatch", "verified_match", "contradiction")
        and bool(verified_entity_id)
    )

    # Account-lock claims require verified lock evidence, not the customer's claim.
    if lock_evidence is not True and lock_evidence is not None:
        for p in _LOCK_CLAIM_PATTERNS:
            m = p.search(response_text)
            if m:
                violations.append(f"unverified_lock_claim: {m.group(0)[:60]}")
                break

    for sent in sentences:
        if not any(p.search(sent) for p in _CONFIRMED_STATUS_PATTERNS):
            continue
        if not has_verified:
            violations.append(f"unverified_confirmation: {sent[:80]}")
            continue
        if verified_amount is not None:
            for amt in _extract_vnd_amounts(sent):
                if amt != int(verified_amount):
                    violations.append(
                        f"claim_amount_in_confirmed_statement: {amt} != verified {verified_amount}"
                    )

    if not violations:
        return GuardrailResult(is_safe=True)

    logger.warning(
        "[Guardrail] Evidence grounding blocked response (%d violations): %s",
        len(violations), violations[:3],
    )
    return GuardrailResult(
        is_safe=False, violations=violations, sanitized_text=fallback_text or None,
    )


def _terms_to_patterns(terms: list[str]) -> list[re.Pattern]:
    """Compile a list of forbidden terms into word-boundary regex patterns."""
    patterns: list[re.Pattern] = []
    seen: set[str] = set()
    for term in terms:
        if not term or term in seen:
            continue
        seen.add(term)
        escaped = re.escape(term).replace(r"\ ", r"[\s_\-]?")
        try:
            patterns.append(re.compile(rf"\b{escaped}\b", re.IGNORECASE))
        except re.error:
            logger.warning("[Guardrail] Invalid forbidden term pattern: %s", term)
    return patterns


def _build_policy_patterns(
    policy: dict | None,
    workflow: str | None = None,
) -> list[re.Pattern]:
    """Build regex patterns from policy forbidden_terms.

    Always includes global_forbidden_terms. Workflow-specific terms are scoped:
    when a `workflow` is given, ONLY that workflow's forbidden_terms are added
    (so e.g. "số dư ví", forbidden for train_ticket, does not block a legitimate
    wallet_topup reply). When no workflow is given, only global terms apply.
    """
    if not policy:
        return []

    terms: list[str] = list(policy.get("global_forbidden_terms", []))

    if workflow:
        wf_config = policy.get("workflows", {}).get(workflow, {})
        terms.extend(wf_config.get("forbidden_terms", []))

    return _terms_to_patterns(terms)


# Wallet-balance wording that must not appear in non-wallet workflows
# unless the diagnosis is explicitly about a wallet balance issue.
_WALLET_WORDING_PATTERNS: list[re.Pattern] = [
    re.compile(r"kiểm\s+tra\s+lại\s+số\s+dư", re.IGNORECASE),
    re.compile(r"ví\s+(?:chưa\s+)?cập\s+nhật\s+số\s+dư", re.IGNORECASE),
    re.compile(r"cập\s+nhật\s+(?:vào\s+)?ví", re.IGNORECASE),
    re.compile(r"số\s+dư\s+ví", re.IGNORECASE),
]

# Topup-transaction wording that must not appear in non-wallet workflows
# (e.g. fraud_account_lock, train_ticket, merchant_settlement_delay)
# unless the diagnosis explicitly concerns a wallet topup issue.
_TOPUP_WORDING_PATTERNS: list[re.Pattern] = [
    re.compile(r"ngân\s+hàng\s+(?:đã\s+)?xác\s+nhận\s+giao\s+dịch", re.IGNORECASE),
    re.compile(r"giao\s+dịch\s+nạp\s+(?:ví|tiền)", re.IGNORECASE),
    re.compile(r"ví\s+chưa\s+nhận\s+(?:được\s+)?tiền", re.IGNORECASE),
    re.compile(r"nạp\s+tiền\s+(?:vào\s+)?ví", re.IGNORECASE),
    re.compile(r"(?:kiểm\s+tra|xác\s+minh)\s+(?:giao\s+dịch\s+)?nạp\s+(?:tiền|ví)", re.IGNORECASE),
    re.compile(r"giao\s+dịch\s+topup", re.IGNORECASE),
]


def _diagnosis_is_wallet_related(diagnosis: dict | None) -> bool:
    """True if the diagnosis explicitly concerns a wallet balance/credit issue."""
    if not diagnosis:
        return False
    if str(diagnosis.get("workflow", "")).lower() == "wallet_topup":
        return True
    blob = " ".join(
        str(diagnosis.get(k, "") or "")
        for k in ("likely_issue_location", "customer_safe_cause", "situation")
    ).lower()
    return "ví" in blob or "số dư" in blob


# Train-ticket wording that must not appear in non-train workflows
# unless the diagnosis is explicitly about a train-ticket issue.
_TRAIN_WORDING_PATTERNS: list[re.Pattern] = [
    re.compile(r"vé\s+(?:chưa\s+)?(?:được\s+)?phát\s+hành", re.IGNORECASE),
    re.compile(r"nhà\s+cung\s+cấp\s+vé", re.IGNORECASE),
    re.compile(r"đối\s+soát\s+vé", re.IGNORECASE),
    re.compile(r"mã\s+vé\b", re.IGNORECASE),
    re.compile(r"ticket_code", re.IGNORECASE),
    re.compile(r"provider.{0,10}train", re.IGNORECASE),
    re.compile(r"chưa\s+nhận\s+(?:được\s+)?vé", re.IGNORECASE),
    re.compile(r"vé\s+tàu", re.IGNORECASE),
]


def _diagnosis_is_train_related(diagnosis: dict | None) -> bool:
    """True if the diagnosis explicitly concerns a train-ticket issue."""
    if not diagnosis:
        return False
    if str(diagnosis.get("workflow", "")).lower() == "train_ticket":
        return True
    blob = " ".join(
        str(diagnosis.get(k, "") or "")
        for k in ("likely_issue_location", "customer_safe_cause", "situation")
    ).lower()
    return "vé" in blob or "tàu" in blob or "ticket" in blob


def check_response_safety(
    response_text: str,
    policy: dict | None = None,
    workflow: str | None = None,
    diagnosis: dict | None = None,
) -> GuardrailResult:
    """Check a customer-facing response for forbidden content.

    Args:
        response_text: The LLM-composed response text.
        policy: The loaded customer_response_policy dict.
        workflow: Active workflow — scopes workflow-specific forbidden terms and
            enables wallet-wording rejection for non-wallet workflows.
        diagnosis: The public-safe diagnosis — used to allow wallet wording only
            when the issue is genuinely wallet-balance related.

    Returns:
        GuardrailResult with is_safe flag and any violations found.
    """
    if not response_text or not response_text.strip():
        return GuardrailResult(is_safe=True)

    violations: list[str] = []

    # 1. Check hardcoded blocked patterns (always enforced)
    for pattern in _HARDCODED_BLOCKED_PATTERNS:
        if pattern.search(response_text):
            violations.append(f"blocked_term: {pattern.pattern}")

    # 2. Check sensitive info requests
    for pattern in _SENSITIVE_REQUEST_PATTERNS:
        if pattern.search(response_text):
            violations.append(f"sensitive_request: {pattern.pattern}")

    # 3. Check policy-driven forbidden terms (scoped to active workflow)
    policy_patterns = _build_policy_patterns(policy, workflow)
    for pattern in policy_patterns:
        if pattern.search(response_text):
            violations.append(f"policy_forbidden: {pattern.pattern}")

    # 4. Workflow-aware wallet-wording rejection.
    #    For any non-wallet workflow, wallet-balance wording is rejected unless
    #    the diagnosis explicitly says the issue is wallet related.
    wf = (workflow or "").lower()
    if wf and wf != "wallet_topup" and not _diagnosis_is_wallet_related(diagnosis):
        for pattern in _WALLET_WORDING_PATTERNS:
            if pattern.search(response_text):
                violations.append(f"cross_workflow_wallet_wording: {pattern.pattern}")

    # 4b. Topup-transaction wording rejection for non-topup workflows.
    #     "ngân hàng đã xác nhận giao dịch", "giao dịch nạp ví", etc.
    #     must NOT appear in fraud_account_lock, train_ticket, etc.
    if wf and wf not in ("wallet_topup", "") and not _diagnosis_is_wallet_related(diagnosis):
        for pattern in _TOPUP_WORDING_PATTERNS:
            if pattern.search(response_text):
                violations.append(f"cross_workflow_topup_wording: {pattern.pattern}")

    # 4c. Train-ticket wording rejection for non-train workflows.
    #     "vé chưa được phát hành", "nhà cung cấp vé", "đối soát vé", etc.
    #     must NOT appear in wallet_topup, fraud_account_lock, etc.
    if wf and wf != "train_ticket" and not _diagnosis_is_train_related(diagnosis):
        for pattern in _TRAIN_WORDING_PATTERNS:
            if pattern.search(response_text):
                violations.append(f"cross_workflow_train_wording: {pattern.pattern}")

    if not violations:
        return GuardrailResult(is_safe=True)

    # Build safe fallback
    logger.warning(
        "[Guardrail] %d violations in response: %s",
        len(violations), violations[:3],
    )

    # Use policy fallback or generic
    fallback = (
        policy.get("generic_fallback_response", "") if policy
        else ""
    )
    if not fallback:
        fallback = (
            "Chúng tôi đã ghi nhận thông tin của bạn. "
            "Bộ phận hỗ trợ sẽ kiểm tra và phản hồi trong thời gian sớm nhất."
        )

    safety_reminder = (
        policy.get("global_safety_reminder", "") if policy
        else "Vui lòng không gửi mã PIN, OTP hoặc mật khẩu."
    )

    sanitized = f"{fallback}\n\n{safety_reminder}".strip()

    return GuardrailResult(
        is_safe=False,
        violations=violations,
        sanitized_text=sanitized,
    )

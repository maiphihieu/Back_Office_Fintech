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

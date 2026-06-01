"""Generic claim verifier — registry-based verification of customer claims.

Business principle:
    Customer complaint text is ONLY an investigation input, NOT the source of truth.
    The agent MUST use system evidence to verify the complaint.

Design:
    - A VerifierFn takes (Claim, EvidenceBundle) → Claim (with verification filled in).
    - _VERIFIER_REGISTRY maps ClaimType → VerifierFn.
    - verify_all_claims() loops over claims generically — no hard-coded if/else.
    - Adding a new claim type = add an enum value + a verifier function + register it.

Two distinct outcomes:
    1. customer_detail_mismatch: customer wrong, system consistent → continue
    2. system_evidence_conflict: system sources disagree → block risky actions
"""

from __future__ import annotations

from collections.abc import Callable

from fintech_agent.schemas.claim_verification import (
    Claim,
    ClaimType,
    ClaimVerificationSummary,
    VerificationStatus,
)
from fintech_agent.schemas.evidence import EvidenceBundle


# ─── Type alias ──────────────────────────────────────────────

VerifierFn = Callable[[Claim, EvidenceBundle], Claim]


# ─── Staff-friendly labels ───────────────────────────────────

CLAIM_TYPE_LABELS: dict[str, str] = {
    ClaimType.TRANSACTION_ID: "Mã giao dịch",
    ClaimType.USER_IDENTITY: "Thông tin người dùng",
    ClaimType.TRANSACTION_AMOUNT: "Số tiền giao dịch",
    ClaimType.WALLET_BALANCE: "Số dư ví khách phản ánh",
    ClaimType.PAYMENT_STATUS: "Trạng thái thanh toán khách phản ánh",
    ClaimType.SERVICE_DELIVERY: "Tình trạng dịch vụ khách phản ánh",
    ClaimType.PROVIDER_STATUS: "Trạng thái provider",
    ClaimType.BANK_STATUS: "Trạng thái ngân hàng",
    ClaimType.REFUND_STATUS: "Trạng thái hoàn tiền",
    ClaimType.ACCOUNT_STATUS: "Trạng thái tài khoản",
    ClaimType.WITHDRAWAL_STATUS: "Trạng thái rút tiền",
    ClaimType.CUSTOMER_OPINION: "Ý kiến khách hàng",
    ClaimType.TIME: "Thời gian khách phản ánh",
    ClaimType.UNKNOWN: "Thông tin khác",
}


# ─── Verifier Functions ─────────────────────────────────────
# Each takes a Claim + EvidenceBundle and returns the claim with
# verification_status, trusted_system_value, trusted_source, explanation.


def _verify_transaction_id_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify claimed transaction_id against system transaction."""
    if evidence.transaction is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_FOUND,
            "explanation": "Không tìm thấy dữ liệu giao dịch trong hệ thống để đối chiếu.",
        })

    system_txn_id = evidence.transaction.transaction_id
    claimed = str(claim.customer_claimed_value or "")

    if claimed == system_txn_id:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": system_txn_id,
            "trusted_source": "transaction.transaction_id",
            "explanation": f"Mã giao dịch khớp: {system_txn_id}",
        })
    else:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MISMATCHED,
            "trusted_system_value": system_txn_id,
            "trusted_source": "transaction.transaction_id",
            "explanation": (
                f"Khách khai mã giao dịch '{claimed}' "
                f"nhưng hệ thống ghi nhận '{system_txn_id}'."
            ),
        })


def _get_trusted_amount(evidence: EvidenceBundle) -> tuple[int | None, str | None]:
    """Get the trusted amount from system evidence (priority order).

    Priority:
        1. wallet_ledger.debit_amount
        2. transaction.amount
        3. reconciliation.bank_amount
    """
    if evidence.wallet_ledger and evidence.wallet_ledger.debit_amount:
        return evidence.wallet_ledger.debit_amount, "wallet_ledger.debit_amount"
    if evidence.transaction and evidence.transaction.amount:
        return evidence.transaction.amount, "transaction.amount"
    if evidence.reconciliation_status and evidence.reconciliation_status.bank_amount:
        return evidence.reconciliation_status.bank_amount, "reconciliation.bank_amount"
    return None, None


def _verify_transaction_amount_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify claimed transaction amount against system evidence."""
    trusted_amount, trusted_source = _get_trusted_amount(evidence)

    if trusted_amount is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": "Không tìm thấy số tiền trong hệ thống để đối chiếu.",
        })

    claimed = claim.normalized_value
    if claimed is not None and int(claimed) == trusted_amount:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": trusted_amount,
            "trusted_source": trusted_source,
            "explanation": f"Số tiền khớp: {trusted_amount:,}đ (từ {trusted_source}).",
        })
    else:
        claimed_display = f"{int(claimed):,}đ" if claimed is not None else "không rõ"
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MISMATCHED,
            "trusted_system_value": trusted_amount,
            "trusted_source": trusted_source,
            "explanation": (
                f"Khách khai {claimed_display} nhưng hệ thống "
                f"ghi nhận {trusted_amount:,}đ (từ {trusted_source})."
            ),
        })


def _verify_wallet_balance_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify claimed wallet balance against system evidence.

    Wallet balance is NOT the same as transaction amount.
    This claim represents what the customer SEES on their wallet display.
    For topup-pending cases, if balance shows 0, the system checks whether
    a credit entry exists in the ledger.

    System sources:
      - wallet_ledger.net_amount (closest proxy for current balance)
      - transaction.status (whether pending → wallet not yet credited)
    """
    claimed = claim.normalized_value
    claimed_display = f"{int(claimed):,}đ" if claimed is not None else "không rõ"

    # Check if transaction is pending (topup not yet credited)
    txn_pending = (
        evidence.transaction is not None
        and evidence.transaction.status == "pending"
    )

    if evidence.wallet_ledger is None:
        # No wallet ledger — wallet may not have been credited
        if txn_pending:
            return claim.model_copy(update={
                "verification_status": VerificationStatus.MATCHED,
                "trusted_system_value": "wallet_not_credited",
                "trusted_source": "transaction.status + wallet_ledger",
                "explanation": (
                    f"Khách phản ánh ví hiển thị {claimed_display}. "
                    f"Giao dịch đang pending, chưa có ledger credit cộng tiền cho ví. "
                    f"Đây là số dư ví khách nhìn thấy, không phải số tiền giao dịch."
                ),
            })
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": (
                f"Khách phản ánh ví hiển thị {claimed_display}. "
                f"Không tìm thấy dữ liệu ví để đối chiếu. "
                f"Đây là số dư ví khách nhìn thấy, không phải số tiền giao dịch."
            ),
        })

    # Use net_amount as the closest proxy for wallet balance after transaction
    system_balance = evidence.wallet_ledger.net_amount
    trusted_source = "wallet_ledger.net_amount"

    if claimed is not None and int(claimed) == system_balance:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": system_balance,
            "trusted_source": trusted_source,
            "explanation": (
                f"Số dư ví khách phản ánh khớp: {system_balance:,}đ (từ {trusted_source}). "
                f"Đây là số dư ví khách nhìn thấy, không phải số tiền giao dịch."
            ),
        })
    else:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MISMATCHED,
            "trusted_system_value": system_balance,
            "trusted_source": trusted_source,
            "explanation": (
                f"Khách phản ánh ví hiển thị {claimed_display} — "
                f"hệ thống ghi nhận net_amount = {system_balance:,}đ (từ {trusted_source}). "
                f"Đây là số dư ví, không phải số tiền giao dịch."
            ),
        })


def _verify_payment_status_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify payment status claim (e.g. 'bank deducted money')."""
    if evidence.wallet_ledger is None and evidence.transaction is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": "Không có dữ liệu giao dịch hoặc ví để xác minh trạng thái thanh toán.",
        })

    # Check wallet_ledger status first (source of truth for money)
    if evidence.wallet_ledger:
        wallet_status = str(evidence.wallet_ledger.status)
        has_debit = evidence.wallet_ledger.has_user_debit
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": wallet_status,
            "trusted_source": "wallet_ledger.status",
            "explanation": (
                f"Wallet ledger status: {wallet_status}, "
                f"has_user_debit: {has_debit}."
            ),
        })

    # Fallback to transaction status
    if evidence.transaction:
        txn_status = evidence.transaction.status
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": txn_status,
            "trusted_source": "transaction.status",
            "explanation": f"Transaction status: {txn_status}.",
        })

    return claim  # pragma: no cover


def _verify_service_delivery_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify service delivery claim (e.g. 'ticket not received')."""
    if evidence.train_provider is not None:
        status = str(evidence.train_provider.booking_status)
        ticket_code = evidence.train_provider.ticket_code
        explanation = f"Provider xác nhận: {status}"
        if ticket_code:
            explanation += f", mã vé: {ticket_code}"
        else:
            explanation += ", chưa có mã vé"
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": status,
            "trusted_source": "train_provider.booking_status",
            "explanation": explanation,
        })

    if evidence.utility_provider is not None:
        status = str(evidence.utility_provider.provider_status)
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": status,
            "trusted_source": "utility_provider.provider_status",
            "explanation": f"Provider xác nhận trạng thái: {status}.",
        })

    return claim.model_copy(update={
        "verification_status": VerificationStatus.NOT_VERIFIABLE,
        "explanation": "Không có dữ liệu provider để xác minh tình trạng dịch vụ.",
    })


def _verify_provider_status_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify provider-side status."""
    # Reuse service delivery logic — same evidence sources
    return _verify_service_delivery_claim(claim, evidence)


def _verify_bank_status_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify bank-side status from reconciliation data."""
    if evidence.reconciliation_status is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": "Không có dữ liệu đối soát ngân hàng.",
        })

    bank_status = evidence.reconciliation_status.bank_status or "unknown"
    return claim.model_copy(update={
        "verification_status": VerificationStatus.MATCHED,
        "trusted_system_value": bank_status,
        "trusted_source": "reconciliation.bank_status",
        "explanation": f"Bank status từ đối soát: {bank_status}.",
    })


def _verify_refund_status_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify refund status from refund_status evidence."""
    if evidence.refund_status is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": "Không có dữ liệu hoàn tiền trong hệ thống.",
        })

    system_refund = str(evidence.refund_status.refund_status)
    return claim.model_copy(update={
        "verification_status": VerificationStatus.MATCHED,
        "trusted_system_value": system_refund,
        "trusted_source": "refund_status.refund_status",
        "explanation": f"Trạng thái hoàn tiền hệ thống: {system_refund}.",
    })


def _verify_account_status_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify account status from account_status evidence."""
    if evidence.account_status is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": "Không có dữ liệu trạng thái tài khoản.",
        })

    system_status = evidence.account_status.account_status or "unknown"
    lock_reason = evidence.account_status.lock_reason or ""
    explanation = f"Trạng thái tài khoản: {system_status}"
    if lock_reason:
        explanation += f", lý do: {lock_reason}"

    return claim.model_copy(update={
        "verification_status": VerificationStatus.MATCHED,
        "trusted_system_value": system_status,
        "trusted_source": "account_status.account_status",
        "explanation": explanation + ".",
    })


def _verify_user_identity_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify user identity against transaction or account data."""
    claimed = str(claim.customer_claimed_value or "")
    phone = claim.raw_text  # may contain phone used for lookup

    # For fraud workflow, check account_status for identity
    if evidence.account_status is not None:
        system_user = evidence.account_status.user_id
        if claimed == system_user or (phone and claimed == phone):
            return claim.model_copy(update={
                "verification_status": VerificationStatus.MATCHED,
                "trusted_system_value": system_user,
                "trusted_source": "accounts.user_id (via phone lookup)",
                "explanation": (
                    f"Đã xác minh: {phone} → user_id = {system_user} (từ accounts)."
                ),
            })

    if evidence.transaction is not None:
        system_user = evidence.transaction.user_id
        if claimed == system_user:
            return claim.model_copy(update={
                "verification_status": VerificationStatus.MATCHED,
                "trusted_system_value": system_user,
                "trusted_source": "transaction.user_id",
                "explanation": f"User ID khớp: {system_user}.",
            })
        else:
            return claim.model_copy(update={
                "verification_status": VerificationStatus.MISMATCHED,
                "trusted_system_value": system_user,
                "trusted_source": "transaction.user_id",
                "explanation": (
                    f"Khách khai user '{claimed}' nhưng "
                    f"hệ thống ghi nhận '{system_user}'."
                ),
            })

    # Phone/email/wallet_id was provided but NO account found → NOT_FOUND
    if claimed:
        id_type = claim.unit or "phone"
        id_label = {
            "phone": "số điện thoại",
            "email": "email",
            "wallet_id": "wallet ID",
        }.get(id_type, "thông tin định danh")
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_FOUND,
            "trusted_system_value": "Không tìm thấy tài khoản",
            "trusted_source": f"accounts.{id_type}",
            "explanation": (
                f"Không tìm thấy tài khoản khớp với {id_label} "
                f"khách cung cấp ({claimed}). "
                "Agent chưa thể kiểm tra trạng thái khóa "
                "hoặc dữ liệu Risk/Fraud."
            ),
        })

    return claim.model_copy(update={
        "verification_status": VerificationStatus.NOT_VERIFIABLE,
        "explanation": "Không có dữ liệu để xác minh thông tin người dùng.",
    })


def _verify_withdrawal_status_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Verify withdrawal status from account_status evidence."""
    if evidence.account_status is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": "Không có dữ liệu tài khoản để xác minh trạng thái rút tiền.",
        })

    withdrawal = evidence.account_status.withdrawal_enabled
    if withdrawal is None:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.NOT_VERIFIABLE,
            "explanation": "Dữ liệu tài khoản không chứa trạng thái rút tiền.",
        })

    status_text = "cho phép" if withdrawal else "bị chặn"
    claimed = str(claim.customer_claimed_value or "").lower()
    # Customer says they can't withdraw → system says blocked → MATCHED
    customer_says_blocked = any(kw in claimed for kw in (
        "không thể", "blocked", "chặn", "bị chặn",
    ))

    if (customer_says_blocked and not withdrawal) or (
        not customer_says_blocked and withdrawal
    ):
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MATCHED,
            "trusted_system_value": status_text,
            "trusted_source": "accounts.withdrawal_enabled",
            "explanation": (
                f"Khách phản ánh không thể rút tiền — hệ thống xác nhận: "
                f"withdrawal_enabled = {withdrawal} ({status_text})."
            ),
        })
    else:
        return claim.model_copy(update={
            "verification_status": VerificationStatus.MISMATCHED,
            "trusted_system_value": status_text,
            "trusted_source": "accounts.withdrawal_enabled",
            "explanation": (
                f"Trạng thái rút tiền từ hệ thống: {status_text} "
                f"(withdrawal_enabled = {withdrawal})."
            ),
        })


def _verify_customer_opinion_claim(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Handle customer opinion claims (e.g. 'vô cớ').

    Customer opinions are NOT directly verifiable against system data.
    They are recorded and flagged for staff review.
    """
    return claim.model_copy(update={
        "verification_status": VerificationStatus.NOT_VERIFIABLE,
        "trusted_system_value": None,
        "trusted_source": None,
        "explanation": (
            "Đây là nhận định của khách hàng. Agent không coi đây là sự thật "
            "cuối cùng; cần kiểm tra fraud evidence để đánh giá."
        ),
    })


def _default_verifier(claim: Claim, evidence: EvidenceBundle) -> Claim:
    """Default verifier for unknown/unregistered claim types."""
    return claim.model_copy(update={
        "verification_status": VerificationStatus.NOT_VERIFIABLE,
        "explanation": f"Không có quy trình xác minh cho loại claim '{claim.claim_type}'.",
    })


# ─── Verifier Registry ──────────────────────────────────────
# Adding a new claim type = add enum + function + register here.

_VERIFIER_REGISTRY: dict[ClaimType, VerifierFn] = {
    ClaimType.TRANSACTION_ID: _verify_transaction_id_claim,
    ClaimType.TRANSACTION_AMOUNT: _verify_transaction_amount_claim,
    ClaimType.WALLET_BALANCE: _verify_wallet_balance_claim,
    ClaimType.PAYMENT_STATUS: _verify_payment_status_claim,
    ClaimType.SERVICE_DELIVERY: _verify_service_delivery_claim,
    ClaimType.PROVIDER_STATUS: _verify_provider_status_claim,
    ClaimType.BANK_STATUS: _verify_bank_status_claim,
    ClaimType.REFUND_STATUS: _verify_refund_status_claim,
    ClaimType.ACCOUNT_STATUS: _verify_account_status_claim,
    ClaimType.WITHDRAWAL_STATUS: _verify_withdrawal_status_claim,
    ClaimType.CUSTOMER_OPINION: _verify_customer_opinion_claim,
    ClaimType.USER_IDENTITY: _verify_user_identity_claim,
}


# ─── System consistency check ────────────────────────────────


def _check_system_amount_consistency(evidence: EvidenceBundle) -> bool:
    """Check if system amount sources are internally consistent.

    Returns True if system sources AGREE on the amount.
    Returns False if there's a conflict between system sources.
    """
    amounts: list[tuple[str, int]] = []

    if evidence.wallet_ledger and evidence.wallet_ledger.debit_amount:
        amounts.append(("wallet_ledger.debit_amount", evidence.wallet_ledger.debit_amount))

    if evidence.transaction and evidence.transaction.amount:
        amounts.append(("transaction.amount", evidence.transaction.amount))

    if evidence.reconciliation_status and evidence.reconciliation_status.bank_amount:
        amounts.append(("reconciliation.bank_amount", evidence.reconciliation_status.bank_amount))

    if len(amounts) <= 1:
        return True  # Can't conflict with only one source

    first_amount = amounts[0][1]
    return all(amt == first_amount for _, amt in amounts)


# ─── Summary builders ────────────────────────────────────────


def _build_staff_explanation(
    claims: list[Claim],
    matched: list[str],
    mismatched: list[str],
    not_verifiable: list[str],
    not_found: list[str],
    has_customer_mismatch: bool,
    has_system_conflict: bool,
) -> str:
    """Build a Vietnamese staff-facing explanation paragraph."""
    parts: list[str] = []

    if matched:
        labels = [CLAIM_TYPE_LABELS.get(ct, ct) for ct in matched]
        parts.append(f"Khách khai đúng về: {', '.join(labels)}.")

    if mismatched:
        for claim in claims:
            if claim.verification_status == VerificationStatus.MISMATCHED:
                parts.append(claim.explanation)

    if not_verifiable:
        labels = [CLAIM_TYPE_LABELS.get(ct, ct) for ct in not_verifiable]
        parts.append(f"Không xác minh được: {', '.join(labels)}.")

    if not_found:
        labels = [CLAIM_TYPE_LABELS.get(ct, ct) for ct in not_found]
        parts.append(f"Không tìm thấy trong hệ thống: {', '.join(labels)}.")

    if has_customer_mismatch and not has_system_conflict:
        parts.append(
            "Vì dữ liệu hệ thống nhất quán, agent sẽ tiếp tục xử lý "
            "theo dữ liệu chuẩn. Không dùng thông tin khách khai sai để tạo action."
        )

    if has_system_conflict:
        parts.append(
            "Các nguồn dữ liệu hệ thống đang mâu thuẫn. "
            "Cần kiểm tra thủ công trước khi tạo action rủi ro."
        )

    return " ".join(parts)


def _build_summary_text(
    matched: list[str],
    mismatched: list[str],
    not_verifiable: list[str],
    not_found: list[str],
    system_only: list[str] | None = None,
) -> str:
    """Build a short summary string."""
    _system_only = system_only or []
    total = len(matched) + len(mismatched) + len(not_verifiable) + len(not_found) + len(_system_only)
    parts = []
    if matched:
        parts.append(f"{len(matched)} khớp")
    if mismatched:
        parts.append(f"{len(mismatched)} lệch")
    if _system_only:
        parts.append(f"{len(_system_only)} chỉ có dữ liệu hệ thống")
    if not_verifiable:
        parts.append(f"{len(not_verifiable)} không xác minh được")
    if not_found:
        parts.append(f"{len(not_found)} không tìm thấy")
    return f"Kiểm tra {total} thông tin: {', '.join(parts)}."


# ─── Backward compatibility: convert ExtractedInfo → Claim[] ──


def _claims_from_extracted_info(
    extracted_info: dict | object | None,
) -> list[Claim]:
    """Convert legacy ExtractedInfo dict to list[Claim].

    This adapter ensures old callers that pass ExtractedInfo (without claims)
    still work. If ExtractedInfo already has .claims populated, use those.
    Otherwise, synthesize claims from the flat fields.
    """
    if extracted_info is None:
        return []

    # Normalize to dict
    if hasattr(extracted_info, "model_dump"):
        ei = extracted_info.model_dump(mode="json", exclude_none=True)
    elif isinstance(extracted_info, dict):
        ei = extracted_info
    else:
        return []

    # If claims are already populated, use them directly
    existing_claims = ei.get("claims", [])
    if existing_claims:
        # They might be dicts (from model_dump) — convert to Claim
        claims = []
        for c in existing_claims:
            if isinstance(c, Claim):
                claims.append(c)
            elif isinstance(c, dict):
                claims.append(Claim.model_validate(c))
        return claims

    # ── Synthesize from flat fields ──────────────────────────
    claims: list[Claim] = []

    txn_id = ei.get("transaction_id")
    if txn_id:
        claims.append(Claim(
            claim_type=ClaimType.TRANSACTION_ID,
            raw_text=str(txn_id),
            customer_claimed_value=txn_id,
            normalized_value=txn_id,
            unit="identifier",
            confidence=1.0,
        ))

    amount = ei.get("amount_claimed")
    if amount is not None:
        claims.append(Claim(
            claim_type=ClaimType.TRANSACTION_AMOUNT,
            raw_text=str(amount),
            customer_claimed_value=amount,
            normalized_value=amount,
            unit="VND",
            confidence=0.8,
        ))

    svc = ei.get("service_type")
    if svc:
        claims.append(Claim(
            claim_type=ClaimType.SERVICE_DELIVERY,
            raw_text=str(svc),
            customer_claimed_value=str(svc),
            normalized_value=str(svc),
            unit="service",
            confidence=0.8,
        ))

    # ── Fraud/account-security-specific claims ──────────────
    if svc == "account_security":
        # A. Account locked claim
        issue = ei.get("issue_type")
        if issue == "account_locked":
            claims.append(Claim(
                claim_type=ClaimType.ACCOUNT_STATUS,
                raw_text="tài khoản bị khóa",
                customer_claimed_value="locked",
                normalized_value="locked",
                unit="status",
                confidence=1.0,
            ))

        # B. Withdrawal blocked claim
        raw = ei.get("_raw_complaint", "")
        if isinstance(raw, str) and any(kw in raw.lower() for kw in (
            "không thể rút", "rút tiền", "withdrawal",
        )):
            claims.append(Claim(
                claim_type=ClaimType.WITHDRAWAL_STATUS,
                raw_text="không thể rút tiền",
                customer_claimed_value="không thể rút tiền",
                normalized_value="blocked",
                unit="status",
                confidence=1.0,
            ))

        # C. "Vô cớ" / customer opinion claim
        if isinstance(raw, str) and any(kw in raw.lower() for kw in (
            "vô cớ", "vô lý", "bất ngờ", "nhầm", "sai",
        )):
            opinion_text = "vô cớ"
            for kw in ("vô cớ", "vô lý", "bất ngờ", "nhầm", "sai"):
                if kw in raw.lower():
                    opinion_text = kw
                    break
            claims.append(Claim(
                claim_type=ClaimType.CUSTOMER_OPINION,
                raw_text=opinion_text,
                customer_claimed_value=opinion_text,
                normalized_value=opinion_text,
                unit="opinion",
                confidence=0.9,
            ))

        # D. Phone / identity claim
        phone = ei.get("phone")
        if phone:
            claims.append(Claim(
                claim_type=ClaimType.USER_IDENTITY,
                raw_text=str(phone),
                customer_claimed_value=phone,
                normalized_value=phone,
                unit="phone",
                confidence=1.0,
            ))

    return claims


# ─── Main entry point ────────────────────────────────────────


def verify_all_claims(
    extracted_info: dict | object | None,
    evidence: EvidenceBundle,
) -> ClaimVerificationSummary:
    """Verify all customer claims against system evidence.

    Generic loop — no hard-coded if/else per claim type.
    Uses the _VERIFIER_REGISTRY to dispatch each claim to its verifier.

    Args:
        extracted_info: Customer-provided claims from LLM extraction.
            Can be a Pydantic model, dict, or list of Claim objects.
            If ExtractedInfo has .claims populated, those are used directly.
            Otherwise, claims are synthesized from flat fields (backward compat).
        evidence: Trusted system evidence bundle.

    Returns:
        ClaimVerificationSummary with per-claim verification results
        and aggregate flags for customer_detail_mismatch vs system_evidence_conflict.
    """
    # Get claims from extracted_info
    claims = _claims_from_extracted_info(extracted_info)

    # Verify each claim using the registry
    verified: list[Claim] = []
    for claim in claims:
        verifier = _VERIFIER_REGISTRY.get(claim.claim_type, _default_verifier)
        verified.append(verifier(claim, evidence))

    # ── Inject SYSTEM_ONLY claims for data the customer did NOT provide ──
    # If customer didn't claim a transaction amount but system has one,
    # add a system_only claim so staff sees the system amount clearly.
    existing_types = {c.claim_type for c in verified}
    if ClaimType.TRANSACTION_AMOUNT not in existing_types:
        trusted_amount, trusted_source = _get_trusted_amount(evidence)
        if trusted_amount is not None:
            verified.append(Claim(
                claim_type=ClaimType.TRANSACTION_AMOUNT,
                raw_text="",
                customer_claimed_value=None,
                normalized_value=None,
                unit="VND",
                confidence=1.0,
                verification_status=VerificationStatus.SYSTEM_ONLY,
                trusted_system_value=trusted_amount,
                trusted_source=trusted_source,
                explanation=(
                    f"Khách không cung cấp số tiền giao dịch. "
                    f"Hệ thống ghi nhận {trusted_amount:,}đ (từ {trusted_source})."
                ),
            ))

    # Categorize results
    matched = [
        str(c.claim_type)
        for c in verified
        if c.verification_status == VerificationStatus.MATCHED
    ]
    mismatched = [
        str(c.claim_type)
        for c in verified
        if c.verification_status == VerificationStatus.MISMATCHED
    ]
    not_verifiable = [
        str(c.claim_type)
        for c in verified
        if c.verification_status == VerificationStatus.NOT_VERIFIABLE
    ]
    not_found = [
        str(c.claim_type)
        for c in verified
        if c.verification_status == VerificationStatus.NOT_FOUND
    ]
    system_only = [
        str(c.claim_type)
        for c in verified
        if c.verification_status == VerificationStatus.SYSTEM_ONLY
    ]

    # Determine flags
    has_customer_mismatch = len(mismatched) > 0
    has_system_conflict = not _check_system_amount_consistency(evidence)

    # Build explanations
    summary_text = _build_summary_text(matched, mismatched, not_verifiable, not_found, system_only)
    staff_explanation = _build_staff_explanation(
        verified, matched, mismatched, not_verifiable, not_found,
        has_customer_mismatch, has_system_conflict,
    )

    # Build trusted_data_used_for_action — NEVER from customer complaint
    trusted_data = _build_trusted_data_for_action(evidence)

    return ClaimVerificationSummary(
        summary=summary_text,
        claims=verified,
        matched_claims=matched,
        mismatched_claims=mismatched,
        not_verifiable_claims=not_verifiable,
        not_found_claims=not_found,
        has_customer_detail_mismatch=has_customer_mismatch,
        has_system_evidence_conflict=has_system_conflict,
        staff_explanation=staff_explanation,
        trusted_data_used_for_action=trusted_data,
    )


def _build_trusted_data_for_action(evidence: EvidenceBundle) -> dict[str, str | int | float | None]:
    """Build the trusted data dict for action inputs.

    Priority for amounts:
        1. wallet_ledger.debit_amount
        2. transaction.amount
        3. reconciliation.bank_amount

    SAFETY: These values come ONLY from system evidence, NEVER from
    customer complaint text.
    """
    data: dict[str, str | int | float | None] = {}

    # Action amount
    amount, source = _get_trusted_amount(evidence)
    if amount is not None:
        data["action_amount"] = amount
        data["action_amount_source"] = source

    # Transaction ID
    if evidence.transaction:
        data["transaction_id"] = evidence.transaction.transaction_id
        data["user_id"] = evidence.transaction.user_id
        data["service_type"] = evidence.transaction.service_type

    # Wallet status
    if evidence.wallet_ledger:
        data["wallet_status"] = str(evidence.wallet_ledger.status)
        data["has_user_debit"] = 1 if evidence.wallet_ledger.has_user_debit else 0
        data["has_credit_refund"] = 1 if evidence.wallet_ledger.has_credit_refund else 0

    # Reconciliation
    if evidence.reconciliation_status:
        if evidence.reconciliation_status.bank_amount is not None:
            data["bank_amount"] = evidence.reconciliation_status.bank_amount
        if evidence.reconciliation_status.bank_status:
            data["bank_status"] = evidence.reconciliation_status.bank_status

    # ── Account / Fraud evidence ──
    if evidence.account_status:
        data["user_id"] = evidence.account_status.user_id
        data["account_status"] = evidence.account_status.account_status or "unknown"
        if evidence.account_status.withdrawal_enabled is not None:
            data["withdrawal_enabled"] = 1 if evidence.account_status.withdrawal_enabled else 0
        if evidence.account_status.lock_reason:
            data["lock_reason"] = evidence.account_status.lock_reason

    if evidence.fraud_case:
        if evidence.fraud_case.risk_score is not None:
            data["risk_score"] = evidence.fraud_case.risk_score
        if evidence.fraud_case.risk_level:
            data["risk_level"] = evidence.fraud_case.risk_level
        if evidence.fraud_case.fraud_status:
            data["fraud_status"] = evidence.fraud_case.fraud_status
        if evidence.fraud_case.recommended_decision:
            data["recommended_decision"] = evidence.fraud_case.recommended_decision

    return data

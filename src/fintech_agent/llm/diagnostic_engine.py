"""Deterministic Diagnostic Engine — structured bottleneck analysis.

Maps (workflow, diagnosis, evidence_bundle) → DiagnosticResult with:
  - Bottleneck: where the issue is, with exact data-point evidence
  - Resolution: recommended action label + staff next steps
  - Missing data: fields needed but not available

SAFETY INVARIANTS:
  - This module is PURE DETERMINISTIC — no LLM, no I/O, no side effects.
  - It does NOT change recommended_action or approval_required.
  - It reads the rule engine's diagnosis string + evidence to produce
    human-readable structured explanations.
  - The Resolution.recommended_action is a DISPLAY LABEL only —
    the actual ActionType comes from the rule engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── Data Structures ────────────────────────────────────────────


@dataclass(frozen=True)
class Bottleneck:
    """Where the issue is in the system."""

    location: str  # wallet_system, bank, provider, fraud_system, etc.
    explanation: str  # Exact data-driven explanation
    evidence: list[str] = field(default_factory=list)  # e.g. ["transaction.status=pending"]
    confidence: str = "unknown"  # high | medium | low


@dataclass(frozen=True)
class Resolution:
    """Recommended resolution with staff instructions."""

    recommended_action: str  # Display label: force_success, refund, keep_locked, etc.
    reason: str
    approval_required: bool
    next_steps_for_staff: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosticResult:
    """Full structured diagnostic for a case."""

    bottleneck: Bottleneck
    resolution: Resolution
    missing_data: list[str] = field(default_factory=list)


# ─── Evidence Extractor Helpers ─────────────────────────────────


def _extract_evidence_dict(evidence_bundle: Any) -> dict[str, Any]:
    """Safely convert evidence_bundle to dict."""
    if evidence_bundle is None:
        return {}
    if hasattr(evidence_bundle, "model_dump"):
        return evidence_bundle.model_dump(mode="json", exclude_none=True)
    if isinstance(evidence_bundle, dict):
        return evidence_bundle
    return {}


def _get_txn(eb: dict) -> dict | None:
    """Get transaction dict from evidence."""
    t = eb.get("transaction")
    return t if isinstance(t, dict) else None


def _get_recon(eb: dict) -> dict | None:
    """Get reconciliation dict from evidence."""
    r = eb.get("reconciliation_status")
    return r if isinstance(r, dict) else None


def _get_ledger(eb: dict) -> dict | None:
    """Get wallet_ledger dict from evidence."""
    wl = eb.get("wallet_ledger")
    return wl if isinstance(wl, dict) else None


def _get_account(eb: dict) -> dict | None:
    """Get account_status dict from evidence."""
    a = eb.get("account_status")
    return a if isinstance(a, dict) else None


def _get_fraud(eb: dict) -> dict | None:
    """Get fraud_case dict from evidence."""
    f = eb.get("fraud_case")
    return f if isinstance(f, dict) else None


# ─── Wallet Topup Diagnostics ──────────────────────────────────


def _diagnose_wallet_topup(
    diagnosis: str,
    eb: dict[str, Any],
) -> DiagnosticResult:
    """Diagnose wallet topup case from rule engine diagnosis + evidence.

    Decision rules (from spec):
    1. bank=success + money=true + txn=pending → wallet_system bottleneck
    2. bank=success + money=true + txn=success + no ledger → ledger posting
    3. bank=pending → bank bottleneck
    4. bank=failed / money=false → bank side failed
    5. no transaction → missing data
    """
    txn = _get_txn(eb)
    recon = _get_recon(eb)
    ledger = _get_ledger(eb)

    txn_status = txn.get("status") if txn else None
    txn_amount = txn.get("amount") if txn else None
    txn_type = txn.get("service_type") if txn else None
    bank_status = (recon.get("bank_status") or "").lower() if recon else ""
    money_received = recon.get("money_received_in_master_wallet") if recon else None
    ledger_status = ledger.get("status") if ledger else None

    evidence_points: list[str] = []
    if txn_status is not None:
        evidence_points.append(f"transaction.status={txn_status}")
    if txn_amount is not None:
        evidence_points.append(f"transaction.amount={txn_amount:,}đ")
    if txn_type:
        evidence_points.append(f"transaction.type={txn_type}")
    if bank_status:
        evidence_points.append(f"bank_reconciliation.bank_status={bank_status}")
    if money_received is not None:
        evidence_points.append(
            f"bank_reconciliation.money_received_in_master_wallet={money_received}"
        )
    if ledger is not None:
        evidence_points.append(f"wallet_ledger.status={ledger_status}")
    else:
        evidence_points.append("wallet_ledger=không tìm thấy")

    missing: list[str] = []

    # ── Rule 5: No transaction → can't identify ──
    if txn is None:
        missing.append("transaction_id")
        if recon is None:
            missing.append("bank_ref_id")
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="unknown",
                explanation=(
                    "Không thể xác định giao dịch vì thiếu transaction_id "
                    "hoặc bank_ref_id. Agent cần thêm thông tin để tra cứu."
                ),
                evidence=evidence_points,
                confidence="low",
            ),
            resolution=Resolution(
                recommended_action="request_more_info",
                reason="Thiếu thông tin định danh giao dịch",
                approval_required=False,
                next_steps_for_staff=[
                    "Yêu cầu khách cung cấp mã giao dịch hoặc biên lai bank",
                    "Tra cứu bằng số điện thoại/email nếu có",
                ],
            ),
            missing_data=missing,
        )

    # No reconciliation data
    if recon is None:
        missing.append("bank_reconciliation")
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="reconciliation",
                explanation=(
                    f"Giao dịch {txn_status} nhưng chưa có dữ liệu đối soát bank. "
                    "Không thể xác nhận tiền đã vào master wallet hay chưa."
                ),
                evidence=evidence_points,
                confidence="low",
            ),
            resolution=Resolution(
                recommended_action="wait_reconciliation_or_recheck_bank",
                reason="Thiếu dữ liệu đối soát ngân hàng",
                approval_required=True,
                next_steps_for_staff=[
                    "Kiểm tra hệ thống reconciliation",
                    "Liên hệ bank nếu đối soát chưa hoàn tất",
                    "Cập nhật cho khách sau khi có kết quả",
                ],
            ),
            missing_data=missing,
        )

    # ── Rule 1: bank=success + money=true + txn=pending ──
    if (
        bank_status == "success"
        and money_received is True
        and txn_status == "pending"
    ):
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="wallet_system",
                explanation=(
                    "Bank đã xác nhận giao dịch thành công và tiền đã vào master wallet, "
                    "nhưng giao dịch ví vẫn ở trạng thái PENDING. "
                    "Điểm nghẽn nằm ở bước cập nhật trạng thái giao dịch ví / "
                    "wallet ledger posting."
                ),
                evidence=evidence_points,
                confidence="high",
            ),
            resolution=Resolution(
                recommended_action="force_success",
                reason=(
                    "Bank confirmed success, tiền đã vào master wallet. "
                    "Giao dịch cần được force-success để cộng tiền vào ví user."
                ),
                approval_required=True,
                next_steps_for_staff=[
                    "Xác nhận bank_status=success và money_received_in_master_wallet=true",
                    "Kiểm tra số tiền giao dịch khớp với bank reconciliation",
                    "Phê duyệt draft force-success nếu dữ liệu đồng nhất",
                    "Không tự động cập nhật wallet — chờ phê duyệt",
                ],
            ),
            missing_data=missing,
        )

    # ── Rule 2: bank=success + money=true + txn=success but ledger missing ──
    if (
        bank_status == "success"
        and money_received is True
        and txn_status == "success"
    ):
        if ledger is None or ledger_status in (None, "unknown", ""):
            return DiagnosticResult(
                bottleneck=Bottleneck(
                    location="wallet_system",
                    explanation=(
                        "Giao dịch đã SUCCESS và bank xác nhận tiền vào master wallet, "
                        "nhưng wallet ledger chưa ghi nhận (ledger missing hoặc status unknown). "
                        "Điểm nghẽn không phải bank reconciliation mà là "
                        "bước ledger posting / wallet balance update."
                    ),
                    evidence=evidence_points,
                    confidence="high",
                ),
                resolution=Resolution(
                    recommended_action="post_ledger_or_manual_balance_review",
                    reason="Transaction SUCCESS nhưng ledger/balance chưa được cập nhật",
                    approval_required=True,
                    next_steps_for_staff=[
                        "Kiểm tra wallet_ledger entries cho transaction này",
                        "Xác nhận số dư ví hiện tại của user",
                        "Nếu thiếu ledger entry, tạo ticket manual balance adjustment",
                        "Escalate đến team ví nếu cần",
                    ],
                ),
                missing_data=["wallet_ledger.entries"],
            )

        # txn=success + ledger exists → already processed normally
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="wallet_system",
                explanation=(
                    f"Giao dịch đã hoàn tất (status={txn_status}), "
                    "bank xác nhận thành công, ledger đã ghi nhận. "
                    "Không phát hiện điểm nghẽn — giao dịch có thể đã được xử lý."
                ),
                evidence=evidence_points,
                confidence="high",
            ),
            resolution=Resolution(
                recommended_action="inform_customer",
                reason="Giao dịch đã được xử lý thành công",
                approval_required=False,
                next_steps_for_staff=[
                    "Xác nhận số dư ví đã được cập nhật",
                    "Thông báo cho khách giao dịch đã thành công",
                    "Nếu khách vẫn chưa thấy tiền, kiểm tra cache/delay ví",
                ],
            ),
        )

    # ── Rule 3: bank=pending ──
    if bank_status == "pending" or bank_status == "":
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="bank",
                explanation=(
                    "Đối soát ngân hàng chưa hoàn tất "
                    f"(bank_status={bank_status or 'chưa có'}). "
                    "Hệ thống ví không thể force-success an toàn vì chưa xác nhận "
                    "tiền đã vào master wallet."
                ),
                evidence=evidence_points,
                confidence="medium",
            ),
            resolution=Resolution(
                recommended_action="wait_reconciliation_or_recheck_bank",
                reason="Bank reconciliation chưa hoàn tất",
                approval_required=False,
                next_steps_for_staff=[
                    "Chờ bank reconciliation hoàn tất",
                    "Kiểm tra lại sau 1-2 giờ",
                    "Thông báo khách chờ kết quả đối soát",
                ],
            ),
        )

    # ── Rule 4: bank=failed / money not received ──
    if bank_status in ("failed", "fail", "rejected") or money_received is False:
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="bank",
                explanation=(
                    f"Ngân hàng báo giao dịch {bank_status or 'failed'} "
                    f"hoặc tiền chưa vào master wallet "
                    f"(money_received_in_master_wallet={money_received}). "
                    "Vấn đề không phải ở hệ thống ví — force-success là không an toàn."
                ),
                evidence=evidence_points,
                confidence="high",
            ),
            resolution=Resolution(
                recommended_action="wait_bank_refund_or_inform_customer",
                reason="Bank side failed hoặc tiền chưa vào master wallet",
                approval_required=False,
                next_steps_for_staff=[
                    "Thông báo khách chờ bank hoàn tiền tự động (thường 1-3 ngày)",
                    "Nếu bank đã trừ tiền, yêu cầu khách liên hệ bank",
                    "Không force-success vì tiền chưa được xác nhận",
                ],
            ),
        )

    # ── Transaction not pending (already processed) ──
    if txn_status and txn_status != "pending":
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="wallet_system",
                explanation=(
                    f"Giao dịch đang ở trạng thái {txn_status} (không phải pending). "
                    "Có thể giao dịch đã được xử lý hoặc đã bị hủy."
                ),
                evidence=evidence_points,
                confidence="medium",
            ),
            resolution=Resolution(
                recommended_action="inform_customer",
                reason=f"Giao dịch status={txn_status}, không cần force-success",
                approval_required=False,
                next_steps_for_staff=[
                    f"Kiểm tra chi tiết giao dịch status={txn_status}",
                    "Nếu thành công, xác nhận số dư ví",
                    "Nếu thất bại, hướng dẫn khách thực hiện lại giao dịch",
                ],
            ),
        )

    # ── Fallback ──
    return DiagnosticResult(
        bottleneck=Bottleneck(
            location="unknown",
            explanation=(
                f"Không thể xác định điểm nghẽn chính xác. "
                f"Transaction status={txn_status}, bank_status={bank_status}, "
                f"money_received={money_received}."
            ),
            evidence=evidence_points,
            confidence="low",
        ),
        resolution=Resolution(
            recommended_action="manual_review",
            reason="Dữ liệu không khớp pattern chuẩn",
            approval_required=True,
            next_steps_for_staff=[
                "Kiểm tra thủ công dữ liệu giao dịch, bank, và wallet ledger",
                "Escalate nếu cần",
            ],
        ),
    )


# ─── Fraud Account Lock Diagnostics ────────────────────────────


def _diagnose_fraud_account_lock(
    diagnosis: str,
    eb: dict[str, Any],
    extracted_info: dict[str, Any] | None = None,
) -> DiagnosticResult:
    """Diagnose fraud account lock case.

    Decision rules:
    1. risk high + fraud confirmed → keep locked
    2. locked + risk low/FP → unlock (draft)
    3. identity not found → request identity correction
    4. insufficient evidence → request more info
    """
    acct = _get_account(eb)
    fraud = _get_fraud(eb)

    account_status_val = acct.get("account_status") if acct else None
    withdrawal_val = acct.get("withdrawal_enabled") if acct else None
    lock_reason = acct.get("lock_reason") if acct else None
    risk_score = fraud.get("risk_score") if fraud else None
    risk_level = fraud.get("risk_level") if fraud else None
    fraud_status = fraud.get("fraud_status") if fraud else None
    recommended_decision = fraud.get("recommended_decision") if fraud else None

    evidence_points: list[str] = []
    if account_status_val:
        evidence_points.append(f"account.status={account_status_val}")
    if withdrawal_val is not None:
        evidence_points.append(f"withdrawal_enabled={withdrawal_val}")
    if lock_reason:
        evidence_points.append(f"lock_reason={lock_reason}")
    if risk_score is not None:
        evidence_points.append(f"risk_score={risk_score}")
    if risk_level:
        evidence_points.append(f"risk_level={risk_level}")
    if fraud_status:
        evidence_points.append(f"fraud_status={fraud_status}")
    if recommended_decision:
        evidence_points.append(f"recommended_decision={recommended_decision}")

    # Add signals if available
    if fraud:
        signals = fraud.get("signals", {})
        for key in ("suspicious_login", "abnormal_transaction", "promotion_abuse"):
            if key in signals:
                evidence_points.append(f"signals.{key}={signals[key]}")
        if fraud.get("device_events"):
            evidence_points.append(f"device_events={len(fraud['device_events'])} events")
        if fraud.get("recent_transactions"):
            evidence_points.append(
                f"recent_transactions={len(fraud['recent_transactions'])} txns"
            )

    # ── Identity not found ──
    identity_not_found = acct is None and fraud is None
    ei_phone = None
    if extracted_info:
        ei_phone = extracted_info.get("phone")

    if identity_not_found:
        missing = ["user_id"]
        if ei_phone:
            evidence_points.append(f"phone_provided={ei_phone}")
            evidence_points.append("account_lookup=không tìm thấy")
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="identity_lookup",
                explanation=(
                    f"Không tìm thấy tài khoản khớp với thông tin khách cung cấp"
                    + (f" (SĐT: {ei_phone})" if ei_phone else "")
                    + ". Chưa thể kiểm tra trạng thái khóa, rút tiền hoặc "
                    "dữ liệu Risk/Fraud."
                ),
                evidence=evidence_points,
                confidence="low",
            ),
            resolution=Resolution(
                recommended_action="request_identity_correction",
                reason="Không định danh được tài khoản",
                approval_required=False,
                next_steps_for_staff=[
                    "Yêu cầu khách xác nhận lại SĐT đăng ký ví, email, wallet_id",
                    "Hoặc yêu cầu mã giao dịch gần nhất",
                    "Sau khi định danh, mới kiểm tra account_status, fraud_case, KYC",
                ],
            ),
            missing_data=missing,
        )

    # ── Rule 1: High risk + confirmed fraud ──
    is_high_risk = (
        risk_level == "high"
        or (risk_score is not None and risk_score >= 70)
    )
    if is_high_risk and fraud_status not in (None, "false_positive"):
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="fraud_system",
                explanation=(
                    "Hệ thống ghi nhận nhiều tín hiệu rủi ro ở mức cao"
                    + (f" (risk_score={risk_score})" if risk_score is not None else "")
                    + f", fraud_status={fraud_status}. "
                    "Tài khoản bị khóa do fraud/risk control."
                ),
                evidence=evidence_points,
                confidence="high",
            ),
            resolution=Resolution(
                recommended_action="keep_locked",
                reason="Risk cao, fraud signals confirmed",
                approval_required=True,
                next_steps_for_staff=[
                    "Giữ trạng thái khóa tài khoản",
                    "Yêu cầu khách bổ sung giấy tờ xác minh danh tính",
                    "Escalate đến Risk/Fraud team nếu khách không hợp tác",
                    "Không tiết lộ risk_score hoặc fraud threshold cho khách",
                ],
            ),
        )

    # ── Rule 2: False positive candidate ──
    is_false_positive = (
        risk_level in ("low", "medium")
        or recommended_decision in ("unlock", "false_positive_candidate", "review_needed")
    )
    if is_false_positive and account_status_val == "locked":
        return DiagnosticResult(
            bottleneck=Bottleneck(
                location="fraud_system",
                explanation=(
                    "Hệ thống Fraud Detection đã khóa tài khoản, "
                    "nhưng dữ liệu rủi ro hiện ở mức thấp"
                    + (f" (risk_score={risk_score})" if risk_score is not None else "")
                    + ". KYC hợp lệ và không có tín hiệu giao dịch bất thường "
                    "nghiêm trọng. Case này có khả năng là false positive."
                ),
                evidence=evidence_points,
                confidence="high",
            ),
            resolution=Resolution(
                recommended_action="unlock_account",
                reason="False positive candidate — risk thấp, KYC hợp lệ",
                approval_required=True,
                next_steps_for_staff=[
                    "Kiểm tra fraud evidence và risk signals chi tiết",
                    "Xác nhận false positive",
                    "Phê duyệt draft mở khóa tài khoản",
                    "Cần Risk/Ops phê duyệt trước khi unlock",
                ],
            ),
        )

    # ── Rule 3: Insufficient evidence ──
    missing: list[str] = []
    if fraud is None:
        missing.append("fraud_case")
    if acct is None:
        missing.append("account_status")

    return DiagnosticResult(
        bottleneck=Bottleneck(
            location="fraud_system",
            explanation=(
                "Hệ thống Fraud Detection đã khóa tài khoản. "
                "Tuy nhiên dữ liệu fraud evidence chưa đầy đủ"
                + (f" (risk_level={risk_level})" if risk_level else "")
                + " để kết luận an toàn."
            ),
            evidence=evidence_points,
            confidence="medium",
        ),
        resolution=Resolution(
            recommended_action="request_more_info",
            reason="Evidence chưa đủ để kết luận",
            approval_required=True,
            next_steps_for_staff=[
                "Kiểm tra thêm fraud case, risk signals",
                "Kiểm tra lịch sử giao dịch và thiết bị đăng nhập",
                "Kiểm tra trạng thái KYC",
                "Escalate Risk/Ops nếu bằng chứng chưa đủ",
            ],
        ),
        missing_data=missing,
    )


# ─── Generic / Other Workflow Diagnostics ──────────────────────


def _diagnose_generic(
    workflow: str,
    diagnosis: str,
    eb: dict[str, Any],
) -> DiagnosticResult:
    """Generic diagnostic for non-specialized workflows."""
    txn = _get_txn(eb)
    ledger = _get_ledger(eb)

    evidence_points: list[str] = []
    missing: list[str] = []

    if txn is None:
        missing.append("transaction")
    else:
        evidence_points.append(f"transaction.status={txn.get('status')}")
        evidence_points.append(f"transaction.amount={txn.get('amount')}")

    if ledger is None:
        missing.append("wallet_ledger")
    else:
        evidence_points.append(f"wallet_ledger.status={ledger.get('status')}")

    return DiagnosticResult(
        bottleneck=Bottleneck(
            location="unknown",
            explanation=(
                f"Workflow {workflow}: diagnosis={diagnosis}. "
                "Agent đã thu thập evidence nhưng cần nhân viên kiểm tra chi tiết."
            ),
            evidence=evidence_points,
            confidence="low",
        ),
        resolution=Resolution(
            recommended_action="manual_review",
            reason=f"Workflow {workflow} cần kiểm tra thủ công",
            approval_required=True,
            next_steps_for_staff=[
                "Kiểm tra evidence đã thu thập",
                "Xác minh thông tin với hệ thống nguồn",
                "Quyết định bước xử lý tiếp theo",
            ],
        ),
        missing_data=missing,
    )



# ─── Custom diagnoser result wrapper ───────────────────────────


def _wrap_custom_result(
    raw: Any,
    workflow: str,
    diagnosis: str,
    eb: dict[str, Any],
) -> DiagnosticResult:
    """Convert arbitrary custom diagnoser output into a DiagnosticResult.

    Accepts dicts with fields like: can_explain_to_customer, issue_location,
    customer_safe_explanation, confidence, etc.
    """
    if isinstance(raw, dict):
        location = raw.get("issue_location", "unknown")
        explanation = raw.get("customer_safe_explanation", "")
        confidence = raw.get("confidence", "low")
        requires_staff = raw.get("requires_staff_review", True)
        missing = raw.get("missing_fields", [])

        return DiagnosticResult(
            bottleneck=Bottleneck(
                location=location,
                explanation=explanation,
                evidence=[],
                confidence=confidence,
            ),
            resolution=Resolution(
                recommended_action="manual_review" if requires_staff else "inform_customer",
                reason=explanation,
                approval_required=requires_staff,
            ),
            missing_data=missing,
        )

    # Fallback: wrap unknown types as generic
    return _diagnose_generic(workflow, diagnosis, eb)


# ─── Public API ─────────────────────────────────────────────────


def diagnose(
    workflow: str,
    diagnosis: str,
    evidence_bundle: Any,
    extracted_info: dict[str, Any] | None = None,
) -> DiagnosticResult:
    """Main entry point: produce structured diagnostic from case data.

    Dispatch order:
      1. Workflow registry custom diagnoser (if registered).
      2. Built-in per-workflow functions (wallet_topup, fraud_account_lock).
      3. Generic fallback.

    Args:
        workflow: Selected workflow (e.g. 'wallet_topup', 'fraud_account_lock').
        diagnosis: Rule engine diagnosis string.
        evidence_bundle: EvidenceBundle (Pydantic model or dict).
        extracted_info: Optional extracted complaint info.

    Returns:
        DiagnosticResult with bottleneck, resolution, and missing_data.
    """
    eb = _extract_evidence_dict(evidence_bundle)

    result: DiagnosticResult | None = None

    # ── 1. Registry-based dispatch ──
    try:
        from fintech_agent.workflows.workflow_registry import get_registry

        registry = get_registry()
        spec = registry.get(workflow)
        if spec and spec.diagnoser is not None:
            logger.info(
                "[DiagnosticEngine] Dispatching via registry: workflow=%s",
                workflow,
            )
            result = spec.diagnoser(
                diagnosis=diagnosis,
                evidence_bundle=eb,
                extracted_info=extracted_info,
            )
            # Wrap non-DiagnosticResult outputs (e.g. dicts) into a
            # DiagnosticResult so the rest of the function can access
            # .bottleneck/.resolution safely.
            if result is not None and not isinstance(result, DiagnosticResult):
                result = _wrap_custom_result(result, workflow, diagnosis, eb)
    except Exception as exc:
        logger.warning(
            "[DiagnosticEngine] Registry dispatch failed for '%s': %s",
            workflow, exc,
        )

    # ── 2. Built-in per-workflow functions ──
    if result is None:
        if workflow == "wallet_topup":
            result = _diagnose_wallet_topup(diagnosis, eb)
        elif workflow == "fraud_account_lock":
            result = _diagnose_fraud_account_lock(diagnosis, eb, extracted_info)
        else:
            result = _diagnose_generic(workflow, diagnosis, eb)

    logger.info(
        "[DiagnosticEngine] workflow=%s, diagnosis=%s, "
        "bottleneck=%s, confidence=%s, evidence=%d, missing=%d",
        workflow,
        diagnosis,
        result.bottleneck.location,
        result.bottleneck.confidence,
        len(result.bottleneck.evidence),
        len(result.missing_data),
    )

    return result

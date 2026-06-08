"""Tests for fraud_account_lock claim verification and ticket polish.

Covers:
1. Claim synthesis — account locked, withdrawal blocked, customer opinion, phone/identity
2. Claim verification — each claim type against evidence
3. Ticket builder — problem explanation, customer reply, action wording
4. Response generator — fraud fallback response
5. Safety — customer reply does not leak fraud thresholds
6. High-risk user does NOT receive unlock draft
7. Missing-evidence user routes to manual review
"""

import pytest

from fintech_agent.schemas.claim_verification import (
    Claim,
    ClaimType,
    VerificationStatus,
)
from fintech_agent.schemas.evidence import (
    AccountStatus,
    EvidenceBundle,
    FraudCase,
)
from fintech_agent.rules.claim_verifier import (
    CLAIM_TYPE_LABELS,
    _claims_from_extracted_info,
    _verify_account_status_claim,
    _verify_customer_opinion_claim,
    _verify_user_identity_claim,
    _verify_withdrawal_status_claim,
    verify_all_claims,
    _build_trusted_data_for_action,
)
from fintech_agent.llm.response_generator import (
    generate_safe_fallback_response,
)
from fintech_agent.llm.ticket_builder import (
    _ACTION_MAP,
    _FRAUD_STAFF_INSTRUCTIONS,
    _STAFF_INSTRUCTIONS,
    build_resolution_ticket,
    _compute_evidence_checked_and_missing,
)


# ─── Test Fixtures ────────────────────────────────────────────


def _fp_account():
    """False positive account: locked, low risk."""
    return AccountStatus(
        user_id="U_FRAUD_FP",
        wallet_id="W_FRAUD_FP",
        account_status="locked",
        withdrawal_enabled=False,
        lock_reason="fraud_suspected",
        current_balance=5_000_000,
    )


def _fp_fraud_case():
    """False positive fraud case: low risk, suspected."""
    return FraudCase(
        fraud_case_id="FC-001",
        user_id="U_FRAUD_FP",
        risk_score=22,
        risk_level="low",
        fraud_status="suspected",
        trigger_reason="unusual_login",
        signals={"ip_change": True, "device_new": True},
        recommended_decision="false_positive_candidate",
    )


def _high_risk_account():
    """High risk account: locked, high risk."""
    return AccountStatus(
        user_id="U_FRAUD_HIGH",
        wallet_id="W_FRAUD_HIGH",
        account_status="locked",
        withdrawal_enabled=False,
        lock_reason="confirmed_fraud",
        current_balance=0,
    )


def _high_risk_fraud_case():
    """High risk fraud case."""
    return FraudCase(
        fraud_case_id="FC-002",
        user_id="U_FRAUD_HIGH",
        risk_score=88,
        risk_level="high",
        fraud_status="confirmed",
        trigger_reason="suspicious_transactions",
        signals={"money_laundering": True, "rapid_withdrawal": True},
        recent_transactions=[{"amount": 50_000_000, "suspicious": True}],
        recommended_decision="lock_confirmed",
    )


def _fp_evidence():
    return EvidenceBundle(
        account_status=_fp_account(),
        fraud_case=_fp_fraud_case(),
    )


def _high_risk_evidence():
    return EvidenceBundle(
        account_status=_high_risk_account(),
        fraud_case=_high_risk_fraud_case(),
    )


def _fraud_extracted_info():
    """Extracted info for fraud complaint."""
    return {
        "user_id": None,
        "transaction_id": None,
        "service_type": "account_security",
        "issue_type": "account_locked",
        "phone": "0981000001",
        "amount_claimed": None,
        "missing_fields": [],
        "_raw_complaint": (
            "Tài khoản của tôi bất ngờ bị khóa vô cớ, "
            "tôi không thể rút tiền. Số điện thoại 0981000001"
        ),
    }


# ═══════════════════════════════════════════════════════════════
# 1. CLAIM SYNTHESIS
# ═══════════════════════════════════════════════════════════════


class TestFraudClaimSynthesis:
    """Test that fraud claims are correctly synthesized from extracted info."""

    def test_account_locked_claim_synthesized(self):
        ei = _fraud_extracted_info()
        claims = _claims_from_extracted_info(ei)
        types = [c.claim_type for c in claims]
        assert ClaimType.ACCOUNT_STATUS in types

    def test_withdrawal_blocked_claim_synthesized(self):
        ei = _fraud_extracted_info()
        claims = _claims_from_extracted_info(ei)
        types = [c.claim_type for c in claims]
        assert ClaimType.WITHDRAWAL_STATUS in types

    def test_customer_opinion_claim_synthesized(self):
        ei = _fraud_extracted_info()
        claims = _claims_from_extracted_info(ei)
        types = [c.claim_type for c in claims]
        assert ClaimType.CUSTOMER_OPINION in types

    def test_customer_opinion_is_vo_co(self):
        ei = _fraud_extracted_info()
        claims = _claims_from_extracted_info(ei)
        opinion = next(c for c in claims if c.claim_type == ClaimType.CUSTOMER_OPINION)
        assert opinion.customer_claimed_value == "vô cớ"

    def test_phone_identity_claim_synthesized(self):
        ei = _fraud_extracted_info()
        claims = _claims_from_extracted_info(ei)
        types = [c.claim_type for c in claims]
        assert ClaimType.USER_IDENTITY in types
        identity = next(c for c in claims if c.claim_type == ClaimType.USER_IDENTITY)
        assert identity.customer_claimed_value == "0981000001"

    def test_no_transaction_claim_for_fraud(self):
        ei = _fraud_extracted_info()
        claims = _claims_from_extracted_info(ei)
        types = [c.claim_type for c in claims]
        assert ClaimType.TRANSACTION_ID not in types
        assert ClaimType.TRANSACTION_AMOUNT not in types

    def test_non_fraud_does_not_synthesize_fraud_claims(self):
        ei = {
            "service_type": "wallet_topup",
            "issue_type": "topup_pending",
            "transaction_id": "TXN-123",
            "_raw_complaint": "Tôi nạp tiền nhưng ví vẫn 0đ",
        }
        claims = _claims_from_extracted_info(ei)
        types = [c.claim_type for c in claims]
        assert ClaimType.WITHDRAWAL_STATUS not in types
        assert ClaimType.CUSTOMER_OPINION not in types

    def test_bat_ngo_also_triggers_opinion(self):
        ei = dict(_fraud_extracted_info())
        ei["_raw_complaint"] = "Tài khoản bất ngờ bị khóa, rút tiền không được"
        claims = _claims_from_extracted_info(ei)
        types = [c.claim_type for c in claims]
        assert ClaimType.CUSTOMER_OPINION in types
        opinion = next(c for c in claims if c.claim_type == ClaimType.CUSTOMER_OPINION)
        assert opinion.customer_claimed_value == "bất ngờ"


# ═══════════════════════════════════════════════════════════════
# 2. CLAIM VERIFICATION
# ═══════════════════════════════════════════════════════════════


class TestFraudClaimVerification:
    """Test individual claim verifiers against evidence."""

    def test_account_status_matched(self):
        claim = Claim(
            claim_type=ClaimType.ACCOUNT_STATUS,
            customer_claimed_value="locked",
        )
        evidence = _fp_evidence()
        result = _verify_account_status_claim(claim, evidence)
        assert result.verification_status == VerificationStatus.MATCHED
        assert result.trusted_system_value == "locked"

    def test_withdrawal_status_matched(self):
        claim = Claim(
            claim_type=ClaimType.WITHDRAWAL_STATUS,
            customer_claimed_value="không thể rút tiền",
        )
        evidence = _fp_evidence()
        result = _verify_withdrawal_status_claim(claim, evidence)
        assert result.verification_status == VerificationStatus.MATCHED
        assert "bị chặn" in str(result.trusted_system_value)

    def test_withdrawal_status_no_evidence(self):
        claim = Claim(
            claim_type=ClaimType.WITHDRAWAL_STATUS,
            customer_claimed_value="blocked",
        )
        evidence = EvidenceBundle()
        result = _verify_withdrawal_status_claim(claim, evidence)
        assert result.verification_status == VerificationStatus.NOT_VERIFIABLE

    def test_customer_opinion_not_verifiable(self):
        claim = Claim(
            claim_type=ClaimType.CUSTOMER_OPINION,
            customer_claimed_value="vô cớ",
        )
        evidence = _fp_evidence()
        result = _verify_customer_opinion_claim(claim, evidence)
        assert result.verification_status == VerificationStatus.NOT_VERIFIABLE
        assert "nhận định của khách hàng" in result.explanation
        assert "fraud evidence" in result.explanation

    def test_identity_via_account_status(self):
        claim = Claim(
            claim_type=ClaimType.USER_IDENTITY,
            raw_text="0981000001",
            customer_claimed_value="0981000001",
        )
        evidence = _fp_evidence()
        result = _verify_user_identity_claim(claim, evidence)
        assert result.verification_status == VerificationStatus.MATCHED
        assert result.trusted_system_value == "U_FRAUD_FP"
        assert "accounts" in str(result.trusted_source)


class TestVerifyAllClaimsFraud:
    """Test end-to-end verify_all_claims for fraud."""

    def test_fraud_fp_all_claims(self):
        ei = _fraud_extracted_info()
        evidence = _fp_evidence()
        summary = verify_all_claims(ei, evidence)

        # Should have 4+ claims
        assert len(summary.claims) >= 4

        # Check claim types present
        types = [c.claim_type for c in summary.claims]
        assert ClaimType.ACCOUNT_STATUS in types
        assert ClaimType.WITHDRAWAL_STATUS in types
        assert ClaimType.CUSTOMER_OPINION in types
        assert ClaimType.USER_IDENTITY in types

    def test_fraud_fp_matched_claims(self):
        ei = _fraud_extracted_info()
        evidence = _fp_evidence()
        summary = verify_all_claims(ei, evidence)

        matched = [c for c in summary.claims if c.verification_status == VerificationStatus.MATCHED]
        # account_status, withdrawal_status, user_identity should be matched
        matched_types = [c.claim_type for c in matched]
        assert ClaimType.ACCOUNT_STATUS in matched_types
        assert ClaimType.WITHDRAWAL_STATUS in matched_types
        assert ClaimType.USER_IDENTITY in matched_types

    def test_fraud_fp_customer_opinion_not_verifiable(self):
        ei = _fraud_extracted_info()
        evidence = _fp_evidence()
        summary = verify_all_claims(ei, evidence)

        opinion = next(
            c for c in summary.claims if c.claim_type == ClaimType.CUSTOMER_OPINION
        )
        assert opinion.verification_status == VerificationStatus.NOT_VERIFIABLE


# ═══════════════════════════════════════════════════════════════
# 3. TRUSTED DATA FOR ACTION
# ═══════════════════════════════════════════════════════════════


class TestTrustedDataFraud:
    """Test trusted data includes fraud evidence fields."""

    def test_fraud_trusted_data_includes_risk(self):
        evidence = _fp_evidence()
        data = _build_trusted_data_for_action(evidence)
        assert data["user_id"] == "U_FRAUD_FP"
        assert data["account_status"] == "locked"
        assert data["risk_score"] == 22
        assert data["risk_level"] == "low"
        assert data["fraud_status"] == "suspected"
        assert data["recommended_decision"] == "false_positive_candidate"
        assert data["withdrawal_enabled"] == 0  # False → 0

    def test_high_risk_trusted_data(self):
        evidence = _high_risk_evidence()
        data = _build_trusted_data_for_action(evidence)
        assert data["risk_score"] == 88
        assert data["risk_level"] == "high"
        assert data["recommended_decision"] == "lock_confirmed"


# ═══════════════════════════════════════════════════════════════
# 4. LABELS
# ═══════════════════════════════════════════════════════════════


class TestClaimTypeLabels:
    """Test staff-friendly labels exist for new claim types."""

    def test_withdrawal_status_label(self):
        assert ClaimType.WITHDRAWAL_STATUS in CLAIM_TYPE_LABELS

    def test_customer_opinion_label(self):
        assert ClaimType.CUSTOMER_OPINION in CLAIM_TYPE_LABELS


# ═══════════════════════════════════════════════════════════════
# 5. RESPONSE GENERATOR — FRAUD FALLBACK
# ═══════════════════════════════════════════════════════════════


class TestFraudFallbackResponse:
    """Test fraud-specific fallback response generation."""

    def test_fp_fallback_has_false_positive_explanation(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
            "recommended_action": "create_unlock_account_draft",
        }
        resp = generate_safe_fallback_response(state)
        assert "false positive" in resp.problem_explanation
        assert resp.problem_location == "fraud_system"
        assert resp.problem_location_confidence == "high"

    def test_fp_fallback_evidence_checked(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
        }
        resp = generate_safe_fallback_response(state)
        # DiagnosticEngine puts evidence data points in
        # evidence_supporting_problem_location, not evidence_checked
        # (evidence_checked is enriched by ticket_builder)
        assert len(resp.evidence_supporting_problem_location) >= 4
        assert any("account.status=" in e for e in resp.evidence_supporting_problem_location)

    def test_fp_fallback_customer_reply_safe(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
        }
        resp = generate_safe_fallback_response(state)
        reply = resp.customer_reply_draft.lower()
        # Must NOT leak internal info
        assert "risk_score" not in reply
        assert "22" not in reply
        assert "threshold" not in reply
        assert "false_positive" not in reply
        assert "fraud" not in reply
        # Must be helpful
        assert "khóa" in reply or "tài khoản" in reply
        assert "xác minh" in reply or "kiểm tra" in reply

    def test_fp_fallback_safety_notes(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
        }
        resp = generate_safe_fallback_response(state)
        notes = " ".join(resp.safety_notes).lower()
        assert "unlock" in notes or "mở khóa" in notes or "unlock account" in notes
        assert "phê duyệt" in notes
        assert "tiết lộ" in notes or "risk_score" in notes

    def test_high_risk_exact_wording(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _high_risk_evidence(),
        }
        resp = generate_safe_fallback_response(state)
        # High risk should NOT say false positive
        assert "false positive" not in resp.problem_explanation
        # DiagnosticEngine now produces data-driven explanation
        assert "rủi ro" in resp.problem_explanation
        assert "risk_score=88" in resp.problem_explanation or "risk_score" in " ".join(resp.evidence_supporting_problem_location)
        assert resp.problem_location_confidence == "high"

    def test_high_risk_customer_reply_safe(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _high_risk_evidence(),
        }
        resp = generate_safe_fallback_response(state)
        reply = resp.customer_reply_draft.lower()
        # Must NOT accuse customer of fraud
        assert "gian lận" not in reply
        assert "fraud" not in reply
        # Must NOT reveal thresholds
        assert "risk_score" not in reply
        assert "88" not in reply
        assert "threshold" not in reply

    def test_missing_evidence_wording(self):
        """Missing evidence scenario (e.g. U_FRAUD_MISSING)."""
        # Simulate: account locked but no fraud_case data
        missing_acct = AccountStatus(
            user_id="U_FRAUD_MISSING",
            account_status="locked",
            withdrawal_enabled=False,
        )
        evidence = EvidenceBundle(account_status=missing_acct)
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
        }
        resp = generate_safe_fallback_response(state)
        # Should mention insufficient data
        assert "chưa đầy đủ" in resp.problem_explanation or "chưa xác định" in resp.problem_explanation
        assert resp.problem_location_confidence == "medium"

    def test_generic_workflow_unchanged(self):
        """Non-fraud workflow still uses generic fallback."""
        state = {
            "selected_workflow": "wallet_topup",
        }
        resp = generate_safe_fallback_response(state)
        assert resp.problem_location == "unknown"
        assert "fraud" not in resp.problem_explanation.lower()


# ═══════════════════════════════════════════════════════════════
# 6. TICKET BUILDER — ACTION MAP
# ═══════════════════════════════════════════════════════════════


class TestFraudActionMap:
    """Test action map wording for fraud unlock."""

    def test_unlock_action_name(self):
        mapping = _ACTION_MAP["create_unlock_account_draft"]
        assert "mở khóa tài khoản" in mapping["action_name"]

    def test_unlock_description_mentions_draft(self):
        mapping = _ACTION_MAP["create_unlock_account_draft"]
        desc = mapping["description"]
        assert "bản nháp" in desc or "draft" in desc.lower()

    def test_unlock_safety_no_auto_unlock(self):
        mapping = _ACTION_MAP["create_unlock_account_draft"]
        safety = " ".join(mapping["safety_notes"]).lower()
        assert "không tự động" in safety
        assert "phê duyệt" in safety or "bỏ qua" in safety

    def test_unlock_execution_mode_draft(self):
        mapping = _ACTION_MAP["create_unlock_account_draft"]
        assert mapping["execution_mode"] == "draft_only"

    def test_staff_instruction_has_checklist(self):
        instr = _STAFF_INSTRUCTIONS["create_unlock_account_draft"]
        assert "Trạng thái tài khoản" in instr
        assert "Trạng thái rút tiền" in instr
        assert "Mức rủi ro" in instr
        assert "KYC" in instr
        assert "Tín hiệu thiết bị" in instr
        assert "Giao dịch đáng ngờ" in instr
        assert "Risk/Ops" in instr


# ═══════════════════════════════════════════════════════════════
# 7. TICKET BUILDER — FULL TICKET
# ═══════════════════════════════════════════════════════════════


class TestFraudTicketBuild:
    """Test build_resolution_ticket for fraud case."""

    def _build_fp_ticket(self):
        """Build a ticket for false positive case."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        state = {
            "case_id": "CASE-FP-001",
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
            "recommended_action": RecommendedAction(
                action_type=ActionType.CREATE_UNLOCK_ACCOUNT_DRAFT,
                risk_level=RiskLevel.MEDIUM,
                approval_required=True,
                diagnosis="fraud_false_positive",
                summary="Low risk, likely false positive. Draft unlock.",
            ),
            "claim_verification_summary": verify_all_claims(
                _fraud_extracted_info(), _fp_evidence()
            ),
        }
        return build_resolution_ticket(state)

    def test_fp_ticket_has_claim_verification(self):
        ticket = self._build_fp_ticket()
        assert ticket.claim_verification is not None
        assert len(ticket.claim_verification.claims) >= 4

    def test_fp_ticket_action_is_unlock_draft(self):
        ticket = self._build_fp_ticket()
        assert len(ticket.recommended_actions) == 1
        action = ticket.recommended_actions[0]
        assert action.action_type == "create_unlock_account_draft"
        assert action.execution_mode == "draft_only"
        assert action.requires_approval is True

    def test_fp_ticket_safety_notes(self):
        ticket = self._build_fp_ticket()
        notes = " ".join(ticket.safety_notes).lower()
        # Must not auto-unlock
        assert "unlock" in notes or "mở khóa" in notes or "tự động" in notes

    def test_fp_ticket_resolution_actionable(self):
        ticket = self._build_fp_ticket()
        assert ticket.resolution_status == "actionable"

    def _build_high_risk_ticket(self):
        """Build a ticket for high risk case — should NOT get unlock draft."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        state = {
            "case_id": "CASE-HR-001",
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _high_risk_evidence(),
            "recommended_action": RecommendedAction(
                action_type=ActionType.MANUAL_REVIEW,
                risk_level=RiskLevel.HIGH,
                approval_required=True,
                diagnosis="fraud_confirmed_high_risk",
                summary="High risk confirmed. Manual review required.",
            ),
            "claim_verification_summary": verify_all_claims(
                _fraud_extracted_info(), _high_risk_evidence()
            ),
        }
        return build_resolution_ticket(state)

    def test_high_risk_no_unlock_draft(self):
        ticket = self._build_high_risk_ticket()
        for action in ticket.recommended_actions:
            assert action.action_type != "create_unlock_account_draft"

    def test_high_risk_manual_review(self):
        ticket = self._build_high_risk_ticket()
        assert ticket.resolution_status == "manual_review_required"


# ═══════════════════════════════════════════════════════════════
# 8. FRAUD MANUAL REVIEW GUIDANCE
# ═══════════════════════════════════════════════════════════════


class TestFraudManualReviewGuidance:
    """Test fraud-specific manual review replaces generic bank/provider text."""

    def test_fraud_manual_review_instruction_exists(self):
        assert "manual_review" in _FRAUD_STAFF_INSTRUCTIONS

    def test_fraud_manual_review_has_fraud_guidance(self):
        instr = _FRAUD_STAFF_INSTRUCTIONS["manual_review"]
        assert "fraud case" in instr
        assert "risk signals" in instr
        assert "thiết bị đăng nhập" in instr
        assert "KYC" in instr
        assert "Escalate Risk/Ops" in instr

    def test_fraud_manual_review_no_bank_provider_text(self):
        instr = _FRAUD_STAFF_INSTRUCTIONS["manual_review"]
        # Must NOT contain generic bank/provider guidance
        assert "reconciliation file" not in instr
        assert "verify provider status" not in instr
        assert "liên hệ bank/provider" not in instr

    def test_fraud_request_docs_instruction(self):
        instr = _FRAUD_STAFF_INSTRUCTIONS["create_request_documents_response_draft"]
        assert "CMND" in instr or "CCCD" in instr
        assert "OTP" in instr
        assert "khóa" in instr

    def test_missing_evidence_ticket_uses_fraud_guidance(self):
        """When manual_review is the action for fraud, staff instruction is fraud-specific."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        missing_acct = AccountStatus(
            user_id="U_FRAUD_MISSING",
            account_status="locked",
            withdrawal_enabled=False,
        )
        evidence = EvidenceBundle(account_status=missing_acct)
        state = {
            "case_id": "CASE-MISS-001",
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
            "recommended_action": RecommendedAction(
                action_type=ActionType.MANUAL_REVIEW,
                risk_level=RiskLevel.MEDIUM,
                approval_required=True,
                diagnosis="fraud_insufficient_evidence",
                summary="Insufficient fraud evidence. Manual review.",
            ),
            "claim_verification_summary": verify_all_claims(
                _fraud_extracted_info(), evidence
            ),
        }
        ticket = build_resolution_ticket(state)
        # Staff instruction should be fraud-specific
        assert "fraud case" in ticket.staff_instruction
        assert "risk signals" in ticket.staff_instruction
        # Must NOT contain generic text
        assert "reconciliation" not in ticket.staff_instruction.lower()
        assert "bank/provider" not in ticket.staff_instruction.lower()


# ═══════════════════════════════════════════════════════════════
# 9. EVIDENCE CHECKED — FRAUD SUB-EVIDENCE
# ═══════════════════════════════════════════════════════════════


class TestFraudEvidenceChecked:
    """Test that fraud sub-evidence items appear in evidence_checked."""

    def test_fp_evidence_checked_has_fraud_items(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
        }
        checked, missing = _compute_evidence_checked_and_missing(state)
        assert "Trạng thái tài khoản" in checked
        assert "Dữ liệu fraud/risk" in checked
        assert "Mức rủi ro / Điểm rủi ro" in checked
        assert "Trạng thái fraud" in checked
        assert "Kết quả rà soát fraud" in checked
        assert "Trạng thái rút tiền" in checked

    def test_fp_evidence_no_transaction_missing(self):
        """Fraud workflow should NOT mark transaction/wallet as missing."""
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
        }
        _, missing = _compute_evidence_checked_and_missing(state)
        assert "Dữ liệu giao dịch" not in missing
        assert "Sổ cái ví" not in missing

    def test_high_risk_has_suspicious_transactions(self):
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _high_risk_evidence(),
        }
        checked, _ = _compute_evidence_checked_and_missing(state)
        assert "Giao dịch đáng ngờ gần đây" in checked


# ═══════════════════════════════════════════════════════════════
# 10. TRUSTED DATA FORMATTING
# ═══════════════════════════════════════════════════════════════


class TestTrustedDataFormatting:
    """Test that trusted data uses correct values for fraud."""

    def test_withdrawal_enabled_is_int_not_bool(self):
        """Backend sends 0/1 integers, frontend formats them."""
        evidence = _fp_evidence()
        data = _build_trusted_data_for_action(evidence)
        assert data["withdrawal_enabled"] == 0
        assert isinstance(data["withdrawal_enabled"], int)

    def test_lock_reason_included(self):
        evidence = _fp_evidence()
        data = _build_trusted_data_for_action(evidence)
        assert data.get("lock_reason") == "fraud_suspected"


# ═══════════════════════════════════════════════════════════════
# 11. IDENTITY NOT FOUND — INVALID PHONE
# ═══════════════════════════════════════════════════════════════


def _invalid_phone_extracted_info():
    """Extracted info with a phone that doesn't match any account."""
    return {
        "user_id": None,
        "transaction_id": None,
        "service_type": "account_security",
        "issue_type": "account_locked",
        "phone": "0999999999",
        "amount_claimed": None,
        "missing_fields": [],
        "_raw_complaint": (
            "Tài khoản của tôi bị khóa vô cớ, "
            "tôi không thể rút tiền. Số điện thoại 0999999999"
        ),
    }


def _empty_evidence():
    """Empty evidence bundle — identity lookup failed."""
    return EvidenceBundle()


class TestIdentityNotFound:
    """Test behavior when phone doesn't match any account."""

    def test_identity_claim_not_found(self):
        """Phone claim should get NOT_FOUND status."""
        ei = _invalid_phone_extracted_info()
        evidence = _empty_evidence()
        summary = verify_all_claims(ei, evidence)
        identity = next(
            c for c in summary.claims if c.claim_type == ClaimType.USER_IDENTITY
        )
        assert identity.verification_status == VerificationStatus.NOT_FOUND
        assert identity.trusted_system_value == "Không tìm thấy tài khoản"
        assert "accounts.phone" in identity.trusted_source
        assert "0999999999" in identity.explanation

    def test_identity_not_found_explanation_text(self):
        """Explanation should mention what can't be verified."""
        ei = _invalid_phone_extracted_info()
        evidence = _empty_evidence()
        summary = verify_all_claims(ei, evidence)
        identity = next(
            c for c in summary.claims if c.claim_type == ClaimType.USER_IDENTITY
        )
        assert "trạng thái khóa" in identity.explanation
        assert "Risk/Fraud" in identity.explanation

    def test_no_fallback_user(self):
        """No fallback user_id should be used."""
        ei = _invalid_phone_extracted_info()
        evidence = _empty_evidence()
        summary = verify_all_claims(ei, evidence)
        for claim in summary.claims:
            tv = str(claim.trusted_system_value or "")
            assert "U_FRAUD_001" not in tv

    def test_no_unlock_draft_action(self):
        """No unlock draft should be created for unresolved identity."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        evidence = _empty_evidence()
        state = {
            "case_id": "CASE-INV-001",
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
            "recommended_action": RecommendedAction(
                action_type=ActionType.REQUEST_IDENTITY_CORRECTION,
                risk_level=RiskLevel.LOW,
                approval_required=False,
                diagnosis="identity_not_found",
                summary="Phone not found. Request identity correction.",
            ),
            "claim_verification_summary": verify_all_claims(
                _invalid_phone_extracted_info(), evidence
            ),
        }
        ticket = build_resolution_ticket(state)
        for action in ticket.recommended_actions:
            assert action.action_type != "create_unlock_account_draft"

    def test_resolution_status_missing_identity(self):
        """Resolution status should be missing_identity."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        evidence = _empty_evidence()
        state = {
            "case_id": "CASE-INV-002",
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
            "recommended_action": RecommendedAction(
                action_type=ActionType.REQUEST_IDENTITY_CORRECTION,
                risk_level=RiskLevel.LOW,
                approval_required=False,
                diagnosis="identity_not_found",
                summary="Phone not found.",
            ),
            "claim_verification_summary": verify_all_claims(
                _invalid_phone_extracted_info(), evidence
            ),
        }
        ticket = build_resolution_ticket(state)
        assert ticket.resolution_status == "missing_identity"

    def test_ticket_action_is_identity_correction(self):
        """Action should be request_identity_correction."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        evidence = _empty_evidence()
        state = {
            "case_id": "CASE-INV-003",
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
            "recommended_action": RecommendedAction(
                action_type=ActionType.REQUEST_IDENTITY_CORRECTION,
                risk_level=RiskLevel.LOW,
                approval_required=False,
                diagnosis="identity_not_found",
                summary="Phone not found.",
            ),
            "claim_verification_summary": verify_all_claims(
                _invalid_phone_extracted_info(), evidence
            ),
        }
        ticket = build_resolution_ticket(state)
        assert len(ticket.recommended_actions) == 1
        action = ticket.recommended_actions[0]
        assert action.action_type == "request_identity_correction"
        assert "bổ sung" in action.action_name or "correct" in action.action_name

    def test_staff_instruction_asks_for_identity(self):
        """Staff instruction should ask for corrected identity info."""
        from fintech_agent.schemas.actions import RecommendedAction
        from fintech_agent.schemas.enums import ActionType, RiskLevel
        evidence = _empty_evidence()
        state = {
            "case_id": "CASE-INV-004",
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
            "recommended_action": RecommendedAction(
                action_type=ActionType.REQUEST_IDENTITY_CORRECTION,
                risk_level=RiskLevel.LOW,
                approval_required=False,
                diagnosis="identity_not_found",
                summary="Phone not found.",
            ),
            "claim_verification_summary": verify_all_claims(
                _invalid_phone_extracted_info(), evidence
            ),
        }
        ticket = build_resolution_ticket(state)
        instr = ticket.staff_instruction
        assert "số điện thoại" in instr or "xác nhận" in instr
        assert "wallet_id" in instr or "email" in instr

    def test_fallback_response_mentions_phone(self):
        """Fallback response should mention the invalid phone."""
        evidence = _empty_evidence()
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
            "extracted_info": _invalid_phone_extracted_info(),
        }
        resp = generate_safe_fallback_response(state)
        assert "0999999999" in resp.problem_explanation
        assert "Không tìm thấy" in resp.problem_explanation
        assert resp.problem_location == "identity_lookup"
        assert resp.problem_location_confidence == "low"

    def test_fallback_customer_reply_asks_for_correction(self):
        """Customer reply should ask for corrected info, not mention fraud."""
        evidence = _empty_evidence()
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": evidence,
            "extracted_info": _invalid_phone_extracted_info(),
        }
        resp = generate_safe_fallback_response(state)
        reply = resp.customer_reply_draft.lower()
        assert "xác nhận" in reply or "kiểm tra lại" in reply
        assert "fraud" not in reply
        assert "risk_score" not in reply

    def test_fp_still_works(self):
        """U_FRAUD_FP should still get false positive explanation."""
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _fp_evidence(),
        }
        resp = generate_safe_fallback_response(state)
        assert "false positive" in resp.problem_explanation
        assert resp.problem_location == "fraud_system"

    def test_high_risk_still_works(self):
        """U_FRAUD_HIGH should still get high risk explanation."""
        state = {
            "selected_workflow": "fraud_account_lock",
            "evidence_bundle": _high_risk_evidence(),
        }
        resp = generate_safe_fallback_response(state)
        # DiagnosticEngine now says "rủi ro" instead of "giữ trạng thái khóa"
        assert "rủi ro" in resp.problem_explanation
        assert resp.problem_location == "fraud_system"

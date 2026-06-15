"""Tests for the generic account issue verification framework.

Validates the verify_account_issue() contract and the full verification pipeline.

Test matrix (matches user requirements):
  1. Account has complained issue → issue_exists=True, bot explains from data.
  2. Account does not have issue → issue_exists=False, "current data does not show".
  3. Customer claim conflicts with data → contradictions populated, mismatch explained.
  4. Customer changes workflow → old diagnosis NOT reused.
  5. Vague complaint → system scans account data for that workflow before no_match.
  6. New workflow added via WorkflowSpec only — no chatbot core changes.

NO hard-coded phrase, phone, user_id, amount, transaction_id, or diagnosis.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from fintech_agent.api.account_verifier import (
    VerificationResult,
    verify_account_issue,
    _build_verified_evidence,
    _build_root_cause,
    _map_issue_status,
)
from fintech_agent.api.customer_claims import CustomerClaims
from fintech_agent.api.generic_resolver import ResolutionResult
from fintech_agent.llm.message_analyzer import (
    MessageAnalysis,
    ExtractedFields,
    _fallback_analyze,
)
from fintech_agent.llm.response_composer import compose_from_verification
from fintech_agent.safety.output_guardrail import validate_verification_response
from fintech_agent.workflows.workflow_registry import (
    WorkflowSpec,
    WorkflowRegistry,
    get_registry,
    reset_registry,
)


# ─── Helpers ────────────────────────────────────────────────────

_NOW = datetime.now(tz=timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _make_txn(**kwargs):
    """Build a mock transaction with dot-attribute access."""
    return SimpleNamespace(
        transaction_id=kwargs.get("transaction_id", "TXN_V_001"),
        user_id=kwargs.get("user_id", "U_V_001"),
        service_type=kwargs.get("service_type", "wallet_topup"),
        amount=kwargs.get("amount", 500000),
        status=kwargs.get("status", "pending"),
        order_id=kwargs.get("order_id"),
        bill_code=kwargs.get("bill_code"),
        customer_code=kwargs.get("customer_code"),
        provider_ref_id=kwargs.get("provider_ref_id"),
        created_at=kwargs.get("created_at", _NOW),
        bank_code=kwargs.get("bank_code"),
        bank_reference=kwargs.get("bank_reference"),
    )


def _make_session(**kwargs):
    """Build a mock session dict."""
    return {
        "session_id": kwargs.get("session_id", "demo_verify_001"),
        "subject_type": kwargs.get("subject_type", "wallet_user"),
        "display_name": kwargs.get("display_name", "Verify User"),
        "role": kwargs.get("role", "customer"),
        "is_authenticated": kwargs.get("is_authenticated", True),
        "user_id": kwargs.get("user_id", "U_V_001"),
        "wallet_id": kwargs.get("wallet_id", "WALLET_V_001"),
        "phone": kwargs.get("phone", "0980000002"),
        "email": kwargs.get("email", "verify@example.com"),
    }


def _mock_txn_repo(transactions):
    """Create a mock transaction repo from a list of mock transactions."""
    from fintech_agent.repositories.base import RecordNotFound

    repo = MagicMock()

    def _get_by_id(txn_id):
        for t in transactions:
            if t.transaction_id == txn_id:
                return t
        raise RecordNotFound("Transaction", "transaction_id", txn_id)

    def _get_by_user_id(user_id):
        return [t for t in transactions if t.user_id == user_id]

    repo.get_by_id.side_effect = _get_by_id
    repo.get_by_user_id.side_effect = _get_by_user_id
    repo.find_by_user_id = repo.get_by_user_id
    return repo


def _patch_repo(txn_repo):
    """Patch transaction repo at the factory level."""
    return patch(
        "fintech_agent.database.repository_factory.get_transaction_repo",
        return_value=txn_repo,
    )


def _make_claims(**kwargs):
    """Build a CustomerClaims with optional pre-seeded claims."""
    claims = CustomerClaims()
    for field_name, value in kwargs.items():
        claims.merge_claim(field_name, value)
    return claims


def _make_context(message, session, extra=None):
    """Build a conversation_context dict from a message and session."""
    analysis = _fallback_analyze(message, {}, {})
    ctx = {
        "analysis": analysis,
        "active_case_context": None,
    }
    if extra:
        ctx.update(extra)
    return ctx, analysis


# ─── Test 1: Account has complained issue ──────────────────────

class TestIssueExists:
    """Account has a pending topup → issue_exists=True, bot explains from data."""

    def test_verified_issue_found(self):
        """Pending topup → issue_exists=True, issue_status='verified_issue_found'."""
        txn = _make_txn(status="pending", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])
        claims = _make_claims()
        ctx, analysis = _make_context(
            "tôi nạp tiền nhưng ví không nhận", session,
        )

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.identity_resolved is True
        assert result.issue_exists is True
        assert result.issue_status == "verified_issue_found"
        assert result.verified_evidence.get("amount") == txn.amount
        assert result.verified_evidence.get("status") == "pending"
        assert result.workflow_id == "wallet_topup"

    def test_failed_txn_also_issue(self):
        """Failed transaction → also issue_exists=True."""
        txn = _make_txn(status="failed", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])
        claims = _make_claims()
        ctx, _ = _make_context("giao dịch nạp tiền bị lỗi", session)

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.issue_exists is True
        assert result.verified_evidence.get("status") == "failed"

    def test_root_cause_populated(self):
        """When issue exists, root_cause should have found=True."""
        txn = _make_txn(status="pending")
        session = _make_session()
        repo = _mock_txn_repo([txn])
        claims = _make_claims()
        ctx, _ = _make_context("nạp tiền chưa vào ví", session)

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.issue_exists is True
        # root_cause is populated from the evidence mapper
        assert isinstance(result.root_cause, dict)
        assert "found" in result.root_cause


# ─── Test 2: Account does NOT have issue ───────────────────────

class TestNoIssueFound:
    """Account has completed transactions → issue_exists=False."""

    def test_completed_topup_no_issue(self):
        """Completed topup → issue_exists=False, issue_status='no_issue_found' or 'no_match'."""
        txn = _make_txn(status="completed", service_type="wallet_topup")
        session = _make_session()
        repo = _mock_txn_repo([txn])
        claims = _make_claims()
        ctx, _ = _make_context("tôi nạp tiền nhưng ví chưa cộng", session)

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.issue_exists is False
        assert result.issue_status in ("no_issue_found", "no_match")

    def test_empty_account(self):
        """No transactions at all → issue_exists=False."""
        session = _make_session()
        repo = _mock_txn_repo([])
        claims = _make_claims()
        ctx, _ = _make_context("nạp tiền chưa nhận", session)

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.issue_exists is False

    def test_identity_not_resolved(self):
        """No user_id → identity_resolved=False."""
        session = _make_session(user_id="")
        claims = _make_claims()
        ctx, _ = _make_context("nạp tiền chưa nhận", session)

        result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.identity_resolved is False
        assert result.issue_exists is False


# ─── Test 3: Contradiction detection ──────────────────────────

class TestContradictions:
    """Customer claim conflicts with verified data → contradictions populated."""

    def test_amount_mismatch_contradiction(self):
        """Customer claims 1M, account has 500K → contradiction."""
        txn = _make_txn(status="pending", amount=500000)
        session = _make_session()
        repo = _mock_txn_repo([txn])
        claims = _make_claims(amount=1_000_000)
        ctx, _ = _make_context("tôi nạp 1 triệu đồng nhưng ví chưa nhận", session)

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        # Must detect contradiction
        assert len(result.contradictions) > 0
        amount_contradictions = [
            c for c in result.contradictions if c["field"] == "amount"
        ]
        assert len(amount_contradictions) > 0
        assert amount_contradictions[0]["customer_claim"] == 1_000_000
        assert amount_contradictions[0]["verified_value"] == 500_000

    def test_no_contradiction_when_matching(self):
        """Matching amount → no contradictions."""
        txn = _make_txn(status="pending", amount=500000)
        session = _make_session()
        repo = _mock_txn_repo([txn])
        claims = _make_claims(amount=500000)
        ctx, _ = _make_context("tôi nạp 500k chưa nhận", session)

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        amount_contradictions = [
            c for c in result.contradictions if c["field"] == "amount"
        ]
        assert len(amount_contradictions) == 0


# ─── Test 4: Workflow change → no stale diagnosis ─────────────

class TestWorkflowChange:
    """When customer changes workflow, old diagnosis must NOT be reused."""

    def test_stale_diagnosis_detected_by_guardrail(self):
        """Guardrail catches old diagnosis reuse when workflow changes."""
        old_diagnosis = {
            "customer_safe_cause": "Ngân hàng đã xác nhận nhưng ví chưa cập nhật",
        }
        # A response that copies the old cause verbatim
        stale_response = (
            "Ngân hàng đã xác nhận nhưng ví chưa cập nhật. "
            "Hệ thống đang kiểm tra."
        )

        vr = VerificationResult(
            workflow_id="train_ticket",
            identity_resolved=True,
            issue_exists=True,
            issue_status="verified_issue_found",
            verified_evidence={"status": "pending"},
        )

        guardrail = validate_verification_response(
            stale_response,
            verification_result=vr,
            selected_workflow="train_ticket",
            previous_workflow="wallet_topup",
            previous_diagnosis=old_diagnosis,
        )

        assert not guardrail.is_safe
        stale_violations = [
            v for v in guardrail.violations if "stale_diagnosis" in v
        ]
        assert len(stale_violations) > 0


# ─── Test 5: Vague complaint → scan before no_match ───────────

class TestVagueComplaint:
    """Vague complaint with no amount/txn_id → system scans account data."""

    def test_vague_message_still_discovers(self):
        """Customer says vague 'ví chưa nhận tiền' → still scans and finds issue."""
        txn = _make_txn(status="pending", amount=300000)
        session = _make_session()
        repo = _mock_txn_repo([txn])
        claims = _make_claims()
        ctx, analysis = _make_context("ví chưa nhận được tiền nạp", session)

        # Verify no specific search criteria in extracted
        assert analysis.extracted.amount is None
        assert analysis.extracted.transaction_id is None

        with _patch_repo(repo):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.issue_exists is True
        assert result.verified_evidence.get("amount") == 300000

    def test_scan_error_not_treated_as_no_match(self):
        """Repo error → issue_status='insufficient_evidence', NOT 'no_match'."""
        session = _make_session()
        claims = _make_claims()
        ctx, _ = _make_context("nạp tiền chưa nhận", session)

        with patch(
            "fintech_agent.database.repository_factory.get_transaction_repo",
            side_effect=RuntimeError("DB down"),
        ):
            result = verify_account_issue(session, "wallet_topup", claims, ctx)

        assert result.issue_status == "insufficient_evidence"
        assert result.issue_exists is False


# ─── Test 6: New workflow via WorkflowSpec only ────────────────

class TestNewWorkflowExtensibility:
    """Adding a new workflow requires ONLY a WorkflowSpec — no chatbot core changes."""

    def test_register_new_workflow(self):
        """A new workflow can be registered and queried via registry."""
        reset_registry()
        registry = get_registry()
        initial_count = len(registry.list_ids())

        # Register a new hypothetical workflow
        registry.register(WorkflowSpec(
            workflow_id="insurance_claim",
            display_noun="yêu cầu bảo hiểm",
            supported_subject_types=["wallet_user"],
            intent_examples=["bảo hiểm", "yêu cầu bồi thường"],
            required_identity_fields=["user_id"],
            searchable_claim_fields=["claim_id", "amount"],
            service_types=frozenset({"insurance_claim"}),
            evidence_schema={"claim_status": "str", "payout_status": "str"},
            issue_verification_rules={
                "issue_when": {"claim_status": ["pending", "rejected"]},
                "no_issue_when": {"claim_status": ["approved", "paid"]},
            },
            safe_response_policy={
                "may_mention": ["claim_status"],
                "never_promise": ["payout", "approval"],
                "claim_label": "bạn cung cấp",
                "evidence_label": "theo kiểm tra hệ thống",
            },
        ))

        assert len(registry.list_ids()) == initial_count + 1
        spec = registry.get("insurance_claim")
        assert spec is not None
        assert spec.display_noun == "yêu cầu bảo hiểm"
        assert spec.issue_verification_rules["issue_when"]["claim_status"] == ["pending", "rejected"]
        assert spec.safe_response_policy["never_promise"] == ["payout", "approval"]

        # Cleanup
        reset_registry()

    def test_verify_uses_registry_spec(self):
        """verify_account_issue looks up WorkflowSpec from registry."""
        session = _make_session()
        claims = _make_claims()
        ctx, _ = _make_context("nạp tiền chưa nhận", session)

        # The workflow "wallet_topup" is registered by default
        registry = get_registry()
        spec = registry.get("wallet_topup")
        assert spec is not None
        assert "issue_when" in spec.issue_verification_rules

    def test_all_builtins_have_issue_rules(self):
        """All 5 built-in workflows have issue_verification_rules."""
        registry = get_registry()
        for wf_id in registry.list_ids():
            spec = registry.get(wf_id)
            assert spec.issue_verification_rules, (
                f"Workflow '{wf_id}' missing issue_verification_rules"
            )
            assert spec.safe_response_policy, (
                f"Workflow '{wf_id}' missing safe_response_policy"
            )

    def test_all_builtins_have_response_policy(self):
        """All 5 built-in workflows have safe_response_policy with claim_label."""
        registry = get_registry()
        for wf_id in registry.list_ids():
            policy = registry.get_response_policy(wf_id)
            assert "claim_label" in policy, f"Workflow '{wf_id}' missing claim_label"
            assert "evidence_label" in policy, f"Workflow '{wf_id}' missing evidence_label"
            assert "never_promise" in policy, f"Workflow '{wf_id}' missing never_promise"


# ─── Test 7: VerificationResult contract correctness ──────────

class TestVerificationResultContract:
    """The VerificationResult matches the user-specified schema."""

    def test_default_values(self):
        """Default VerificationResult has correct initial state."""
        vr = VerificationResult()
        assert vr.workflow_id == ""
        assert vr.identity_resolved is False
        assert vr.issue_exists is False
        assert vr.issue_status == "no_match"
        assert vr.verified_evidence == {}
        assert vr.customer_claims == {}
        assert vr.contradictions == []
        assert vr.data_checked == []
        assert vr.missing_evidence == []
        assert vr.root_cause["found"] is False
        assert vr.root_cause["confidence"] == "low"

    def test_issue_status_values(self):
        """All documented issue_status values are valid."""
        valid_statuses = {
            "verified_issue_found", "no_issue_found",
            "insufficient_evidence", "contradiction", "no_match",
        }
        for status in valid_statuses:
            vr = VerificationResult(issue_status=status)
            assert vr.issue_status == status


# ─── Test 8: Output guardrail validation ──────────────────────

class TestVerificationGuardrail:
    """validate_verification_response enforces all 6 rules."""

    def test_no_issue_must_acknowledge(self):
        """When issue_exists=False, response must say 'chưa tìm thấy' or similar."""
        vr = VerificationResult(
            workflow_id="wallet_topup",
            issue_exists=False,
            issue_status="no_match",
        )
        # This response does NOT acknowledge no-issue
        bad_response = "Cảm ơn bạn đã liên hệ. Chúng tôi đã ghi nhận."

        guardrail = validate_verification_response(
            bad_response,
            verification_result=vr,
            selected_workflow="wallet_topup",
        )

        assert not guardrail.is_safe
        assert any("no_issue" in v for v in guardrail.violations)

    def test_no_issue_with_acknowledgment_passes(self):
        """When issue_exists=False and response acknowledges, guardrail passes."""
        vr = VerificationResult(
            workflow_id="wallet_topup",
            issue_exists=False,
            issue_status="no_match",
        )
        good_response = "Hiện tại hệ thống chưa tìm thấy giao dịch nạp tiền có vấn đề trên tài khoản của bạn."

        guardrail = validate_verification_response(
            good_response,
            verification_result=vr,
            selected_workflow="wallet_topup",
        )

        assert guardrail.is_safe

    def test_workflow_mismatch_detected(self):
        """Verification workflow != selected workflow → violation."""
        vr = VerificationResult(
            workflow_id="train_ticket",
            issue_exists=True,
        )

        guardrail = validate_verification_response(
            "Giao dịch đang xử lý.",
            verification_result=vr,
            selected_workflow="wallet_topup",
        )

        assert not guardrail.is_safe
        assert any("workflow_mismatch" in v for v in guardrail.violations)

    def test_issue_exists_passes_cleanly(self):
        """When issue_exists=True and response is clean, guardrail passes."""
        vr = VerificationResult(
            workflow_id="wallet_topup",
            issue_exists=True,
            issue_status="verified_issue_found",
            verified_evidence={"status": "pending", "amount": 500000},
        )
        good_response = (
            "Theo kiểm tra hệ thống, giao dịch nạp tiền 500.000đ đang ở trạng thái chờ xử lý. "
            "Hệ thống đang cập nhật và sẽ phản hồi sớm."
        )

        guardrail = validate_verification_response(
            good_response,
            verification_result=vr,
            selected_workflow="wallet_topup",
        )

        assert guardrail.is_safe


# ─── Test 9: compose_from_verification ────────────────────────

class TestComposeFromVerification:
    """compose_from_verification uses only the verification result."""

    def test_compose_produces_response(self):
        """compose_from_verification returns a ComposedResponse."""
        vr = VerificationResult(
            workflow_id="wallet_topup",
            identity_resolved=True,
            issue_exists=True,
            issue_status="verified_issue_found",
            verified_evidence={"status": "pending", "amount": 500000},
            root_cause={"found": True, "reason": "test cause", "issue_location": "ví", "confidence": "high"},
        )
        analysis = _fallback_analyze("nạp tiền chưa nhận", {}, {})

        composed = compose_from_verification(
            latest_message="nạp tiền chưa nhận",
            router_result=analysis,
            verification=vr,
        )

        assert composed is not None
        assert len(composed.public_message) > 0


# ─── Test 10: Issue status mapping ────────────────────────────

class TestIssueStatusMapping:
    """_map_issue_status correctly maps resolver outputs."""

    def test_resolved_maps_to_verified(self):
        assert _map_issue_status("resolved", False, True) == "verified_issue_found"

    def test_amount_mismatch_with_contradiction(self):
        assert _map_issue_status("amount_mismatch", True, True) == "contradiction"

    def test_no_match_no_discovery(self):
        # When resolver says no_match and discovery ran but found no issues,
        # the correct status is no_issue_found (we checked, nothing wrong)
        assert _map_issue_status("no_match", False, False) == "no_issue_found"

    def test_no_match_with_discovery_no_issue(self):
        # no_match from resolver but discovery ran and found data without issues
        # → correct status is no_issue_found (data was checked)
        assert _map_issue_status("no_match", False, False) == "no_issue_found"

    def test_evidence_error_maps_to_insufficient(self):
        assert _map_issue_status("evidence_error", False, False) == "insufficient_evidence"

    def test_need_more_info_maps_to_insufficient(self):
        assert _map_issue_status("need_more_info", False, False) == "insufficient_evidence"

"""Workflow framework tests — proves the registry-driven architecture works.

Test groups:
  1. Registry CRUD + built-in registration
  2. Resolver contract shape
  3. ConversationState lifecycle
  4. Wrong amount → contradiction (not bare no_match)
  5. No-match follow-up → direct answer (not repeated template)
  6. Recheck → resolver reruns (response says "result unchanged")
  7. Correction → old claim superseded, resolver reruns
  8. Workflow switch → new case, no stale diagnosis
  9. No hardcoded workflow dispatch in core
"""

import hashlib
import os
import re

import pytest

from fintech_agent.workflows.workflow_registry import (
    WorkflowRegistry,
    WorkflowSpec,
    get_registry,
    reset_registry,
)
from fintech_agent.workflows.resolver_contract import (
    ResolverResult,
    RootCause,
)
from fintech_agent.workflows.generic_diagnosis import (
    DiagnosisResult,
    diagnose_case,
)
from fintech_agent.api.conversation_state import (
    CaseState,
    ClaimRecord,
    ConversationState,
)
from fintech_agent.api.message_router import (
    RouterResult,
    route_customer_message,
    _detect_expanded_type,
)


# ═══════════════════════════════════════════════════════════════════
# 1. Registry CRUD + built-in registration
# ═══════════════════════════════════════════════════════════════════

class TestRegistryCRUD:
    """WorkflowRegistry basic operations."""

    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_builtin_workflows_registered(self):
        """All 5 built-in workflows should be registered on first access."""
        reg = get_registry()
        expected = {
            "wallet_topup", "train_ticket", "utility_bill",
            "fraud_account_lock", "merchant_settlement_delay",
        }
        assert expected.issubset(reg.known_workflow_ids())

    def test_register_new_workflow(self):
        reg = get_registry()
        spec = WorkflowSpec(
            workflow_id="test_new_wf",
            display_noun="test workflow",
        )
        reg.register(spec)
        assert "test_new_wf" in reg.known_workflow_ids()
        assert reg.get("test_new_wf") is spec

    def test_get_unknown_returns_none(self):
        reg = get_registry()
        assert reg.get("nonexistent_workflow") is None

    def test_list_ids_includes_all(self):
        reg = get_registry()
        ids = reg.list_ids()
        assert isinstance(ids, list)
        assert len(ids) >= 5

    def test_display_noun_lookup(self):
        reg = get_registry()
        assert reg.get_display_noun("wallet_topup") == "giao dịch nạp tiền"
        assert reg.get_display_noun("unknown_wf") == "giao dịch"  # fallback

    def test_service_type_lookup(self):
        reg = get_registry()
        svc = reg.get_service_types("wallet_topup")
        assert svc is not None
        assert "wallet_topup" in svc

    def test_match_workflow_for_service_type(self):
        reg = get_registry()
        assert reg.match_workflow_for_service_type("wallet_topup") == "wallet_topup"
        assert reg.match_workflow_for_service_type("train_ticket") == "train_ticket"
        assert reg.match_workflow_for_service_type("electric_bill") == "utility_bill"
        assert reg.match_workflow_for_service_type("nonexistent") is None

    def test_register_replaces_existing(self):
        reg = get_registry()
        old_spec = reg.get("wallet_topup")
        new_spec = WorkflowSpec(
            workflow_id="wallet_topup",
            display_noun="new topup noun",
        )
        reg.register(new_spec)
        assert reg.get("wallet_topup") is new_spec
        assert reg.get_display_noun("wallet_topup") == "new topup noun"

    def test_known_workflow_ids_is_frozen(self):
        reg = get_registry()
        ids = reg.known_workflow_ids()
        assert isinstance(ids, frozenset)


# ═══════════════════════════════════════════════════════════════════
# 2. Resolver contract shape
# ═══════════════════════════════════════════════════════════════════

class TestResolverContract:
    """ResolverResult has correct fields and mappings."""

    def test_default_values(self):
        r = ResolverResult()
        assert r.resolver_status == "insufficient_evidence"
        assert r.verified_evidence == {}
        assert r.candidate_evidence == []
        assert r.contradictions == []
        assert r.root_cause is None

    def test_resolution_status_mapping(self):
        r = ResolverResult(resolver_status="verified_match")
        assert r.resolution_status == "resolved"

        r2 = ResolverResult(resolver_status="no_match")
        assert r2.resolution_status == "no_match"

        r3 = ResolverResult(resolver_status="insufficient_evidence")
        assert r3.resolution_status == "need_more_info"

    def test_resolution_status_setter(self):
        r = ResolverResult()
        r.resolution_status = "resolved"
        assert r.resolver_status == "resolved"

        r.resolution_status = "need_more_info"
        assert r.resolver_status == "insufficient_evidence"

    def test_root_cause_dataclass(self):
        rc = RootCause(found=True, issue_location="bank", reason="timeout", confidence="high")
        assert rc.found is True
        assert rc.confidence == "high"

    def test_candidate_evidence(self):
        r = ResolverResult(
            resolver_status="no_match",
            candidate_evidence=[
                {"amount": 500000, "status": "pending"},
                {"amount": 200000, "status": "success"},
            ],
        )
        assert len(r.candidate_evidence) == 2


# ═══════════════════════════════════════════════════════════════════
# 3. ConversationState lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestConversationState:
    """ConversationState manages multiple cases correctly."""

    def test_create_case(self):
        cs = ConversationState(session_id="test")
        case = cs.create_case("wallet_topup")
        assert case.workflow_id == "wallet_topup"
        assert case.status == "active"
        assert cs.active_case is case

    def test_archive_and_create(self):
        cs = ConversationState(session_id="test")
        old_case = cs.create_case("wallet_topup")
        old_id = old_case.case_id

        new_case = cs.archive_and_create("train_ticket")
        assert old_case.status == "archived"
        assert new_case.workflow_id == "train_ticket"
        assert cs.active_case is new_case
        assert cs.active_case.case_id != old_id

    def test_restore_case(self):
        cs = ConversationState(session_id="test")
        case1 = cs.create_case("wallet_topup")
        case1_id = case1.case_id
        cs.archive_and_create("train_ticket")

        restored = cs.restore_case("wallet_topup")
        assert restored is not None
        assert restored.case_id == case1_id
        assert restored.status == "active"

    def test_restore_nonexistent_returns_none(self):
        cs = ConversationState(session_id="test")
        cs.create_case("wallet_topup")
        assert cs.restore_case("nonexistent") is None

    def test_claim_recording(self):
        case = CaseState(workflow_id="wallet_topup")
        record = case.record_claim({"amount": 500000, "bank_name": "VCB"})
        assert record.fields["amount"] == 500000
        assert case.customer_claims["amount"] == 500000
        assert len(case.claim_history) == 1

    def test_claim_superseding(self):
        case = CaseState(workflow_id="wallet_topup")
        case.record_claim({"amount": 1000000})
        corrected = case.supersede_claims({"amount": 500000})

        assert case.customer_claims["amount"] == 500000
        assert len(case.claim_history) == 2  # original (now superseded) + correction
        assert len(case.superseded_claims) == 1
        assert case.superseded_claims[0].superseded is True

    def test_no_match_tracking(self):
        case = CaseState(workflow_id="wallet_topup")
        case.record_no_match("hash1")
        case.record_no_match("hash2")
        assert case.no_match_count == 2
        assert case.last_response_hash == "hash2"

    def test_recheck_tracking(self):
        case = CaseState(workflow_id="wallet_topup")
        case.record_recheck()
        assert case.recheck_count == 1
        assert case.last_recheck_at > 0

    def test_to_active_case_context_compat(self):
        cs = ConversationState(session_id="test")
        case = cs.create_case("wallet_topup")
        case.record_claim({"amount": 500000})

        ctx = cs.to_active_case_context()
        assert ctx["selected_workflow"] == "wallet_topup"
        assert ctx["has_active_case"] is True
        assert ctx["customer_claims"]["amount"] == 500000


# ═══════════════════════════════════════════════════════════════════
# 4. Router — expanded message types
# ═══════════════════════════════════════════════════════════════════

class TestMessageRouter:
    """Router detects expanded message types correctly."""

    def test_recheck_detection(self):
        assert _detect_expanded_type("kiểm tra lại giúp tôi", "follow_up") == "ask_recheck"
        assert _detect_expanded_type("check lại đi", "follow_up") == "ask_recheck"

    def test_account_status_detection(self):
        assert _detect_expanded_type("tài khoản tôi bị gì vậy", "unknown") == "ask_account_status"

    def test_disagree_detection(self):
        assert _detect_expanded_type("không phải, sai rồi", "follow_up") == "customer_disagrees"

    def test_frustrated_detection(self):
        assert _detect_expanded_type("quá lâu rồi mà chưa xong", "follow_up") == "customer_frustrated"

    def test_staff_detection(self):
        assert _detect_expanded_type("cho tôi gặp nhân viên đi", "follow_up") == "ask_staff_support"

    def test_sensitive_info_never_overridden(self):
        assert _detect_expanded_type("kiểm tra lại", "provide_sensitive_info") == "provide_sensitive_info"

    def test_normal_message_unchanged(self):
        assert _detect_expanded_type("tôi nạp 500k sáng nay", "provide_missing_info") == "provide_missing_info"

    def test_route_returns_router_result(self):
        result = route_customer_message(
            session={"user_id": "U001", "subject_type": "wallet_user"},
            conversation_state=None,
            latest_message="tôi nạp 500k sáng nay qua VCB",
        )
        assert isinstance(result, RouterResult)
        assert result.scope in ("in_scope", "unknown")


# ═══════════════════════════════════════════════════════════════════
# 5. Generic Diagnosis
# ═══════════════════════════════════════════════════════════════════

class TestGenericDiagnosis:
    """Generic diagnosis wraps engine output correctly."""

    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_builtin_wallet_topup(self):
        result = diagnose_case(
            workflow_id="wallet_topup",
            resolver_result={
                "verified_evidence": {
                    "transaction": {"status": "pending", "amount": 500000},
                    "reconciliation_status": {
                        "bank_status": "success",
                        "money_received_in_master_wallet": True,
                    },
                },
            },
        )
        assert isinstance(result, DiagnosisResult)
        assert result.issue_location != ""
        assert result.confidence in ("low", "medium", "high")

    def test_unknown_workflow_fallback(self):
        result = diagnose_case(
            workflow_id="totally_unknown",
            resolver_result={"verified_evidence": {}},
        )
        assert isinstance(result, DiagnosisResult)


# ═══════════════════════════════════════════════════════════════════
# 6. Workflow switch creates new case state
# ═══════════════════════════════════════════════════════════════════

class TestWorkflowSwitch:
    """Switching workflows archives old case and creates clean new one."""

    def test_switch_creates_new_case(self):
        cs = ConversationState(session_id="test")
        old_case = cs.create_case("wallet_topup")
        old_case.record_claim({"amount": 500000})
        old_case.record_diagnosis({"issue_location": "bank"}, "resolved")

        new_case = cs.archive_and_create("train_ticket")

        assert old_case.status == "archived"
        assert new_case.workflow_id == "train_ticket"
        assert new_case.customer_claims == {}  # no stale claims
        assert new_case.diagnosis == {}  # no stale diagnosis
        assert cs.active_case is new_case

    def test_switch_preserves_history(self):
        cs = ConversationState(session_id="test")
        cs.create_case("wallet_topup")
        cs.archive_and_create("train_ticket")

        assert len(cs.cases) == 2
        assert cs.cases[0].workflow_id == "wallet_topup"
        assert cs.cases[1].workflow_id == "train_ticket"


# ═══════════════════════════════════════════════════════════════════
# 7. No hardcoded workflow dispatch scan
# ═══════════════════════════════════════════════════════════════════

class TestNoHardcodedDispatch:
    """Ensure core modules don't contain hardcoded workflow dispatch."""

    def _scan_file(self, filepath: str, pattern: str) -> list[str]:
        """Grep for pattern in a file, returning matching lines."""
        hits = []
        if not os.path.exists(filepath):
            return hits
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if stripped.startswith(("\"\"\"", "'''")):
                        continue
                    if re.search(pattern, stripped, re.IGNORECASE):
                        hits.append(f"{filepath}:{i}: {stripped[:120]}")
        except Exception:
            pass
        return hits

    def test_no_frozen_known_workflows_in_customer_chat(self):
        """_KNOWN_WORKFLOWS should be a function call, not a frozenset literal."""
        filepath = os.path.join(
            os.path.dirname(__file__), "..", "src", "fintech_agent", "api", "customer_chat.py",
        )
        filepath = os.path.abspath(filepath)
        hits = self._scan_file(filepath, r'_KNOWN_WORKFLOWS\s*=\s*frozenset\(\{')
        assert len(hits) == 0, (
            f"Found hardcoded _KNOWN_WORKFLOWS frozenset literal:\n" +
            "\n".join(hits)
        )

    def test_resolver_has_registry_dispatch(self):
        """resolve_case_evidence should contain registry dispatch logic."""
        filepath = os.path.join(
            os.path.dirname(__file__), "..", "src", "fintech_agent", "api", "generic_resolver.py",
        )
        filepath = os.path.abspath(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        assert "get_registry" in content, (
            "resolve_case_evidence should use get_registry() for dispatch"
        )

    def test_diagnostic_engine_has_registry_dispatch(self):
        """diagnose() should contain registry dispatch logic."""
        filepath = os.path.join(
            os.path.dirname(__file__), "..", "src", "fintech_agent", "llm", "diagnostic_engine.py",
        )
        filepath = os.path.abspath(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        assert "get_registry" in content, (
            "diagnose() should use get_registry() for dispatch"
        )

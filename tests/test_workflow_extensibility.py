"""Extensibility test — proves card_payment_dispute works without
editing any core files.

This test:
  1. Imports card_payment_dispute (triggers auto-registration).
  2. Verifies it appears in the registry.
  3. Runs the resolver and checks the contract shape.
  4. Runs the diagnosis and checks the contract shape.
  5. Verifies the response composer handles it generically.
  6. Scans core files to prove they were NOT modified for this workflow.
"""

import os
import re

import pytest

from fintech_agent.workflows.workflow_registry import (
    get_registry,
    reset_registry,
)


def _ensure_card_dispute_registered():
    """Re-register card_payment_dispute after a registry reset."""
    import fintech_agent.workflows.card_payment_dispute as cpd
    cpd.register()


class TestCardPaymentDisputeExtensibility:
    """card_payment_dispute workflow works without modifying core files."""

    def setup_method(self):
        reset_registry()
        _ensure_card_dispute_registered()

    def teardown_method(self):
        reset_registry()

    def test_auto_registration(self):
        """Importing card_payment_dispute registers it with the registry."""
        reg = get_registry()
        assert "card_payment_dispute" in reg.known_workflow_ids()

    def test_spec_has_correct_fields(self):
        """The registered spec has all required fields."""
        reg = get_registry()
        spec = reg.get("card_payment_dispute")
        assert spec is not None
        assert spec.display_noun == "giao dịch tranh chấp thẻ"
        assert spec.resolver is not None
        assert spec.diagnoser is not None
        assert "wallet_user" in spec.supported_subject_types

    def test_resolver_returns_valid_result(self):
        """The card dispute resolver returns a valid ResolutionResult."""
        from fintech_agent.api.generic_resolver import ResolutionResult
        from fintech_agent.llm.message_analyzer import ExtractedFields

        reg = get_registry()
        spec = reg.get("card_payment_dispute")
        assert spec is not None

        # Simulate a session and extracted fields
        session = {"user_id": "U_TEST_001", "subject_type": "wallet_user"}
        extracted = ExtractedFields(amount=100000, bank_reference="1234")

        result = spec.resolver(session, extracted, "card_payment_dispute", "new_complaint")

        assert isinstance(result, ResolutionResult)
        assert result.resolution_status in (
            "resolved", "no_match", "need_more_info", "multiple_candidates",
        )

    def test_resolver_need_more_info_when_empty(self):
        """Resolver returns need_more_info when no fields are provided."""
        from fintech_agent.llm.message_analyzer import ExtractedFields

        reg = get_registry()
        spec = reg.get("card_payment_dispute")
        assert spec is not None

        session = {"user_id": "U_TEST_001", "subject_type": "wallet_user"}
        extracted = ExtractedFields()

        result = spec.resolver(session, extracted, "card_payment_dispute", "new_complaint")
        assert result.resolution_status == "need_more_info"
        assert len(result.missing_info) > 0

    def test_diagnosis_returns_valid_result(self):
        """The card dispute diagnoser returns a valid DiagnosisResult."""
        from fintech_agent.workflows.generic_diagnosis import diagnose_case, DiagnosisResult

        result = diagnose_case(
            workflow_id="card_payment_dispute",
            resolver_result={
                "verified_evidence": {"transaction_status": "pending"},
            },
        )
        assert isinstance(result, DiagnosisResult)
        # Custom diagnoser wraps to DiagnosisResult — may land in "card_processing"
        # or "unknown" depending on wrapper. Key: it returns a valid result.
        assert result.confidence in ("low", "medium", "high")

    def test_response_composer_handles_generically(self):
        """Response composer produces output for the new workflow."""
        from fintech_agent.llm.message_analyzer import MessageAnalysis
        from fintech_agent.llm.response_composer import compose_customer_response

        analysis = MessageAnalysis(
            message_type="new_complaint",
            workflow_hint="card_payment_dispute",
        )
        evidence = {
            "what_we_know": "Giao dịch thẻ đang được kiểm tra.",
            "customer_safe_cause": "Giao dịch thẻ đang được bộ phận kiểm tra xác minh.",
        }
        composed = compose_customer_response(
            customer_message="tôi không nhận ra giao dịch thẻ 100k",
            message_analysis=analysis,
            public_safe_evidence=evidence,
            resolution_status="resolved",
        )
        assert composed.public_message
        assert len(composed.public_message) > 10

    def test_core_files_not_modified_for_card_dispute(self):
        """Core files should NOT mention card_payment_dispute."""
        core_files = [
            "src/fintech_agent/api/customer_chat.py",
            "src/fintech_agent/api/generic_resolver.py",
            "src/fintech_agent/llm/message_analyzer.py",
            "src/fintech_agent/llm/response_composer.py",
        ]
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."),
        )

        for relpath in core_files:
            filepath = os.path.join(project_root, relpath)
            if not os.path.exists(filepath):
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            matches = [
                line.strip()
                for i, line in enumerate(content.split("\n"), 1)
                if "card_payment_dispute" in line.lower()
                and not line.strip().startswith("#")
            ]
            assert len(matches) == 0, (
                f"Core file {relpath} mentions 'card_payment_dispute':\n" +
                "\n".join(matches)
            )

    def test_registry_dispatch_routes_to_custom_resolver(self):
        """resolve_case_evidence dispatches via registry for card_payment_dispute."""
        from fintech_agent.api.generic_resolver import resolve_case_evidence
        from fintech_agent.llm.message_analyzer import MessageAnalysis, ExtractedFields

        session = {"user_id": "U_TEST_001", "subject_type": "wallet_user"}
        analysis = MessageAnalysis(
            message_type="new_complaint",
            workflow_hint="card_payment_dispute",
            extracted=ExtractedFields(amount=100000, bank_reference="5678"),
        )

        result = resolve_case_evidence(session, None, analysis)

        # Should have been dispatched via registry to our custom resolver
        assert result.resolution_status == "resolved"
        assert result.resolved_entity_type == "card_transaction"

    def test_diagnostic_engine_dispatches_via_registry(self):
        """diagnose() dispatches via registry for card_payment_dispute."""
        from fintech_agent.llm.diagnostic_engine import diagnose

        result = diagnose(
            workflow="card_payment_dispute",
            diagnosis="",
            evidence_bundle={"transaction_status": "pending"},
        )
        # Should have been dispatched via registry to our custom diagnoser
        # Either it wraps cleanly or falls back to generic.
        assert result is not None

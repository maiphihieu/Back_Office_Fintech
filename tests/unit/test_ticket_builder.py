"""Tests for the deterministic resolution ticket builder.

Tests that:
- ActionType → TicketAction mapping is correct
- MCP tool names are correct
- resolution_status is computed correctly
- missing_evidence detection works
- Staff instructions are generated
- Safety: no execute actions appear
- Fallback behavior when generated_response is None
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from fintech_agent.llm.ticket_builder import (
    _ACTION_MAP,
    _STAFF_INSTRUCTIONS,
    build_resolution_ticket,
)
from fintech_agent.schemas.resolution_ticket import ResolutionTicket, TicketAction
from fintech_agent.schemas.response_generation import GeneratedResponse, ResponseDebug


def _make_generated_response(**overrides) -> GeneratedResponse:
    """Helper to create a GeneratedResponse with sensible defaults."""
    defaults = {
        "case_summary": "Test case summary",
        "problem_location": "wallet_system",
        "problem_explanation": "Wallet ledger mismatch",
        "evidence_checked": ["transaction", "wallet_ledger"],
        "evidence_supporting_problem_location": ["transaction.status=completed"],
        "problem_location_confidence": "high",
        "internal_summary": "Internal summary for staff",
        "recommended_next_step": "Review draft refund",
        "customer_reply_draft": "Dạ em đã ghi nhận",
        "safety_notes": ["Draft cần phê duyệt"],
        "debug": ResponseDebug(generation_mode="llm", model_used="gpt-4o-mini"),
    }
    defaults.update(overrides)
    return GeneratedResponse(**defaults)


def _make_state(**overrides) -> dict:
    """Helper to create a state dict with sensible defaults."""
    defaults = {
        "case_id": "TEST-001",
        "selected_workflow": "train_ticket",
        "recommended_action": "create_refund_request_draft",
        "risk_level": "medium",
        "approval_required": True,
        "approval_status": None,
        "status": "draft_created",
        "has_conflict": False,
        "evidence_bundle": {
            "transaction": {"id": "TXN_001", "status": "completed"},
            "wallet_ledger": {"status": "debited", "debit_amount": 350000},
        },
    }
    defaults.update(overrides)
    return defaults


class TestActionMapping(unittest.TestCase):
    """Test that ActionType → TicketAction mapping is correct."""

    def test_refund_draft_has_mcp_tool(self):
        state = _make_state(recommended_action="create_refund_request_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(len(ticket.recommended_actions), 1)
        action = ticket.recommended_actions[0]
        self.assertEqual(action.mcp_tool, "create_refund_draft")
        self.assertEqual(action.execution_mode, "draft_only")

    def test_force_success_draft_has_mcp_tool(self):
        state = _make_state(recommended_action="create_force_success_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.mcp_tool, "create_force_success_draft")
        self.assertEqual(action.execution_mode, "draft_only")

    def test_reconciliation_draft_has_mcp_tool(self):
        state = _make_state(recommended_action="create_reconciliation_ticket_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.mcp_tool, "create_reconciliation_draft")
        self.assertEqual(action.execution_mode, "draft_only")

    def test_unlock_account_draft_has_mcp_tool(self):
        state = _make_state(recommended_action="create_unlock_account_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.mcp_tool, "create_unlock_account_draft")
        self.assertEqual(action.execution_mode, "draft_only")

    def test_manual_review_has_no_mcp_tool(self):
        state = _make_state(recommended_action="manual_review")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertIsNone(action.mcp_tool)
        self.assertEqual(action.execution_mode, "manual")

    def test_draft_customer_response_has_mcp_tool(self):
        state = _make_state(recommended_action="draft_customer_response")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.mcp_tool, "create_customer_response_draft")

    def test_all_action_types_have_mapping(self):
        """Every ActionType in _ACTION_MAP has expected keys."""
        for action_type, mapping in _ACTION_MAP.items():
            self.assertIn("action_name", mapping, f"Missing action_name for {action_type}")
            self.assertIn("mcp_tool", mapping, f"Missing mcp_tool for {action_type}")
            self.assertIn("execution_mode", mapping, f"Missing execution_mode for {action_type}")

    def test_all_action_types_have_staff_instruction(self):
        """Every mapped ActionType has a staff instruction template."""
        for action_type in _ACTION_MAP:
            self.assertIn(
                action_type, _STAFF_INSTRUCTIONS,
                f"Missing staff instruction for {action_type}",
            )


class TestResolutionStatus(unittest.TestCase):
    """Test resolution_status computation."""

    def test_actionable_with_mcp_tool(self):
        state = _make_state(recommended_action="create_refund_request_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.resolution_status, "actionable")

    def test_manual_review_required(self):
        state = _make_state(recommended_action="manual_review")
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.resolution_status, "manual_review_required")

    def test_not_supported_when_no_action(self):
        state = _make_state(recommended_action=None)
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.resolution_status, "not_supported")

    def test_manual_review_on_conflict(self):
        state = _make_state(
            recommended_action="create_refund_request_draft",
            has_conflict=True,
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.resolution_status, "manual_review_required")

    def test_manual_review_on_missing_evidence(self):
        """If both transaction and wallet_ledger are missing → manual_review_required."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            evidence_bundle={},
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.resolution_status, "manual_review_required")


class TestActionStatus(unittest.TestCase):
    """Test action status determination."""

    def test_draft_when_no_approval_required(self):
        state = _make_state(
            recommended_action="create_refund_request_draft",
            approval_required=False,
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.recommended_actions[0].status, "draft")

    def test_waiting_approval_when_required(self):
        state = _make_state(
            recommended_action="create_refund_request_draft",
            approval_required=True,
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.recommended_actions[0].status, "waiting_approval")

    def test_manual_required_for_manual_review(self):
        state = _make_state(recommended_action="manual_review")
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.recommended_actions[0].status, "manual_required")


class TestMissingEvidence(unittest.TestCase):
    """Test missing evidence detection."""

    def test_no_missing_when_evidence_complete(self):
        state = _make_state(evidence_bundle={
            "transaction": {"id": "TXN_001"},
            "wallet_ledger": {"status": "debited"},
        })
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.missing_evidence, [])

    def test_missing_transaction(self):
        state = _make_state(evidence_bundle={
            "wallet_ledger": {"status": "debited"},
        })
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertIn("Dữ liệu giao dịch", ticket.missing_evidence)

    def test_missing_wallet_ledger(self):
        state = _make_state(evidence_bundle={
            "transaction": {"id": "TXN_001"},
        })
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertIn("Sổ cái ví", ticket.missing_evidence)

    def test_all_missing_when_no_bundle(self):
        state = _make_state(evidence_bundle=None)
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertTrue(len(ticket.missing_evidence) > 0)


class TestLlmIntegration(unittest.TestCase):
    """Test that LLM response text is correctly pulled into ticket."""

    def test_issue_summary_from_llm(self):
        gr = _make_generated_response(case_summary="Custom summary")
        ticket = build_resolution_ticket(_make_state(), gr)
        self.assertEqual(ticket.issue_summary, "Custom summary")

    def test_customer_reply_from_llm(self):
        gr = _make_generated_response(customer_reply_draft="Custom reply")
        ticket = build_resolution_ticket(_make_state(), gr)
        self.assertEqual(ticket.customer_reply_draft, "Custom reply")

    def test_problem_location_from_llm(self):
        gr = _make_generated_response(problem_location="bank")
        ticket = build_resolution_ticket(_make_state(), gr)
        self.assertEqual(ticket.problem_location, "bank")

    def test_safety_notes_from_llm(self):
        gr = _make_generated_response(safety_notes=["Note 1", "Note 2"])
        ticket = build_resolution_ticket(_make_state(), gr)
        self.assertIn("Note 1", ticket.safety_notes)
        self.assertIn("Note 2", ticket.safety_notes)


class TestFallback(unittest.TestCase):
    """Test fallback when generated_response is None."""

    def test_fallback_has_ticket_id(self):
        ticket = build_resolution_ticket(_make_state(), None)
        self.assertEqual(ticket.ticket_id, "TEST-001")

    def test_fallback_has_default_issue_summary(self):
        ticket = build_resolution_ticket(_make_state(), None)
        self.assertTrue(len(ticket.issue_summary) > 0)

    def test_fallback_has_default_customer_reply(self):
        ticket = build_resolution_ticket(_make_state(), None)
        self.assertTrue(len(ticket.customer_reply_draft) > 0)

    def test_fallback_has_safety_notes(self):
        ticket = build_resolution_ticket(_make_state(), None)
        self.assertTrue(len(ticket.safety_notes) > 0)


class TestSafety(unittest.TestCase):
    """Test safety invariants for resolution tickets."""

    def test_no_execute_actions(self):
        """No action_type should be an 'execute' action."""
        for action_type in _ACTION_MAP:
            self.assertNotIn("execute", action_type.lower(), f"Unsafe action: {action_type}")

    def test_money_actions_are_draft_only(self):
        """All money/account-affecting actions must be draft_only."""
        money_actions = [
            "create_refund_request_draft",
            "create_force_success_draft",
            "create_unlock_account_draft",
        ]
        for action_type in money_actions:
            mapping = _ACTION_MAP[action_type]
            self.assertEqual(
                mapping["execution_mode"], "draft_only",
                f"Money action {action_type} must be draft_only",
            )

    def test_high_risk_adds_safety_note(self):
        state = _make_state(risk_level="high")
        ticket = build_resolution_ticket(state, _make_generated_response())
        risk_notes = [n for n in ticket.safety_notes if "risk" in n.lower() or "Risk" in n]
        self.assertTrue(len(risk_notes) > 0, "High risk should add safety note")

    def test_ticket_type_valid(self):
        state = _make_state(selected_workflow="wallet_topup")
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.ticket_type, "wallet_topup")


class TestPydanticModelExtraction(unittest.TestCase):
    """Test that Pydantic model objects in state are handled correctly."""

    def test_recommended_action_pydantic_model(self):
        """When recommended_action is a RecommendedAction Pydantic model."""
        mock_action = MagicMock()
        mock_action.action_type = MagicMock()
        mock_action.action_type.value = "create_refund_request_draft"
        mock_action.risk_level = MagicMock()
        mock_action.risk_level.value = "medium"
        mock_action.approval_required = True
        mock_action.diagnosis = "provider_not_confirmed"

        state = _make_state(recommended_action=mock_action)
        ticket = build_resolution_ticket(state, _make_generated_response())

        self.assertEqual(len(ticket.recommended_actions), 1)
        self.assertEqual(ticket.recommended_actions[0].action_type, "create_refund_request_draft")
        self.assertEqual(ticket.recommended_actions[0].reason, "provider_not_confirmed")

    def test_evidence_bundle_pydantic_model(self):
        """When evidence_bundle is a Pydantic model with model_dump."""
        mock_bundle = MagicMock()
        mock_bundle.model_dump.return_value = {
            "transaction": {"id": "TXN_001", "status": "completed"},
            "wallet_ledger": {"status": "debited", "debit_amount": 350000},
            "provider_status": {"status": "not_confirmed"},
        }

        state = _make_state(evidence_bundle=mock_bundle)
        ticket = build_resolution_ticket(state, _make_generated_response())

        self.assertIn("Dữ liệu giao dịch", ticket.evidence_checked)
        self.assertIn("Sổ cái ví", ticket.evidence_checked)
        self.assertIn("Trạng thái nhà cung cấp", ticket.evidence_checked)


class TestReturnType(unittest.TestCase):
    """Test that the ticket is always a valid ResolutionTicket."""

    def test_return_type(self):
        ticket = build_resolution_ticket(_make_state(), _make_generated_response())
        self.assertIsInstance(ticket, ResolutionTicket)

    def test_actions_are_ticket_actions(self):
        ticket = build_resolution_ticket(_make_state(), _make_generated_response())
        for action in ticket.recommended_actions:
            self.assertIsInstance(action, TicketAction)

    def test_serializable(self):
        """Ticket should be serializable to dict."""
        ticket = build_resolution_ticket(_make_state(), _make_generated_response())
        data = ticket.model_dump()
        self.assertIsInstance(data, dict)
        self.assertIn("ticket_id", data)
        self.assertIn("recommended_actions", data)
        self.assertIsInstance(data["recommended_actions"], list)


class TestExpandedActionFields(unittest.TestCase):
    """Test the expanded action detail fields."""

    def test_action_id_format(self):
        """action_id should be case_id:action_type."""
        state = _make_state(recommended_action="create_refund_request_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.action_id, "TEST-001:create_refund_request_draft")

    def test_description_populated(self):
        """All mapped actions should have a description."""
        for action_type in _ACTION_MAP:
            state = _make_state(recommended_action=action_type)
            ticket = build_resolution_ticket(state, _make_generated_response())
            action = ticket.recommended_actions[0]
            self.assertTrue(len(action.description) > 0, f"Missing description for {action_type}")

    def test_preconditions_for_force_success(self):
        """Force success should have bank/transaction preconditions."""
        state = _make_state(recommended_action="create_force_success_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertTrue(len(action.preconditions) > 0)
        precondition_text = " ".join(action.preconditions)
        self.assertIn("transaction", precondition_text.lower())

    def test_evidence_dependencies_for_refund(self):
        """Refund action should depend on transaction and wallet_ledger."""
        state = _make_state(recommended_action="create_refund_request_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertIn("transaction", action.evidence_dependencies)
        self.assertIn("wallet_ledger", action.evidence_dependencies)

    def test_mcp_input_for_refund(self):
        """Refund should have mcp_input with transaction_id."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            extracted_info={"transaction_id": "TXN_REFUND_001", "user_id": "USER_001"},
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertIn("transaction_id", action.mcp_input)
        self.assertEqual(action.mcp_input["transaction_id"], "TXN_REFUND_001")

    def test_mcp_input_for_force_success(self):
        """Force success should have mcp_input with transaction_id and reason."""
        state = _make_state(recommended_action="create_force_success_draft")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertIn("transaction_id", action.mcp_input)
        self.assertIn("reason", action.mcp_input)

    def test_mcp_input_empty_for_manual_review(self):
        """Manual review should have no mcp_input."""
        state = _make_state(recommended_action="manual_review")
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.mcp_input, {})

    def test_expected_result_populated(self):
        """All mapped actions should have expected_result."""
        for action_type in _ACTION_MAP:
            state = _make_state(recommended_action=action_type)
            ticket = build_resolution_ticket(state, _make_generated_response())
            action = ticket.recommended_actions[0]
            self.assertTrue(
                len(action.expected_result) > 0,
                f"Missing expected_result for {action_type}",
            )

    def test_action_safety_notes_for_money_actions(self):
        """Money actions should have safety notes at the action level."""
        money_actions = [
            "create_refund_request_draft",
            "create_force_success_draft",
            "create_unlock_account_draft",
        ]
        for action_type in money_actions:
            state = _make_state(recommended_action=action_type)
            ticket = build_resolution_ticket(state, _make_generated_response())
            action = ticket.recommended_actions[0]
            self.assertTrue(
                len(action.safety_notes) > 0,
                f"Missing safety_notes for {action_type}",
            )

    def test_action_staff_instruction_populated(self):
        """All mapped actions should have a per-action staff_instruction."""
        for action_type in _ACTION_MAP:
            state = _make_state(recommended_action=action_type)
            ticket = build_resolution_ticket(state, _make_generated_response())
            action = ticket.recommended_actions[0]
            self.assertTrue(
                len(action.staff_instruction) > 0,
                f"Missing staff_instruction for {action_type}",
            )

    def test_risk_level_propagated(self):
        """risk_level should be propagated to each action."""
        state = _make_state(
            recommended_action="create_force_success_draft",
            risk_level="high",
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.risk_level, "high")

    def test_approval_status_pending(self):
        """When approval_required and no decision, approval_status should be pending."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            approval_required=True,
            approval_status=None,
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.approval_status, "pending")

    def test_approval_status_not_required(self):
        """When approval_required is False, approval_status should be not_required."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            approval_required=False,
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.approval_status, "not_required")

    def test_approval_status_approved(self):
        """When approval_status is approved, action should reflect it."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            approval_required=True,
            approval_status="approved",
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.approval_status, "approved")

    def test_mcp_input_refund_includes_amount(self):
        """Refund mcp_input should include amount from wallet_ledger."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 250000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertEqual(action.mcp_input.get("amount"), 250000)

    def test_all_actions_have_all_detail_fields(self):
        """Every _ACTION_MAP entry should have all detail keys."""
        required_keys = {
            "action_name", "description", "mcp_tool", "execution_mode",
            "preconditions", "evidence_dependencies", "expected_result", "safety_notes",
        }
        for action_type, mapping in _ACTION_MAP.items():
            for key in required_keys:
                self.assertIn(key, mapping, f"Missing {key} for {action_type}")

    def test_serialized_action_has_all_fields(self):
        """Serialized action dict should contain all expanded fields."""
        ticket = build_resolution_ticket(_make_state(), _make_generated_response())
        data = ticket.model_dump()
        action_data = data["recommended_actions"][0]
        expected_fields = [
            "action_id", "action_name", "action_type", "description",
            "mcp_tool", "mcp_input", "preconditions", "evidence_dependencies",
            "requires_approval", "approval_status", "execution_mode",
            "risk_level", "reason", "status", "expected_result",
            "safety_notes", "staff_instruction",
        ]
        for field in expected_fields:
            self.assertIn(field, action_data, f"Missing field in serialized action: {field}")


class TestAmountVerification(unittest.TestCase):
    """Test amount verification: trusted vs customer-claimed amounts."""

    def test_matching_amounts_no_mismatch(self):
        """When claimed == system amount, has_amount_mismatch = False."""
        state = _make_state(
            extracted_info={"amount_claimed": 350000},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 350000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertIsNotNone(ticket.amount_verification)
        av = ticket.amount_verification
        self.assertFalse(av.has_amount_mismatch)
        self.assertEqual(av.customer_claimed_amount, 350000)
        self.assertEqual(av.trusted_amount, 350000)

    def test_mismatching_amounts_creates_mismatch(self):
        """When claimed != system amount, has_amount_mismatch = True."""
        state = _make_state(
            extracted_info={"amount_claimed": 4500000},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 450000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        av = ticket.amount_verification
        self.assertTrue(av.has_amount_mismatch)
        self.assertEqual(av.customer_claimed_amount, 4500000)
        self.assertEqual(av.trusted_amount, 450000)
        self.assertEqual(av.trusted_amount_source, "wallet_ledger.debit_amount")
        self.assertIn("4,500,000", av.mismatch_description)
        self.assertIn("450,000", av.mismatch_description)

    def test_refund_amount_uses_wallet_ledger_never_complaint(self):
        """Refund mcp_input.amount must use wallet_ledger.debit_amount."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            extracted_info={"amount_claimed": 9999999, "transaction_id": "TXN_001"},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 350000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        # Amount must come from wallet ledger, NOT from complaint
        self.assertEqual(action.mcp_input.get("amount"), 350000)
        self.assertEqual(action.mcp_input.get("amount_source"), "wallet_ledger.debit_amount")

    def test_force_success_no_complaint_amount_in_mcp_input(self):
        """Force success mcp_input should not contain complaint amount."""
        state = _make_state(
            recommended_action="create_force_success_draft",
            extracted_info={"amount_claimed": 9999999, "transaction_id": "TXN_001"},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 350000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        # Force success should not have a customer amount
        self.assertNotEqual(action.mcp_input.get("amount"), 9999999)

    def test_amount_mismatch_forces_manual_review_for_money_action(self):
        """Money action + mismatch -> resolution_status = manual_review_required."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            extracted_info={"amount_claimed": 4500000},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 450000},
            },
            has_conflict=False,
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        self.assertEqual(ticket.resolution_status, "manual_review_required")

    def test_no_claimed_amount_no_mismatch(self):
        """If no amount_claimed in extracted_info, no mismatch."""
        state = _make_state(
            extracted_info={},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 350000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        av = ticket.amount_verification
        self.assertFalse(av.has_amount_mismatch)
        self.assertIsNone(av.customer_claimed_amount)

    def test_mismatch_adds_safety_note(self):
        """Amount mismatch should add a safety note to the ticket."""
        state = _make_state(
            extracted_info={"amount_claimed": 4500000},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 450000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        mismatch_notes = [n for n in ticket.safety_notes if "Chênh lệch" in n]
        self.assertTrue(len(mismatch_notes) > 0, "Missing mismatch safety note")

    def test_trusted_amount_fallback_to_transaction(self):
        """If no wallet_ledger, fall back to transaction.amount."""
        state = _make_state(
            extracted_info={"amount_claimed": 500000},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed", "amount": 500000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        av = ticket.amount_verification
        self.assertEqual(av.trusted_amount, 500000)
        self.assertEqual(av.trusted_amount_source, "transaction.amount")
        self.assertFalse(av.has_amount_mismatch)

    def test_mcp_input_refund_has_amount_source(self):
        """Refund mcp_input must include amount_source field."""
        state = _make_state(
            recommended_action="create_refund_request_draft",
            extracted_info={"transaction_id": "TXN_001"},
            evidence_bundle={
                "transaction": {"id": "TXN_001", "status": "completed"},
                "wallet_ledger": {"status": "debited", "debit_amount": 200000},
            },
        )
        ticket = build_resolution_ticket(state, _make_generated_response())
        action = ticket.recommended_actions[0]
        self.assertIn("amount_source", action.mcp_input)
        self.assertEqual(action.mcp_input["amount_source"], "wallet_ledger.debit_amount")


if __name__ == "__main__":
    unittest.main()


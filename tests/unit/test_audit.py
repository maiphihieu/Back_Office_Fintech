"""Unit tests for the audit logging layer.

Covers:
  1. Basic event logging and retrieval
  2. Query by case_id, event_type, correlation_id
  3. Convenience helpers (case_received, tool_called, etc.)
  4. PII masking in details
  5. File persistence (JSONL)
  6. State transition logging
  7. Safety blocked logging
"""

import json
from pathlib import Path

import pytest

from fintech_agent.audit import AuditLogger
from fintech_agent.schemas.audit import AuditEvent
from fintech_agent.schemas.enums import AuditEventType


# ═══════════════════════════════════════════════════════════
#  1. Basic event logging
# ═══════════════════════════════════════════════════════════


class TestAuditLoggerBasic:
    def setup_method(self) -> None:
        self.logger = AuditLogger()

    def test_log_event_returns_audit_event(self) -> None:
        event = self.logger.log_event(
            "CASE_001",
            AuditEventType.CASE_RECEIVED,
            actor="system",
        )
        assert isinstance(event, AuditEvent)
        assert event.case_id == "CASE_001"
        assert event.event_type == AuditEventType.CASE_RECEIVED
        assert event.actor == "system"
        assert event.event_id  # auto-generated

    def test_log_event_with_details(self) -> None:
        event = self.logger.log_event(
            "CASE_001",
            AuditEventType.TOOL_CALLED,
            details={"tool": "get_transaction", "txn_id": "TXN_001"},
        )
        assert event.details["tool"] == "get_transaction"

    def test_log_event_with_status_transition(self) -> None:
        event = self.logger.log_event(
            "CASE_001",
            AuditEventType.WORKFLOW_ROUTED,
            previous_status="extracting",
            new_status="fetching_evidence",
        )
        assert event.previous_status == "extracting"
        assert event.new_status == "fetching_evidence"

    def test_log_event_with_correlation_id(self) -> None:
        corr_id = AuditLogger.generate_correlation_id()
        event = self.logger.log_event(
            "CASE_001",
            AuditEventType.TOOL_CALLED,
            correlation_id=corr_id,
        )
        assert event.correlation_id == corr_id

    def test_event_count(self) -> None:
        assert self.logger.event_count == 0
        self.logger.log_event("CASE_001", AuditEventType.CASE_RECEIVED, actor="system")
        self.logger.log_event("CASE_001", AuditEventType.INFO_EXTRACTED)
        assert self.logger.event_count == 2

    def test_default_actor_is_agent(self) -> None:
        event = self.logger.log_event("CASE_001", AuditEventType.TOOL_CALLED)
        assert event.actor == "agent"


# ═══════════════════════════════════════════════════════════
#  2. Query operations
# ═══════════════════════════════════════════════════════════


class TestAuditLoggerQuery:
    def setup_method(self) -> None:
        self.logger = AuditLogger()
        self.logger.log_event("CASE_001", AuditEventType.CASE_RECEIVED, actor="system")
        self.logger.log_event("CASE_001", AuditEventType.TOOL_CALLED)
        self.logger.log_event("CASE_001", AuditEventType.TOOL_CALLED)
        self.logger.log_event("CASE_002", AuditEventType.CASE_RECEIVED, actor="system")

    def test_get_by_case_id(self) -> None:
        events = self.logger.get_events_by_case("CASE_001")
        assert len(events) == 3
        assert all(e.case_id == "CASE_001" for e in events)

    def test_get_by_case_id_empty(self) -> None:
        events = self.logger.get_events_by_case("CASE_999")
        assert events == []

    def test_get_by_type(self) -> None:
        events = self.logger.get_events_by_type("CASE_001", AuditEventType.TOOL_CALLED)
        assert len(events) == 2

    def test_get_by_correlation_id(self) -> None:
        corr_id = "corr_abc123"
        self.logger.log_event(
            "CASE_001", AuditEventType.TOOL_CALLED, correlation_id=corr_id
        )
        self.logger.log_event(
            "CASE_001", AuditEventType.TOOL_RESULT_RECEIVED, correlation_id=corr_id
        )
        events = self.logger.get_events_by_correlation(corr_id)
        assert len(events) == 2

    def test_get_all_events(self) -> None:
        assert len(self.logger.get_all_events()) == 4


# ═══════════════════════════════════════════════════════════
#  3. Convenience helpers
# ═══════════════════════════════════════════════════════════


class TestAuditLoggerHelpers:
    def setup_method(self) -> None:
        self.logger = AuditLogger()

    def test_log_case_received(self) -> None:
        event = self.logger.log_case_received(
            "CASE_001", "Tôi mua vé tàu nhưng chưa nhận được vé"
        )
        assert event.event_type == AuditEventType.CASE_RECEIVED
        assert event.actor == "system"
        assert event.new_status == "new"
        assert event.details["raw_complaint_length"] > 0

    def test_log_case_received_no_pii_in_complaint(self) -> None:
        """Complaint text is NOT stored directly — only length."""
        event = self.logger.log_case_received(
            "CASE_001", "SDT 0987654321, email user@test.com"
        )
        # Raw complaint should NOT be in details
        assert "0987654321" not in str(event.details)
        assert "user@test.com" not in str(event.details)

    def test_log_tool_called(self) -> None:
        event = self.logger.log_tool_called(
            "CASE_001",
            "get_transaction",
            args={"transaction_id": "TXN_001"},
        )
        assert event.event_type == AuditEventType.TOOL_CALLED
        assert event.details["tool"] == "get_transaction"

    def test_log_tool_result_success(self) -> None:
        event = self.logger.log_tool_result(
            "CASE_001", "get_transaction", success=True, result_summary="found"
        )
        assert event.event_type == AuditEventType.TOOL_RESULT_RECEIVED
        assert event.details["success"] is True

    def test_log_tool_result_failure(self) -> None:
        event = self.logger.log_tool_result(
            "CASE_001", "get_transaction", success=False, result_summary="timeout"
        )
        assert event.event_type == AuditEventType.TOOL_FAILED
        assert event.details["success"] is False

    def test_log_state_transition_closed(self) -> None:
        event = self.logger.log_state_transition(
            "CASE_001", "draft_created", "closed", reason="draft delivered"
        )
        assert event.event_type == AuditEventType.CASE_CLOSED
        assert event.previous_status == "draft_created"
        assert event.new_status == "closed"

    def test_log_state_transition_reopened(self) -> None:
        event = self.logger.log_state_transition(
            "CASE_001", "closed", "reopened", reason="customer disputed"
        )
        assert event.event_type == AuditEventType.CASE_REOPENED

    def test_log_state_transition_conflict(self) -> None:
        event = self.logger.log_state_transition(
            "CASE_001", "fetching_evidence", "conflict_detected"
        )
        assert event.event_type == AuditEventType.CONFLICT_DETECTED

    def test_log_state_transition_generic(self) -> None:
        event = self.logger.log_state_transition(
            "CASE_001", "new", "extracting"
        )
        assert event.event_type == AuditEventType.WORKFLOW_ROUTED

    def test_log_action_recommended(self) -> None:
        event = self.logger.log_action_recommended(
            "CASE_001",
            action_type="create_refund_request_draft",
            diagnosis="wallet_debited_ticket_not_issued",
            approval_required=True,
        )
        assert event.event_type == AuditEventType.ACTION_RECOMMENDED
        assert event.details["action_type"] == "create_refund_request_draft"
        assert event.details["approval_required"] is True

    def test_log_safety_blocked(self) -> None:
        event = self.logger.log_safety_blocked(
            "CASE_001", action="execute_refund", reason="forbidden action"
        )
        assert event.event_type == AuditEventType.SAFETY_BLOCKED
        assert event.actor == "system"
        assert event.details["blocked_action"] == "execute_refund"


# ═══════════════════════════════════════════════════════════
#  4. PII masking
# ═══════════════════════════════════════════════════════════


class TestAuditPIIMasking:
    def test_masks_phone_in_details(self) -> None:
        logger = AuditLogger(mask_pii_in_details=True)
        event = logger.log_event(
            "CASE_001",
            AuditEventType.CASE_RECEIVED,
            actor="system",
            details={"customer_info": "SDT: 0987654321"},
        )
        assert "0987654321" not in event.details["customer_info"]
        assert "[MASKED_PHONE]" in event.details["customer_info"]

    def test_masks_email_in_details(self) -> None:
        logger = AuditLogger(mask_pii_in_details=True)
        event = logger.log_event(
            "CASE_001",
            AuditEventType.CASE_RECEIVED,
            actor="system",
            details={"contact": "email: user@example.com"},
        )
        assert "user@example.com" not in event.details["contact"]

    def test_masks_nested_dict(self) -> None:
        logger = AuditLogger(mask_pii_in_details=True)
        event = logger.log_event(
            "CASE_001",
            AuditEventType.INFO_EXTRACTED,
            details={"extracted": {"phone": "0987654321"}},
        )
        assert "0987654321" not in str(event.details)

    def test_no_masking_when_disabled(self) -> None:
        logger = AuditLogger(mask_pii_in_details=False)
        event = logger.log_event(
            "CASE_001",
            AuditEventType.CASE_RECEIVED,
            actor="system",
            details={"phone": "0987654321"},
        )
        assert event.details["phone"] == "0987654321"

    def test_non_string_values_unchanged(self) -> None:
        logger = AuditLogger(mask_pii_in_details=True)
        event = logger.log_event(
            "CASE_001",
            AuditEventType.TOOL_CALLED,
            details={"amount": 450000, "success": True},
        )
        assert event.details["amount"] == 450000
        assert event.details["success"] is True


# ═══════════════════════════════════════════════════════════
#  5. File persistence
# ═══════════════════════════════════════════════════════════


class TestAuditLoggerFile:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        log_file = tmp_path / "audit.jsonl"
        logger = AuditLogger(log_file=log_file)
        logger.log_event("CASE_001", AuditEventType.CASE_RECEIVED, actor="system")
        logger.log_event("CASE_001", AuditEventType.TOOL_CALLED)

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

        # Each line is valid JSON
        for line in lines:
            data = json.loads(line)
            assert data["case_id"] == "CASE_001"
            assert "event_id" in data
            assert "timestamp" in data

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "audit.jsonl"
        logger1 = AuditLogger(log_file=log_file)
        logger1.log_event("CASE_001", AuditEventType.CASE_RECEIVED, actor="system")

        logger2 = AuditLogger(log_file=log_file)
        logger2.log_event("CASE_002", AuditEventType.CASE_RECEIVED, actor="system")

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        log_file = tmp_path / "sub" / "dir" / "audit.jsonl"
        logger = AuditLogger(log_file=log_file)
        logger.log_event("CASE_001", AuditEventType.CASE_RECEIVED, actor="system")
        assert log_file.exists()

    def test_no_file_when_not_configured(self) -> None:
        """Default: no file output."""
        logger = AuditLogger()
        logger.log_event("CASE_001", AuditEventType.CASE_RECEIVED, actor="system")
        # Should not raise, just store in memory


# ═══════════════════════════════════════════════════════════
#  6. Correlation ID
# ═══════════════════════════════════════════════════════════


class TestCorrelationId:
    def test_generate_correlation_id_format(self) -> None:
        corr_id = AuditLogger.generate_correlation_id()
        assert len(corr_id) == 12
        assert isinstance(corr_id, str)

    def test_generate_unique_ids(self) -> None:
        ids = {AuditLogger.generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100  # all unique


# ═══════════════════════════════════════════════════════════
#  7. Event types completeness
# ═══════════════════════════════════════════════════════════


class TestEventTypesComplete:
    """Verify all required event types exist in the enum."""

    def test_all_required_event_types(self) -> None:
        required = {
            "case_received",
            "info_extracted",
            "missing_info_detected",
            "evidence_fetch_started",
            "tool_called",
            "tool_result_received",
            "tool_failed",
            "retry_scheduled",
            "dead_letter_created",
            "conflict_detected",
            "workflow_routed",
            "rule_applied",
            "action_recommended",
            "approval_requested",
            "human_approved",
            "human_rejected",
            "draft_created",
            "case_closed",
            "case_reopened",
            "safety_blocked",
        }
        actual = {e.value for e in AuditEventType}
        assert required.issubset(actual), f"Missing: {required - actual}"

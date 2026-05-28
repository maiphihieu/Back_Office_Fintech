"""Audit logger — structured event logging for the case lifecycle.

Provides:
  - In-memory event store (queryable by case_id)
  - Optional file-based persistence (JSONL format)
  - Convenience helpers for common event types
  - PII masking before writing details

Usage:
    from fintech_agent.audit import AuditLogger

    logger = AuditLogger()
    logger.log_event("CASE_001", AuditEventType.CASE_RECEIVED, actor="system",
                     details={"raw_complaint": "..."})

    events = logger.get_events_by_case("CASE_001")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

from fintech_agent.safety.pii_masking import mask_pii
from fintech_agent.schemas.audit import AuditEvent
from fintech_agent.schemas.enums import AuditEventType

_python_logger = logging.getLogger("fintech_agent.audit")


class AuditLogger:
    """Structured audit logger with in-memory store and optional file sink.

    Args:
        log_file: Optional path to a JSONL file for persistent audit logs.
                  If None, events are only stored in memory.
        mask_pii_in_details: If True, mask PII in detail string values.
    """

    def __init__(
        self,
        log_file: Path | str | None = None,
        mask_pii_in_details: bool = True,
    ) -> None:
        self._events: list[AuditEvent] = []
        self._log_file = Path(log_file) if log_file else None
        self._mask_pii = mask_pii_in_details

        if self._log_file:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)

    # ─── Core logging ──────────────────────────────────────

    def log_event(
        self,
        case_id: str,
        event_type: AuditEventType,
        *,
        actor: str = "agent",
        details: dict | None = None,
        previous_status: str | None = None,
        new_status: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Log an audit event.

        Args:
            case_id: The case this event belongs to.
            event_type: Type of event (from AuditEventType enum).
            actor: Who triggered this event.
            details: Arbitrary dict with event-specific data.
            previous_status: Case status before (for state transitions).
            new_status: Case status after.
            correlation_id: Links related events in a single run.

        Returns:
            The created AuditEvent.
        """
        safe_details = self._sanitize_details(details or {})

        event = AuditEvent(
            case_id=case_id,
            event_type=event_type,
            actor=actor,
            details=safe_details,
            previous_status=previous_status,
            new_status=new_status,
            correlation_id=correlation_id,
        )

        self._events.append(event)
        self._write_to_file(event)
        _python_logger.info(
            "audit: case=%s type=%s actor=%s",
            case_id,
            event_type.value,
            actor,
        )

        return event

    # ─── Query ──────────────────────────────────────────────

    def get_events_by_case(self, case_id: str) -> list[AuditEvent]:
        """Return all audit events for a case, ordered by timestamp."""
        return [e for e in self._events if e.case_id == case_id]

    def get_events_by_type(
        self, case_id: str, event_type: AuditEventType
    ) -> list[AuditEvent]:
        """Return events of a specific type for a case."""
        return [
            e
            for e in self._events
            if e.case_id == case_id and e.event_type == event_type
        ]

    def get_events_by_correlation(self, correlation_id: str) -> list[AuditEvent]:
        """Return all events sharing a correlation_id."""
        return [e for e in self._events if e.correlation_id == correlation_id]

    def get_all_events(self) -> list[AuditEvent]:
        """Return all events (for admin/debug)."""
        return list(self._events)

    @property
    def event_count(self) -> int:
        """Total number of logged events."""
        return len(self._events)

    # ─── Convenience helpers ────────────────────────────────

    def log_case_received(
        self, case_id: str, raw_complaint: str, **extra: object
    ) -> AuditEvent:
        """Log case received event."""
        return self.log_event(
            case_id,
            AuditEventType.CASE_RECEIVED,
            actor="system",
            details={"raw_complaint_length": len(raw_complaint), **extra},
            new_status="new",
        )

    def log_tool_called(
        self,
        case_id: str,
        tool_name: str,
        args: dict | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Log a tool invocation."""
        return self.log_event(
            case_id,
            AuditEventType.TOOL_CALLED,
            details={"tool": tool_name, "args": args or {}},
            correlation_id=correlation_id,
        )

    def log_tool_result(
        self,
        case_id: str,
        tool_name: str,
        success: bool,
        result_summary: str = "",
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Log a tool result (success or failure)."""
        event_type = (
            AuditEventType.TOOL_RESULT_RECEIVED
            if success
            else AuditEventType.TOOL_FAILED
        )
        return self.log_event(
            case_id,
            event_type,
            details={
                "tool": tool_name,
                "success": success,
                "result_summary": result_summary,
            },
            correlation_id=correlation_id,
        )

    def log_state_transition(
        self,
        case_id: str,
        previous_status: str,
        new_status: str,
        reason: str = "",
    ) -> AuditEvent:
        """Log a case state transition."""
        event_type_map = {
            "closed": AuditEventType.CASE_CLOSED,
            "reopened": AuditEventType.CASE_REOPENED,
            "conflict_detected": AuditEventType.CONFLICT_DETECTED,
            "dead_letter": AuditEventType.DEAD_LETTER_CREATED,
        }
        event_type = event_type_map.get(
            new_status, AuditEventType.WORKFLOW_ROUTED
        )

        return self.log_event(
            case_id,
            event_type,
            previous_status=previous_status,
            new_status=new_status,
            details={"reason": reason} if reason else {},
        )

    def log_action_recommended(
        self,
        case_id: str,
        action_type: str,
        diagnosis: str,
        approval_required: bool,
    ) -> AuditEvent:
        """Log a recommended action from the rule engine."""
        return self.log_event(
            case_id,
            AuditEventType.ACTION_RECOMMENDED,
            details={
                "action_type": action_type,
                "diagnosis": diagnosis,
                "approval_required": approval_required,
            },
        )

    def log_safety_blocked(
        self,
        case_id: str,
        action: str,
        reason: str,
    ) -> AuditEvent:
        """Log a safety violation block."""
        return self.log_event(
            case_id,
            AuditEventType.SAFETY_BLOCKED,
            actor="system",
            details={"blocked_action": action, "reason": reason},
        )

    # ─── Internals ──────────────────────────────────────────

    def _sanitize_details(self, details: dict) -> dict:
        """Mask PII in string values within details dict."""
        if not self._mask_pii:
            return details
        sanitized = {}
        for key, value in details.items():
            if isinstance(value, str):
                sanitized[key] = mask_pii(value)
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_details(value)
            else:
                sanitized[key] = value
        return sanitized

    def _write_to_file(self, event: AuditEvent) -> None:
        """Append event as JSON line to file (if configured)."""
        if self._log_file is None:
            return
        line = event.model_dump_json() + "\n"
        with self._log_file.open("a", encoding="utf-8") as f:
            f.write(line)

    @staticmethod
    def generate_correlation_id() -> str:
        """Generate a new correlation ID for linking related events."""
        return str(uuid4())[:12]

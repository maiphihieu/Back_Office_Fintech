"""Audit event model — every action in the case lifecycle must be logged.

Audit events are immutable records. Once written, they cannot be modified.
PII in details should be masked before logging (see safety/pii_masking.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from fintech_agent.schemas.enums import AuditEventType


class AuditEvent(BaseModel):
    """Immutable audit log entry.

    Each event records who did what, when, and why.
    Actors can be: "agent", "system", or "human:<operator_id>".

    Fields:
        correlation_id: Links related events across a single case run.
                        Useful for tracing a complete workflow execution.
        previous_status: Case status before this event (for state transitions).
        new_status: Case status after this event.
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str = Field(..., min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str = Field(
        ...,
        min_length=1,
        description='Who: "agent", "system", or "human:ops_senior_xxx"',
    )
    event_type: AuditEventType
    previous_status: str | None = None
    new_status: str | None = None
    correlation_id: str | None = None
    details: dict = Field(default_factory=dict)

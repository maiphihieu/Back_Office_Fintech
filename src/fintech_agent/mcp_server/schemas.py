"""MCP tool handler schemas — input/output models for MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class DraftOutput:
    """Standard output for draft-creating tools."""

    draft_id: str
    type: str
    case_id: str
    transaction_id: str | None = None
    status: str = "pending_approval"
    approval_required: bool = True
    amount: int | None = None
    user_id: str | None = None
    reason: str | None = None
    message: str | None = None
    evidence_summary: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    note: str = "Draft only. Human approval required before any money-impacting action."

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


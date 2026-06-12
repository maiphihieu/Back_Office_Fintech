"""Reusable conversation state — tracks multi-case chat sessions.

Replaces the flat ``ActiveCaseContext`` with a richer state machine that
supports:
  - Multiple cases per conversation (archived/active/switched)
  - Claim history + superseded claims (for ``correct_previous_info``)
  - Resolver/diagnosis snapshots (to detect "recheck — result unchanged")
  - Workflow switching (archive old, create new, preserve transcript)

IMPORTANT: This module has NO persistence — state lives in-memory for the
duration of a request session. Ticket persistence is out of scope.
"""

from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ClaimRecord:
    """Snapshot of customer-provided claims at a point in time."""

    timestamp: float = field(default_factory=time.time)
    fields: dict[str, Any] = field(default_factory=dict)
    superseded: bool = False
    superseded_by: str = ""  # claim_id that replaced this one
    claim_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class CaseState:
    """Full state for a single complaint case within a conversation."""

    case_id: str = field(default_factory=lambda: f"CASE_{uuid.uuid4().hex[:8].upper()}")
    workflow_id: str = ""

    # Claims
    customer_claims: dict[str, Any] = field(default_factory=dict)
    claim_history: list[ClaimRecord] = field(default_factory=list)
    superseded_claims: list[ClaimRecord] = field(default_factory=list)

    # Resolver
    resolver_result: Any = None
    verified_evidence: dict[str, Any] = field(default_factory=dict)

    # Diagnosis
    diagnosis: dict[str, Any] = field(default_factory=dict)
    resolution_status: str = ""
    last_lookup_summary: str = ""

    # Status
    status: str = "active"  # active | archived | switched
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    message_count: int = 0

    # No-match tracking
    no_match_count: int = 0
    last_response_hash: str = ""

    # Recheck tracking
    last_recheck_at: float = 0.0
    recheck_count: int = 0

    def record_claim(self, fields: dict[str, Any]) -> ClaimRecord:
        """Record customer claims, returning the new ClaimRecord."""
        record = ClaimRecord(fields=dict(fields))
        self.claim_history.append(record)
        # Merge into current claims
        for k, v in fields.items():
            if v is not None and v != "":
                self.customer_claims[k] = v
        self.last_updated = time.time()
        return record

    def supersede_claims(self, correction_fields: dict[str, Any]) -> ClaimRecord:
        """Mark current claims as superseded and record corrected claims.

        Called when the customer says "à tôi nhầm" or provides a correction.
        """
        # Archive current claims as superseded
        if self.claim_history:
            old_record = self.claim_history[-1]
            old_record.superseded = True
            self.superseded_claims.append(old_record)

        # Record corrected claims
        new_record = ClaimRecord(fields=dict(correction_fields))
        if self.claim_history:
            new_record.superseded_by = ""  # This IS the current record
            self.claim_history[-1].superseded_by = new_record.claim_id

        self.claim_history.append(new_record)

        # Update current claims
        for k, v in correction_fields.items():
            if v is not None and v != "":
                self.customer_claims[k] = v

        self.last_updated = time.time()
        return new_record

    def record_resolver_result(self, result: Any) -> None:
        """Store the latest resolver result snapshot."""
        self.resolver_result = result
        if hasattr(result, "verified_evidence"):
            self.verified_evidence = dict(result.verified_evidence or {})
        elif isinstance(result, dict):
            self.verified_evidence = dict(result.get("verified_evidence", {}))
        self.last_updated = time.time()

    def record_diagnosis(self, diagnosis: dict[str, Any], resolution_status: str) -> None:
        """Store the latest diagnosis snapshot."""
        self.diagnosis = dict(diagnosis)
        self.resolution_status = resolution_status
        self.last_updated = time.time()

    def record_no_match(self, response_hash: str = "") -> None:
        """Track consecutive no-match responses."""
        self.no_match_count += 1
        self.last_response_hash = response_hash
        self.last_updated = time.time()

    def record_recheck(self) -> None:
        """Track recheck requests."""
        self.recheck_count += 1
        self.last_recheck_at = time.time()
        self.last_updated = time.time()

    def increment_messages(self) -> None:
        """Increment the message counter."""
        self.message_count += 1
        self.last_updated = time.time()


@dataclass
class ConversationState:
    """Multi-case conversation state for a single chat session.

    Drop-in upgrade for the old ``_session_context: dict[str, ActiveCaseContext]``.
    """

    session_id: str = ""
    active_case_id: str | None = None
    cases: list[CaseState] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def active_case(self) -> CaseState | None:
        """Currently active case, or None."""
        if not self.active_case_id:
            return None
        for c in self.cases:
            if c.case_id == self.active_case_id and c.status == "active":
                return c
        return None

    def create_case(self, workflow_id: str) -> CaseState:
        """Create a new active case and return it."""
        case = CaseState(workflow_id=workflow_id)
        self.cases.append(case)
        self.active_case_id = case.case_id
        logger.info(
            "[ConversationState] Created case %s (workflow=%s)",
            case.case_id, workflow_id,
        )
        return case

    def archive_case(self, case_id: str) -> None:
        """Archive a case (e.g. before switching workflows)."""
        for c in self.cases:
            if c.case_id == case_id:
                c.status = "archived"
                if self.active_case_id == case_id:
                    self.active_case_id = None
                logger.info(
                    "[ConversationState] Archived case %s (workflow=%s)",
                    c.case_id, c.workflow_id,
                )
                return

    def archive_and_create(self, new_workflow_id: str) -> CaseState:
        """Archive the current active case and create a new one.

        Used during workflow switches.
        """
        if self.active_case_id:
            self.archive_case(self.active_case_id)
        return self.create_case(new_workflow_id)

    def restore_case(self, workflow_id: str) -> CaseState | None:
        """Restore an archived case for the given workflow, if one exists.

        Returns the restored case, or None if no match.
        """
        for c in reversed(self.cases):
            if c.workflow_id == workflow_id and c.status == "archived":
                c.status = "active"
                self.active_case_id = c.case_id
                logger.info(
                    "[ConversationState] Restored case %s (workflow=%s)",
                    c.case_id, c.workflow_id,
                )
                return c
        return None

    def switch_to_case(self, case_id: str) -> CaseState | None:
        """Make a specific case active (by ID)."""
        for c in self.cases:
            if c.case_id == case_id:
                if c.status == "archived":
                    c.status = "active"
                if self.active_case_id and self.active_case_id != case_id:
                    self.archive_case(self.active_case_id)
                self.active_case_id = case_id
                return c
        return None

    def to_active_case_context(self) -> dict[str, Any]:
        """Backward-compatible conversion to the old ActiveCaseContext dict.

        Returns a dict consumable by existing functions that expect the
        ``_session_context[session_id]`` shape.
        """
        case = self.active_case
        if case is None:
            return {}

        return {
            "case_id": case.case_id,
            "selected_workflow": case.workflow_id,
            "has_active_case": True,
            "awaiting_field": "",
            "resolution_status": case.resolution_status,
            "customer_claims": dict(case.customer_claims),
            "verified_evidence": dict(case.verified_evidence),
            "no_match_count": case.no_match_count,
            "recheck_count": case.recheck_count,
            "last_response_hash": case.last_response_hash,
        }

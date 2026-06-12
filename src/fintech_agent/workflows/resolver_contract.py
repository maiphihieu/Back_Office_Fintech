"""Universal resolver contract for all workflow resolvers.

Every workflow resolver must return a ``ResolverResult`` with the fields
defined here. The core chatbot pipeline consumes this contract — it
never touches workflow-specific fields directly.

DESIGN PRINCIPLES:
  - ``resolver_status`` is the ONLY status the core pipeline checks.
  - ``verified_evidence`` is the ONLY evidence the core pipeline trusts.
  - ``candidate_evidence`` enables the "broader search" pattern (§6).
  - ``contradictions`` are detected generically (claim ≠ evidence).
  - ``root_cause`` is optional — only populated when diagnosis is certain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RootCause:
    """Structured root-cause from a resolver's own analysis."""

    found: bool = False
    issue_location: str = ""
    reason: str = ""
    confidence: str = "low"  # low | medium | high


@dataclass
class ResolverResult:
    """Standardised output that every workflow resolver must produce.

    The core chatbot pipeline only reads these fields — never
    workflow-specific attributes.
    """

    resolver_status: str = "insufficient_evidence"
    # verified_match | no_match | multiple_candidates | contradiction |
    # insufficient_evidence | resolved

    verified_evidence: dict[str, Any] = field(default_factory=dict)
    candidate_evidence: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    root_cause: RootCause | None = None

    # ── Legacy compat fields (consumed by existing callers) ──
    # These mirror the old ``ResolutionResult`` so the transition is smooth.
    public_safe_evidence: dict[str, Any] = field(default_factory=dict)
    public_response: str = ""
    resolved_entity_id: str | None = None
    resolved_entity_type: str = "none"
    missing_info: list[str] = field(default_factory=list)
    # Verified evidence values (from DB, not customer)
    verified_amount: int | None = None
    verified_status: str = ""
    verified_owner_id: str = ""
    verified_bank_name: str = ""
    verified_bank_status: str = ""
    verified_provider_status: str = ""

    # ── Mapping helpers ──

    @property
    def resolution_status(self) -> str:
        """Backward-compatible alias for ``resolver_status``.

        Maps the new contract vocabulary to the legacy one used by
        ``customer_chat.py`` and ``evidence_mapper.py``.
        """
        _MAP = {
            "verified_match": "resolved",
            "resolved": "resolved",
            "no_match": "no_match",
            "multiple_candidates": "multiple_candidates",
            "contradiction": "no_match",  # presented as "found, but mismatch"
            "insufficient_evidence": "need_more_info",
        }
        return _MAP.get(self.resolver_status, self.resolver_status)

    @resolution_status.setter
    def resolution_status(self, value: str) -> None:
        """Allow legacy callers to write ``resolution_status``."""
        _REVERSE = {
            "resolved": "resolved",
            "no_match": "no_match",
            "multiple_candidates": "multiple_candidates",
            "need_more_info": "insufficient_evidence",
            "invalid_session": "insufficient_evidence",
            "evidence_error": "insufficient_evidence",
            "ownership_mismatch": "no_match",
        }
        self.resolver_status = _REVERSE.get(value, value)

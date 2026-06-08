"""Structured debug trace for each customer chat turn.

Captures the full pipeline state at every step for inspection.
NEVER includes: PIN, OTP, password, pin_hash, raw evidence rows,
raw ledger/reconciliation/master wallet details, risk_score, fraud_status.

Usage:
    trace = CustomerChatTrace(session_id="...", message="...")
    # ... populate at each step ...
    trace.log_safe()   # logs in dev mode
    trace.to_dict()    # returns sanitized dict
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TraceMessageAnalysis:
    """Trace snapshot of LLM message analysis."""
    message_type: str = ""
    belongs_to_active_case: bool = False
    workflow_hint: str = ""
    customer_emotion: str = ""
    confidence: float = 0.0
    extracted_amount: Any = None
    extracted_time: str = ""
    extracted_bank: str = ""
    extracted_reference: str = ""
    extracted_transaction_id: str = ""


@dataclass
class TraceCaseContext:
    """Trace snapshot of active case context."""
    selected_workflow: str = ""
    issue_type: str = ""
    missing_fields_before: list[str] = field(default_factory=list)
    missing_fields_after: list[str] = field(default_factory=list)
    extracted_info_before: dict = field(default_factory=dict)
    extracted_info_after: dict = field(default_factory=dict)


@dataclass
class TraceResolver:
    """Trace snapshot of resolver execution."""
    called: bool = False
    resolver_type: str = ""  # wallet_user | merchant | account_lock
    query_basis: dict = field(default_factory=dict)
    candidate_count: int = 0
    resolution_status: str = "skipped"
    resolved_entity_type: str = "none"
    resolved_entity_id_present: bool = False


@dataclass
class TraceEvidence:
    """Trace snapshot of evidence lookup."""
    lookup_called: bool = False
    evidence_found: bool = False
    evidence_keys: list[str] = field(default_factory=list)
    has_bank_status: bool = False
    has_wallet_status: bool = False
    has_reconciliation: bool = False


@dataclass
class TraceRuleEngine:
    """Trace snapshot of rule engine execution."""
    called: bool = False
    decision_present: bool = False
    issue_location_present: bool = False
    recommended_action_present: bool = False


@dataclass
class TraceDiagnosis:
    """Trace snapshot of public-safe diagnosis."""
    created: bool = False
    what_was_checked: list[str] = field(default_factory=list)
    confirmed_public_facts: list[str] = field(default_factory=list)
    likely_issue_location: str = ""
    customer_safe_cause: str = ""
    next_step: str = ""
    confidence: str = "low"


@dataclass
class TraceComposer:
    """Trace snapshot of response composer."""
    called: bool = False
    input_has_diagnosis: bool = False
    input_has_evidence: bool = False
    input_has_rule_result: bool = False
    used_llm: bool = False
    used_deterministic: bool = False


@dataclass
class TraceGuardrail:
    """Trace snapshot of output guardrail."""
    passed: bool = True
    blocked_terms: list[str] = field(default_factory=list)


@dataclass
class CustomerChatTrace:
    """Full structured trace for one customer chat turn.

    Populated step-by-step during pipeline execution.
    Safe for logging — never contains raw sensitive data.
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_id: str = ""
    message: str = ""
    active_case_id_before: str = ""
    active_case_id_after: str = ""

    message_analysis: TraceMessageAnalysis = field(
        default_factory=TraceMessageAnalysis,
    )
    case_context: TraceCaseContext = field(
        default_factory=TraceCaseContext,
    )
    resolver: TraceResolver = field(default_factory=TraceResolver)
    evidence: TraceEvidence = field(default_factory=TraceEvidence)
    rule_engine: TraceRuleEngine = field(default_factory=TraceRuleEngine)
    public_safe_diagnosis: TraceDiagnosis = field(
        default_factory=TraceDiagnosis,
    )
    response_composer: TraceComposer = field(
        default_factory=TraceComposer,
    )
    guardrail: TraceGuardrail = field(default_factory=TraceGuardrail)

    final_status: str = ""
    final_response_length: int = 0
    questions_count: int = 0

    def populate_analysis(self, analysis: Any) -> None:
        """Populate trace from MessageAnalysis object."""
        self.message_analysis.message_type = getattr(
            analysis, "message_type", "",
        )
        self.message_analysis.belongs_to_active_case = getattr(
            analysis, "belongs_to_active_case", False,
        )
        self.message_analysis.workflow_hint = getattr(
            analysis, "workflow_hint", "",
        )
        self.message_analysis.customer_emotion = getattr(
            analysis, "customer_emotion", "",
        )
        self.message_analysis.confidence = getattr(
            analysis, "confidence", 0.0,
        )

        extracted = getattr(analysis, "extracted", None)
        if extracted:
            self.message_analysis.extracted_amount = getattr(
                extracted, "amount", None,
            )
            self.message_analysis.extracted_time = getattr(
                extracted, "approximate_time_text", "",
            ) or ""
            self.message_analysis.extracted_bank = getattr(
                extracted, "bank_name", "",
            ) or ""
            self.message_analysis.extracted_reference = getattr(
                extracted, "bank_reference", "",
            ) or ""
            self.message_analysis.extracted_transaction_id = getattr(
                extracted, "transaction_id", "",
            ) or ""

    def populate_resolver(self, resolution: Any) -> None:
        """Populate trace from ResolutionResult."""
        self.resolver.called = True
        self.resolver.resolution_status = getattr(
            resolution, "resolution_status", "unknown",
        )
        self.resolver.resolved_entity_type = getattr(
            resolution, "resolved_entity_type", "none",
        )
        self.resolver.resolved_entity_id_present = bool(
            getattr(resolution, "resolved_entity_id", None),
        )

        evidence = getattr(resolution, "public_safe_evidence", {}) or {}
        self.evidence.lookup_called = bool(evidence)
        self.evidence.evidence_found = bool(evidence)
        self.evidence.evidence_keys = list(evidence.keys())
        self.evidence.has_bank_status = "bank_status" in evidence
        self.evidence.has_wallet_status = "wallet_status" in evidence
        self.evidence.has_reconciliation = "reconciliation_status" in evidence

    def populate_diagnosis(self, public_evidence: dict) -> None:
        """Populate trace from public-safe evidence/diagnosis dict."""
        if not public_evidence:
            return
        self.public_safe_diagnosis.created = bool(
            public_evidence.get("customer_safe_cause")
            or public_evidence.get("what_we_know"),
        )
        self.public_safe_diagnosis.what_was_checked = (
            public_evidence.get("what_was_checked", [])
        )
        self.public_safe_diagnosis.confirmed_public_facts = (
            public_evidence.get("confirmed_public_facts", [])
        )
        self.public_safe_diagnosis.likely_issue_location = (
            public_evidence.get("likely_issue_location", "")
        )
        self.public_safe_diagnosis.customer_safe_cause = (
            public_evidence.get("customer_safe_cause", "")
        )
        self.public_safe_diagnosis.next_step = (
            public_evidence.get("next_step", "")
        )
        self.public_safe_diagnosis.confidence = (
            public_evidence.get("confidence", "low")
        )

    def populate_guardrail(self, guardrail_result: Any) -> None:
        """Populate trace from GuardrailResult."""
        self.guardrail.passed = getattr(guardrail_result, "is_safe", True)
        self.guardrail.blocked_terms = getattr(
            guardrail_result, "violations", [],
        )

    def to_dict(self) -> dict:
        """Convert to safe dict for logging or API response."""
        from dataclasses import asdict
        return asdict(self)

    def log_safe(self) -> None:
        """Log trace at INFO level (dev mode only)."""
        if os.environ.get("ENVIRONMENT", "local") in (
            "local", "development", "dev",
        ):
            import json
            try:
                trace_json = json.dumps(
                    self.to_dict(), ensure_ascii=False, indent=2,
                )
                logger.info(
                    "[ChatTrace] request=%s\\n%s",
                    self.request_id, trace_json,
                )
            except Exception as exc:
                logger.warning(
                    "[ChatTrace] Failed to serialize trace: %s", exc,
                )

"""Generic diagnosis step — workflow-agnostic wrapper.

This module provides a standardised diagnosis contract that:
  1. Delegates to workflow-specific diagnosers (registered in WorkflowSpec)
     when available.
  2. Falls back to the existing ``diagnostic_engine.diagnose()`` for
     built-in workflows.
  3. Falls back to a generic, data-driven diagnosis for unknown workflows.

The output shape (``DiagnosisResult``) is the ONLY thing the core
chatbot pipeline reads — it never accesses workflow-specific diagnosis
internals.

SAFETY: This module is pure deterministic — no LLM, no I/O, no side
effects. It reads evidence and config, never writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DiagnosisResult:
    """Standardised diagnosis output consumed by the chatbot pipeline.

    All fields are customer-safe by construction — internal terms
    (risk_score, fraud_status, ledger, etc.) are never stored here.
    """

    can_explain_to_customer: bool = False
    root_cause_found: bool = False
    issue_location: str = ""
    customer_safe_explanation: str = ""
    requires_staff_review: bool = True
    requires_more_customer_info: bool = False
    missing_fields: list[str] = field(default_factory=list)
    confidence: str = "low"  # low | medium | high

    # Full structured diagnostic (from diagnostic_engine.DiagnosticResult)
    # Stored for staff-facing display; never sent to customer.
    _raw_diagnostic: Any = field(default=None, repr=False)


def diagnose_case(
    workflow_id: str,
    resolver_result: Any,
    customer_claims: dict[str, Any] | None = None,
    extracted_info: dict[str, Any] | None = None,
    rule_result: Any = None,
) -> DiagnosisResult:
    """Produce a standardised diagnosis for a resolved/unresolved case.

    Precedence:
      1. If the workflow has a registered ``diagnoser`` callable in its
         ``WorkflowSpec``, call it and wrap the output.
      2. Else, call the existing ``diagnostic_engine.diagnose()`` for
         built-in workflows (wallet_topup, fraud_account_lock).
      3. Else, produce a generic low-confidence diagnosis.

    Args:
        workflow_id: Active workflow (from registry).
        resolver_result: Output from the resolver (ResolverResult or legacy ResolutionResult).
        customer_claims: Customer-provided claim fields (for reference only).
        extracted_info: Structured extracted info from the message analyzer.
        rule_result: Deterministic rule-engine output (if applicable).

    Returns:
        DiagnosisResult — always non-None, always customer-safe.
    """
    from fintech_agent.workflows.workflow_registry import get_registry

    registry = get_registry()
    spec = registry.get(workflow_id)

    # ── 1. Registered custom diagnoser ──
    if spec and spec.diagnoser is not None:
        try:
            raw = spec.diagnoser(
                resolver_result=resolver_result,
                extracted_info=extracted_info,
                rule_result=rule_result,
            )
            return _wrap_custom_diagnostic(raw)
        except Exception as exc:
            logger.warning(
                "[GenericDiagnosis] Custom diagnoser for '%s' failed: %s",
                workflow_id, exc,
            )

    # ── 2. Built-in diagnostic engine ──
    diagnosis_str = ""
    evidence_bundle = None

    if rule_result is not None:
        if isinstance(rule_result, dict):
            diagnosis_str = rule_result.get("diagnosis", "")
        else:
            diagnosis_str = getattr(rule_result, "diagnosis", "")

    if resolver_result is not None:
        if isinstance(resolver_result, dict):
            evidence_bundle = resolver_result.get("verified_evidence") or resolver_result
        elif hasattr(resolver_result, "verified_evidence"):
            evidence_bundle = resolver_result.verified_evidence or {}
        else:
            evidence_bundle = resolver_result

    try:
        from fintech_agent.llm.diagnostic_engine import diagnose as _diagnose_builtin

        raw_diagnostic = _diagnose_builtin(
            workflow=workflow_id,
            diagnosis=diagnosis_str,
            evidence_bundle=evidence_bundle,
            extracted_info=extracted_info,
        )
        return _wrap_diagnostic_result(raw_diagnostic)
    except Exception as exc:
        logger.warning(
            "[GenericDiagnosis] Built-in diagnostic_engine failed for '%s': %s",
            workflow_id, exc,
        )

    # ── 3. Generic fallback ──
    return DiagnosisResult(
        can_explain_to_customer=False,
        root_cause_found=False,
        issue_location="unknown",
        customer_safe_explanation="",
        requires_staff_review=True,
        requires_more_customer_info=True,
        confidence="low",
    )


def _wrap_diagnostic_result(raw: Any) -> DiagnosisResult:
    """Convert a ``diagnostic_engine.DiagnosticResult`` to ``DiagnosisResult``."""
    from fintech_agent.llm.diagnostic_engine import DiagnosticResult as EngineResult

    if not isinstance(raw, EngineResult):
        return DiagnosisResult(confidence="low", _raw_diagnostic=raw)

    bottleneck = raw.bottleneck
    resolution = raw.resolution

    return DiagnosisResult(
        can_explain_to_customer=bottleneck.confidence in ("high", "medium"),
        root_cause_found=bottleneck.location != "unknown",
        issue_location=bottleneck.location,
        customer_safe_explanation=bottleneck.explanation,
        requires_staff_review=resolution.approval_required,
        requires_more_customer_info=bool(raw.missing_data),
        missing_fields=list(raw.missing_data),
        confidence=bottleneck.confidence,
        _raw_diagnostic=raw,
    )


def _wrap_custom_diagnostic(raw: Any) -> DiagnosisResult:
    """Wrap arbitrary diagnoser output into ``DiagnosisResult``.

    Accepts:
      - A ``DiagnosisResult`` (returned as-is).
      - A dict with the expected fields.
      - A ``diagnostic_engine.DiagnosticResult``.
    """
    if isinstance(raw, DiagnosisResult):
        return raw

    # Check if it's a DiagnosticResult from the built-in engine
    try:
        from fintech_agent.llm.diagnostic_engine import DiagnosticResult as EngineResult
        if isinstance(raw, EngineResult):
            return _wrap_diagnostic_result(raw)
    except ImportError:
        pass

    if isinstance(raw, dict):
        return DiagnosisResult(
            can_explain_to_customer=raw.get("can_explain_to_customer", False),
            root_cause_found=raw.get("root_cause_found", False),
            issue_location=raw.get("issue_location", ""),
            customer_safe_explanation=raw.get("customer_safe_explanation", ""),
            requires_staff_review=raw.get("requires_staff_review", True),
            requires_more_customer_info=raw.get("requires_more_customer_info", False),
            missing_fields=raw.get("missing_fields", []),
            confidence=raw.get("confidence", "low"),
            _raw_diagnostic=raw,
        )

    return DiagnosisResult(confidence="low", _raw_diagnostic=raw)

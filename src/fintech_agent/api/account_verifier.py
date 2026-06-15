"""Generic account issue verification framework.

Single entry point for ALL customer complaint verification across every workflow.
No workflow-specific logic lives here — each workflow provides its own rules via
WorkflowSpec.

Usage::

    from fintech_agent.api.account_verifier import verify_account_issue

    result = verify_account_issue(
        session_identity=session,
        workflow_id="wallet_topup",
        customer_claims=claims,
        conversation_context=conv_ctx,
    )

    # result.issue_exists → True/False
    # result.verified_evidence → {...}
    # result.contradictions → [...]
    # result.root_cause → {"found": ..., "reason": ..., ...}

CONTRACT:
    - verify_account_issue is the ONLY entry point for all workflows.
    - It delegates to existing resolver, claims, evidence, and diagnosis modules.
    - No hard-coded workflow logic, phrases, amounts, phone numbers, user IDs.
    - WorkflowSpec provides: resolver, evidence_schema, issue_verification_rules,
      safe_response_policy. Adding a new workflow = registering a new WorkflowSpec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fintech_agent.api.customer_claims import (
    CustomerClaims,
    VerifiedEvidence,
    detect_contradictions,
)
from fintech_agent.api.generic_resolver import (
    ResolutionResult,
    _build_identity_trace,
    discover_account_issues,
    resolve_case_evidence,
)
from fintech_agent.safety.evidence_mapper import build_public_safe_diagnosis
from fintech_agent.workflows.workflow_registry import get_registry

logger = logging.getLogger(__name__)


# ─── VerificationResult contract ───────────────────────────────

@dataclass
class VerificationResult:
    """Standardized result from verify_account_issue().

    This is the ONLY data structure the response composer and guardrail
    need to produce a customer reply. Nothing else should be consulted.
    """

    workflow_id: str = ""
    identity_resolved: bool = False
    issue_exists: bool = False
    issue_status: str = "no_match"
    # Possible values:
    #   "verified_issue_found"   — data confirms the customer's complaint
    #   "no_issue_found"         — account data checked, no issue present
    #   "insufficient_evidence"  — could not get enough data to decide
    #   "contradiction"          — customer claim conflicts with verified data
    #   "no_match"               — no matching data found at all

    verified_evidence: dict = field(default_factory=dict)
    customer_claims: dict = field(default_factory=dict)
    contradictions: list[dict] = field(default_factory=list)
    data_checked: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)

    root_cause: dict = field(default_factory=lambda: {
        "found": False,
        "reason": "",
        "issue_location": "",
        "confidence": "low",
    })

    # Debug only — never exposed to customer
    identity_trace: dict | None = None
    discovery_result: dict | None = None


# ─── Resolution → issue_status mapping ─────────────────────────

_RESOLUTION_TO_ISSUE_STATUS = {
    "resolved": "verified_issue_found",
    "amount_mismatch": "contradiction",
    "multiple_candidates": "verified_issue_found",
    "no_match": "no_match",
    "need_more_info": "insufficient_evidence",
    "invalid_session": "no_match",
    "ownership_mismatch": "no_match",
    "evidence_error": "insufficient_evidence",
}


def _map_issue_status(
    resolution_status: str,
    has_contradictions: bool,
    discovery_has_issue: bool,
) -> str:
    """Map resolver output + contradiction state to issue_status."""
    if has_contradictions and resolution_status in ("resolved", "amount_mismatch"):
        return "contradiction"

    base = _RESOLUTION_TO_ISSUE_STATUS.get(resolution_status, "no_match")

    # If resolution says resolved, issue exists
    if base == "verified_issue_found":
        return base

    # If no resolution match but discovery found data without issues
    if base == "no_match" and not discovery_has_issue:
        return "no_issue_found"

    return base


# ─── Build verified_evidence dict from ResolutionResult ────────

def _build_verified_evidence(resolution: ResolutionResult) -> dict:
    """Extract verified evidence from a resolution result.

    Only includes fields that are actually populated (non-empty).
    This is what the response composer may treat as factual.
    """
    evidence: dict[str, Any] = {}

    if resolution.resolved_entity_id:
        evidence["entity_id"] = resolution.resolved_entity_id
    if resolution.resolved_entity_type and resolution.resolved_entity_type != "none":
        evidence["entity_type"] = resolution.resolved_entity_type
    if resolution.verified_amount is not None:
        evidence["amount"] = resolution.verified_amount
    if resolution.verified_status:
        evidence["status"] = resolution.verified_status
    if resolution.verified_owner_id:
        evidence["owner_id"] = resolution.verified_owner_id
    if resolution.verified_bank_name:
        evidence["bank_name"] = resolution.verified_bank_name
    if resolution.verified_bank_status:
        evidence["bank_status"] = resolution.verified_bank_status
    if resolution.verified_provider_status:
        evidence["provider_status"] = resolution.verified_provider_status

    # Include public-safe diagnosis evidence if available
    pse = resolution.public_safe_evidence or {}
    for key in (
        "what_was_checked", "confirmed_public_facts",
        "customer_safe_cause", "likely_issue_location",
        "next_step", "customer_action_needed", "confidence",
        "case_status", "workflow",
    ):
        val = pse.get(key)
        if val:
            evidence[key] = val

    return evidence


def _build_root_cause(resolution: ResolutionResult) -> dict:
    """Build root_cause dict from the public-safe evidence."""
    pse = resolution.public_safe_evidence or {}
    cause = pse.get("customer_safe_cause", "")
    location = pse.get("likely_issue_location", "")
    confidence = pse.get("confidence", "low")

    return {
        "found": bool(cause),
        "reason": cause,
        "issue_location": location,
        "confidence": confidence,
    }


# ─── Main entry point ──────────────────────────────────────────

def verify_account_issue(
    session_identity: dict,
    workflow_id: str,
    customer_claims: CustomerClaims,
    conversation_context: dict,
) -> VerificationResult:
    """Generic account issue verification — works for ALL workflows.

    This is the single entry point for the entire verification pipeline.
    It delegates to existing modules:
      1. Identity resolution (from session)
      2. WorkflowSpec lookup (from registry)
      3. Data resolution (resolve_case_evidence)
      4. Contradiction detection (detect_contradictions)
      5. Root cause analysis (build_public_safe_diagnosis)

    Args:
        session_identity: Server-side session dict (user_id, merchant_id, etc.)
        workflow_id: LLM-selected workflow (e.g. "wallet_topup")
        customer_claims: Accumulated customer claims from messages
        conversation_context: Dict with analysis, extracted fields, etc.

    Returns:
        VerificationResult with standardized fields for all workflows.
    """
    # ── Step 1: Identity trace ──
    if not session_identity:
        return VerificationResult(
            workflow_id=workflow_id,
            identity_resolved=False,
            issue_status="no_match",
            customer_claims=customer_claims.latest if customer_claims else {},
        )

    identity_trace = _build_identity_trace(session_identity)
    identity_resolved = identity_trace.get("identity_found", False)

    if not identity_resolved:
        return VerificationResult(
            workflow_id=workflow_id,
            identity_resolved=False,
            issue_status="no_match",
            identity_trace=identity_trace,
            customer_claims=customer_claims.latest if customer_claims else {},
        )

    # ── Step 2: WorkflowSpec lookup ──
    registry = get_registry()
    spec = registry.get(workflow_id)
    if not spec:
        logger.warning(
            "[Verifier] Unknown workflow '%s' — falling back to generic resolution",
            workflow_id,
        )

    # Validate subject_type is supported
    subject_type = session_identity.get("subject_type", "")
    if spec and subject_type not in spec.supported_subject_types:
        logger.warning(
            "[Verifier] Subject type '%s' not supported for workflow '%s'",
            subject_type, workflow_id,
        )
        return VerificationResult(
            workflow_id=workflow_id,
            identity_resolved=True,
            issue_status="no_match",
            identity_trace=identity_trace,
            customer_claims=customer_claims.latest if customer_claims else {},
            missing_evidence=[f"subject_type_{subject_type}_not_supported"],
        )

    # ── Step 3: Resolve evidence (delegates to existing resolver) ──
    analysis = conversation_context.get("analysis")
    if not analysis:
        return VerificationResult(
            workflow_id=workflow_id,
            identity_resolved=True,
            issue_status="insufficient_evidence",
            identity_trace=identity_trace,
            customer_claims=customer_claims.latest if customer_claims else {},
            missing_evidence=["message_analysis"],
        )

    active_case_context = conversation_context.get("active_case_context")
    resolution = resolve_case_evidence(
        session_identity, active_case_context, analysis,
    )

    # ── Step 4: Account discovery (for no-criteria path) ──
    discovery_result = None
    discovery_has_issue = False
    if resolution.discovery_result:
        discovery_result = resolution.discovery_result
        discovery_has_issue = resolution.discovery_result.get("issue_found", False)

    # ── Step 5: Build verified evidence ──
    verified_evidence = _build_verified_evidence(resolution)

    # ── Step 6: Build VerifiedEvidence for contradiction detection ──
    ve = VerifiedEvidence(
        resolved_entity_id=resolution.resolved_entity_id or "",
        resolved_entity_type=resolution.resolved_entity_type,
        verified_amount=resolution.verified_amount,
        verified_status=resolution.verified_status,
        verified_owner_id=resolution.verified_owner_id,
        verified_bank_name=resolution.verified_bank_name,
        verified_bank_status=resolution.verified_bank_status,
        verified_provider_status=resolution.verified_provider_status,
        evidence_source="transaction_table",
    )
    contradictions_list = detect_contradictions(customer_claims, ve)
    contradictions_dicts = [
        {
            "field": c.field,
            "customer_claim": c.customer_claim,
            "verified_value": c.verified_value,
            "severity": c.severity,
        }
        for c in contradictions_list
    ]
    has_contradictions = len(contradictions_dicts) > 0

    # ── Step 7: Map issue status ──
    issue_status = _map_issue_status(
        resolution.resolution_status,
        has_contradictions,
        discovery_has_issue,
    )
    issue_exists = issue_status in ("verified_issue_found", "contradiction")

    # ── Step 8: Root cause ──
    root_cause = _build_root_cause(resolution)

    # ── Step 9: Data checked ──
    data_checked = []
    if discovery_result:
        data_checked = discovery_result.get("data_checked", [])
    elif verified_evidence:
        # Infer from what we found
        if "bank_status" in verified_evidence:
            data_checked.append("bank_status")
        if "status" in verified_evidence:
            data_checked.append("transaction_status")
        if "provider_status" in verified_evidence:
            data_checked.append("provider_status")

    # ── Step 10: Missing evidence ──
    missing_evidence = list(resolution.missing_info or [])

    # ── Step 11: Assemble result ──
    result = VerificationResult(
        workflow_id=workflow_id,
        identity_resolved=True,
        issue_exists=issue_exists,
        issue_status=issue_status,
        verified_evidence=verified_evidence,
        customer_claims=customer_claims.latest if customer_claims else {},
        contradictions=contradictions_dicts,
        data_checked=data_checked,
        missing_evidence=missing_evidence,
        root_cause=root_cause,
        identity_trace=identity_trace,
        discovery_result=discovery_result,
    )

    logger.info(
        "[Verifier] workflow=%s identity=%s issue_status=%s issue_exists=%s "
        "contradictions=%d data_checked=%s",
        workflow_id,
        identity_resolved,
        issue_status,
        issue_exists,
        len(contradictions_dicts),
        data_checked,
    )

    return result

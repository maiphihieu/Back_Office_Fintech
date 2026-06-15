"""Generic case-transition decision for multi-issue customer chats.

A single chat can contain several distinct complaints. Each new message must be
judged on its own intent + workflow, then compared against the active case so a
*resolved / no-issue* case never blocks the NEXT complaint, and a different
workflow never reuses the previous workflow's diagnosis/evidence.

This module is pure and data-driven (no hard-coded phrases, no DB access): it
takes the router's structured understanding + the active case state and returns
a transition decision. The pipeline acts on it (reset no-match streak, clear
stale diagnosis, route the correct resolver).
"""

from __future__ import annotations

from dataclasses import dataclass

# Workflows the resolver/rule-engine can actually diagnose.
_KNOWN_WORKFLOWS = frozenset({
    "wallet_topup", "fraud_account_lock", "train_ticket",
    "utility_bill", "merchant_settlement_delay",
})

# Message types that are NOT a complaint/case interaction.
_NON_CASE_TYPES = frozenset({"greeting", "out_of_scope"})

# Message types that just acknowledge / thank, not a new request.
_ACK_TYPES = frozenset({"acknowledgement", "correction_ack"})

# Case statuses that mean the active case is finished / found no issue, so the
# next complaint must start fresh (not be glued on as a follow-up).
_TERMINAL_STATUSES = frozenset({
    "resolved", "no_issue_found", "no_match", "closed", "evidence_error",
})


@dataclass
class CaseTransition:
    """Decision for how the latest message relates to the active case."""
    transition: str              # same_case | workflow_switch | new_case |
                                 # acknowledgement | off_topic
    reason: str
    target_workflow: str         # workflow to resolve the latest message against
    reuse_old_diagnosis: bool    # may the previous diagnosis/evidence be reused?

    def to_dict(self) -> dict:
        return {
            "transition": self.transition,
            "reason": self.reason,
            "target_workflow": self.target_workflow,
            "reuse_old_diagnosis": self.reuse_old_diagnosis,
        }


def _norm_workflow(wf: str | None) -> str:
    wf = (wf or "").strip()
    return wf if wf in _KNOWN_WORKFLOWS else ""


def decide_case_transition(
    latest_router_result: dict,
    active_case: dict,
    conversation_state: dict | None = None,
) -> CaseTransition:
    """Decide how the latest message relates to the active case.

    Args:
        latest_router_result: {"message_type", "workflow_hint", ...} from the
            LLM analyzer (the latest message's intent).
        active_case: {"workflow", "status", "case_id"} of the active case (may
            be empty when there is no active case).
        conversation_state: reserved for future multi-case history.

    Returns:
        CaseTransition. The pipeline must NOT reuse old diagnosis/evidence when
        reuse_old_diagnosis is False.
    """
    message_type = str(latest_router_result.get("message_type", "") or "")
    latest_wf = _norm_workflow(latest_router_result.get("workflow_hint"))

    active_wf = _norm_workflow(active_case.get("workflow"))
    active_status = str(active_case.get("status", "") or "").lower()
    has_active = bool(active_case.get("workflow") or active_case.get("case_id"))

    # 1. Off-topic / greeting — never a case interaction.
    if message_type in _NON_CASE_TYPES:
        return CaseTransition("off_topic", "non-case message type", "", False)

    # 2. Pure acknowledgement / correction-ack — stays on the active case.
    if message_type in _ACK_TYPES:
        return CaseTransition(
            "acknowledgement", "acknowledgement of active case",
            active_wf, reuse_old_diagnosis=True,
        )

    # 3. No active case yet → this is the first/new case.
    if not has_active:
        return CaseTransition("new_case", "no active case", latest_wf, False)

    # 4. A genuinely NEW complaint. If the active case is already finished /
    #    found no issue, OR the latest workflow differs, it is a separate issue —
    #    never reuse the old diagnosis.
    if message_type == "new_complaint":
        if latest_wf and active_wf and latest_wf != active_wf:
            return CaseTransition(
                "workflow_switch", "new complaint about a different workflow",
                latest_wf, False,
            )
        if active_status in _TERMINAL_STATUSES:
            return CaseTransition(
                "new_case", "new complaint after the active case was finished",
                latest_wf, False,
            )
        if latest_wf and active_wf and latest_wf == active_wf:
            # New complaint, same workflow, active case still open → fresh case
            # for the same workflow (don't merge into the old diagnosis).
            return CaseTransition(
                "new_case", "new complaint, same workflow", latest_wf, False,
            )
        # Workflow still unknown — let the downstream graph classify it; treat
        # as a fresh case so the previous issue does not block it.
        return CaseTransition(
            "new_case", "new complaint, workflow not yet resolved", "", False,
        )

    # 5. Explicit workflow switch, or the latest workflow simply differs from the
    #    active one (e.g. a follow-up that actually raised a new service).
    if message_type == "workflow_switch" or (
        latest_wf and active_wf and latest_wf != active_wf
    ):
        return CaseTransition(
            "workflow_switch", "latest workflow differs from active case",
            latest_wf, False,
        )

    # 6. Default: same-case follow-up / status / info — reuse the active case.
    return CaseTransition(
        "same_case", "follow-up on the active case",
        active_wf or latest_wf, reuse_old_diagnosis=True,
    )

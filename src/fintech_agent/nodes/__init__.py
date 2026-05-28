"""Graph nodes — each function takes AgentState and returns partial updates.

Nodes:
    case_intake        — initialize case from raw input
    extract_info       — extract structured info (MVP: regex)
    missing_info       — handle missing info (dead_letter if critical)
    fetch_evidence     — call read-only tools
    conflict_detection — run conflict rules
    workflow_router    — route to train/utility workflow
    rule_decision      — apply deterministic rules
    recommendation     — build RecommendedAction
    approval_gate      — check/wait for human approval
    draft_action       — create draft output
    audit_close        — log and close case
    manual_review      — route to human
    retry_dead_letter  — retry or dead-letter
"""

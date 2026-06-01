"""Graph builder — assembles the LangGraph StateGraph.

Graph flow:
    START → case_intake → extract_info
      → [missing?] → missing_info_handler → [dead_letter?] → audit_and_close → END
                                          → fetch_evidence
      → [no missing] → fetch_evidence
      → [critical fail?] → retry_or_dead_letter → [dead?] → audit_and_close → END
                                                → fetch_evidence (loop)
      → detect_conflict
      → [conflict?] → manual_review → audit_and_close → END
      → route_workflow
      → [unknown?] → manual_review
      → apply_rules → recommend_action
      → [approval?] → approval_gate → [approved?] → create_draft
                                    → [rejected?] → manual_review
      → create_draft → audit_and_close → END
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from fintech_agent.audit import AuditLogger
from fintech_agent.graph.edges import (
    after_approval_gate,
    after_detect_conflict,
    after_extract_info,
    after_fetch_evidence,
    after_generate_response,
    after_missing_info,
    after_retry,
    after_route_workflow,
)
from fintech_agent.graph.state import AgentState
from fintech_agent.nodes.approval_gate import approval_gate
from fintech_agent.nodes.audit_close import audit_and_close
from fintech_agent.nodes.case_intake import case_intake
from fintech_agent.nodes.conflict_detection import detect_conflict
from fintech_agent.nodes.draft_action import create_draft
from fintech_agent.nodes.extract_info import extract_info
from fintech_agent.nodes.fetch_evidence import fetch_evidence
from fintech_agent.nodes.generate_response import generate_response
from fintech_agent.nodes.manual_review import manual_review
from fintech_agent.nodes.missing_info import missing_info_handler
from fintech_agent.nodes.recommendation import recommend_action
from fintech_agent.nodes.retry_dead_letter import retry_or_dead_letter
from fintech_agent.nodes.rule_decision import apply_rules
from fintech_agent.nodes.workflow_router import route_workflow


def build_graph(audit: AuditLogger | None = None) -> StateGraph:
    """Build and return the (uncompiled) StateGraph.

    Args:
        audit: Optional shared AuditLogger injected into all nodes.
               If None, nodes run without audit logging.

    Returns:
        Uncompiled StateGraph. Call .compile() to get a runnable.
    """
    graph = StateGraph(AgentState)

    # ── Register nodes (inject audit via partial) ────────────
    graph.add_node("case_intake", partial(case_intake, audit=audit))
    graph.add_node("extract_info", partial(extract_info, audit=audit))
    graph.add_node("missing_info_handler", partial(missing_info_handler, audit=audit))
    graph.add_node("fetch_evidence", partial(fetch_evidence, audit=audit))
    graph.add_node("detect_conflict", partial(detect_conflict, audit=audit))
    graph.add_node("route_workflow", partial(route_workflow, audit=audit))
    graph.add_node("apply_rules", partial(apply_rules, audit=audit))
    graph.add_node("recommend_action", partial(recommend_action, audit=audit))
    graph.add_node("generate_response", partial(generate_response, audit=audit))
    graph.add_node("approval_gate", partial(approval_gate, audit=audit))
    graph.add_node("create_draft", partial(create_draft, audit=audit))
    graph.add_node("audit_and_close", partial(audit_and_close, audit=audit))
    graph.add_node("manual_review", partial(manual_review, audit=audit))
    graph.add_node("retry_or_dead_letter", partial(retry_or_dead_letter, audit=audit))

    # ── Edges ────────────────────────────────────────────────
    graph.add_edge(START, "case_intake")
    graph.add_edge("case_intake", "extract_info")
    graph.add_conditional_edges("extract_info", after_extract_info)
    graph.add_conditional_edges("missing_info_handler", after_missing_info)
    graph.add_conditional_edges("fetch_evidence", after_fetch_evidence)
    graph.add_conditional_edges("retry_or_dead_letter", after_retry)
    graph.add_conditional_edges("detect_conflict", after_detect_conflict)
    graph.add_conditional_edges("route_workflow", after_route_workflow)
    graph.add_edge("apply_rules", "recommend_action")
    graph.add_edge("recommend_action", "generate_response")
    graph.add_conditional_edges("generate_response", after_generate_response)
    graph.add_conditional_edges("approval_gate", after_approval_gate)
    graph.add_edge("create_draft", "audit_and_close")
    graph.add_edge("manual_review", "audit_and_close")
    graph.add_edge("audit_and_close", END)

    return graph


def compile_graph(audit: AuditLogger | None = None):
    """Build and compile the graph into a runnable.

    Args:
        audit: Optional shared AuditLogger.
    """
    return build_graph(audit=audit).compile()

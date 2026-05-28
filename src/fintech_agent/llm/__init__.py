"""LLM extraction module.

Public API:
    extract_complaint_info — extract structured info from complaint text.
        Uses mock/regex when MOCK_LLM=true (default).
        Uses OpenAI when MOCK_LLM=false.
"""

from fintech_agent.llm.extractor import extract_complaint_info

__all__ = ["extract_complaint_info"]

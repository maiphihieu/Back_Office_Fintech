"""LLM module — extraction and response generation.

Public API:
    extract_complaint_info — extract structured info from complaint text.
        Uses mock/regex when MOCK_LLM=true (default).
        Uses OpenAI when MOCK_LLM=false.
    generate_case_response — generate structured summary for CS/Ops staff.
        Uses OpenAI when OPENAI_API_KEY is set.
        Falls back to safe generic response otherwise.
"""

from fintech_agent.llm.extractor import extract_complaint_info
from fintech_agent.llm.response_generator import generate_case_response

__all__ = ["extract_complaint_info", "generate_case_response"]


"""Audit subsystem — structured event logging for compliance and traceability.

Usage:
    from fintech_agent.audit import AuditLogger

    logger = AuditLogger(log_file="audit.jsonl")
    logger.log_case_received("CASE_001", raw_complaint="...")
"""

from fintech_agent.audit.audit_logger import AuditLogger

__all__ = ["AuditLogger"]

"""Tool-layer exceptions.

These exceptions are raised by tools and caught by graph nodes
for retry logic and error handling.
"""

from __future__ import annotations


class ToolError(Exception):
    """Base exception for all tool errors."""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"[{tool_name}] {message}")


class ToolDataNotFound(ToolError):
    """Raised when the requested data does not exist."""

    def __init__(self, tool_name: str, key: str, value: str) -> None:
        self.key = key
        self.value = value
        super().__init__(tool_name, f"Data not found: {key}={value}")


class ToolTimeout(ToolError):
    """Raised when a tool call times out (simulated in mock)."""

    def __init__(self, tool_name: str, timeout_ms: int = 5000) -> None:
        self.timeout_ms = timeout_ms
        super().__init__(tool_name, f"Timeout after {timeout_ms}ms")


class ToolValidationError(ToolError):
    """Raised when tool input validation fails."""

    def __init__(self, tool_name: str, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(tool_name, f"Validation error on '{field}': {reason}")


class DuplicateActionError(ToolError):
    """Raised when attempting to create a duplicate draft."""

    def __init__(self, tool_name: str, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(tool_name, f"Duplicate action: key={idempotency_key}")

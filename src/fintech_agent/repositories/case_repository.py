"""Case state repository — in-memory store for case lifecycle."""

from __future__ import annotations

from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.case_state import CaseState


class CaseRepository:
    """In-memory repository for case states.

    For MVP, cases are stored in a dict and lost on restart.
    Can be swapped to SQLite/PostgreSQL later.
    """

    def __init__(self) -> None:
        self._store: dict[str, CaseState] = {}

    def save(self, case: CaseState) -> CaseState:
        """Save or update a case."""
        self._store[case.case_id] = case
        return case

    def get_by_id(self, case_id: str) -> CaseState:
        """Fetch a case by ID.

        Raises:
            RecordNotFound: If the case doesn't exist.
        """
        if case_id not in self._store:
            raise RecordNotFound("CaseState", "case_id", case_id)
        return self._store[case_id]

    def list_all(self) -> list[CaseState]:
        """List all cases (for admin/debug)."""
        return list(self._store.values())

    def exists(self, case_id: str) -> bool:
        """Check if a case exists."""
        return case_id in self._store

"""Train provider status tool — READ-ONLY."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fintech_agent.database.repository_factory import get_train_provider_repo

if TYPE_CHECKING:
    from fintech_agent.repositories.provider_repository import TrainProviderRepository
from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import TrainProviderStatus
from fintech_agent.tools.tool_errors import ToolDataNotFound, ToolTimeout

TOOL_NAME = "get_train_provider_status"

# Mock: simulate timeout
_TIMEOUT_REFS = frozenset({"TRAIN_REF_TIMEOUT_001"})


@dataclass(frozen=True)
class TrainProviderResult:
    """Structured result from get_train_provider_status."""

    success: bool
    provider_status: TrainProviderStatus | None = None
    error: str | None = None


def get_train_provider_status(
    provider_ref_id: str,
    repo: TrainProviderRepository | None = None,
) -> TrainProviderResult:
    """Fetch train provider status by reference ID.

    Raises:
        ToolTimeout: Simulated timeout.
        ToolDataNotFound: If no record matches.
    """
    if provider_ref_id in _TIMEOUT_REFS:
        raise ToolTimeout(TOOL_NAME)

    _repo = repo or get_train_provider_repo()
    try:
        status = _repo.get_by_ref_id(provider_ref_id)
        return TrainProviderResult(success=True, provider_status=status)
    except RecordNotFound:
        raise ToolDataNotFound(TOOL_NAME, "provider_ref_id", provider_ref_id)

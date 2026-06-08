"""Utility bill provider status tool — READ-ONLY."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fintech_agent.database.repository_factory import get_utility_provider_repo

if TYPE_CHECKING:
    from fintech_agent.repositories.provider_repository import UtilityProviderRepository
from fintech_agent.repositories.base import RecordNotFound
from fintech_agent.schemas.evidence import UtilityProviderStatus
from fintech_agent.tools.tool_errors import ToolDataNotFound

TOOL_NAME = "get_utility_bill_status"


@dataclass(frozen=True)
class UtilityProviderResult:
    """Structured result from get_utility_bill_status."""

    success: bool
    provider_status: UtilityProviderStatus | None = None
    error: str | None = None


def get_utility_bill_status(
    provider_ref_id: str,
    repo: UtilityProviderRepository | None = None,
) -> UtilityProviderResult:
    """Fetch utility bill provider status by reference ID.

    Raises:
        ToolDataNotFound: If no record matches.
    """
    _repo = repo or get_utility_provider_repo()
    try:
        status = _repo.get_by_ref_id(provider_ref_id)
        return UtilityProviderResult(success=True, provider_status=status)
    except RecordNotFound:
        raise ToolDataNotFound(TOOL_NAME, "provider_ref_id", provider_ref_id)

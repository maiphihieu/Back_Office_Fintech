"""Provider status repositories — source of truth for service delivery."""

from __future__ import annotations

from fintech_agent.repositories.base import BaseRepository, RecordNotFound
from fintech_agent.repositories.json_loader import load_mock_json
from fintech_agent.schemas.evidence import TrainProviderStatus, UtilityProviderStatus


class TrainProviderRepository(BaseRepository):
    """Read-only repository for train ticket provider statuses."""

    def __init__(self) -> None:
        self._data: list[dict] | None = None

    def _load_data(self) -> list[dict]:
        if self._data is None:
            self._data = load_mock_json("mock_train_provider_status.json")
        return self._data

    def get_by_ref_id(self, provider_ref_id: str) -> TrainProviderStatus:
        """Fetch train provider status by provider reference ID.

        Raises:
            RecordNotFound: If no record matches.
        """
        for record in self._load_data():
            if record["provider_ref_id"] == provider_ref_id:
                return TrainProviderStatus(**record)
        raise RecordNotFound("TrainProviderStatus", "provider_ref_id", provider_ref_id)


class UtilityProviderRepository(BaseRepository):
    """Read-only repository for utility bill provider statuses."""

    def __init__(self) -> None:
        self._data: list[dict] | None = None

    def _load_data(self) -> list[dict]:
        if self._data is None:
            self._data = load_mock_json("mock_utility_provider_status.json")
        return self._data

    def get_by_ref_id(self, provider_ref_id: str) -> UtilityProviderStatus:
        """Fetch utility provider status by provider reference ID.

        Raises:
            RecordNotFound: If no record matches.
        """
        for record in self._load_data():
            if record["provider_ref_id"] == provider_ref_id:
                return UtilityProviderStatus(**record)
        raise RecordNotFound(
            "UtilityProviderStatus", "provider_ref_id", provider_ref_id
        )

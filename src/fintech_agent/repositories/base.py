"""Base repository interface and common exceptions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class RecordNotFound(Exception):
    """Raised when a record is not found in the data store."""

    def __init__(self, entity: str, key: str, value: str) -> None:
        self.entity = entity
        self.key = key
        self.value = value
        super().__init__(f"{entity} not found: {key}={value}")


class BaseRepository(ABC):
    """Abstract base for all repositories."""

    @abstractmethod
    def _load_data(self) -> list[dict]:
        """Load raw data from the backing store."""
        ...

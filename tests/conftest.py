"""Shared test fixtures."""

import pytest
from fastapi.testclient import TestClient

from fintech_agent.api.service import reset_case_service
from fintech_agent.main import app


@pytest.fixture()
def client() -> TestClient:
    """Return a FastAPI test client with fresh service state."""
    reset_case_service()
    return TestClient(app)

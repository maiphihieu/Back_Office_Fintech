"""Shared test fixtures."""

import pytest
from fastapi.testclient import TestClient

from fintech_agent.api.service import reset_case_service
from fintech_agent.main import app
from fintech_agent.tools.draft_action_tools import reset_default_store


@pytest.fixture()
def client() -> TestClient:
    """Return a FastAPI test client with fresh service state."""
    reset_case_service()
    reset_default_store()  # Clear idempotency store between tests
    return TestClient(app)

"""Tests for the /health endpoint."""

from fastapi.testclient import TestClient

from fintech_agent import __version__


def test_health_returns_200(client: TestClient) -> None:
    """GET /health should return 200 with status ok."""
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == __version__
    assert "environment" in data


def test_health_version_matches_package(client: TestClient) -> None:
    """The version in /health should match the package version."""
    response = client.get("/health")
    assert response.json()["version"] == "0.1.0"

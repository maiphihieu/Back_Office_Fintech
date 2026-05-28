"""JSON file loader utility for mock repositories."""

from __future__ import annotations

import json
from pathlib import Path

# Resolve once: .../src/fintech_agent/data/mock/
_MOCK_DIR = Path(__file__).resolve().parent.parent / "data" / "mock"


def load_mock_json(filename: str) -> list[dict]:
    """Load and parse a JSON file from the mock data directory.

    Args:
        filename: Name of the JSON file (e.g. 'mock_transactions.json').

    Returns:
        Parsed list of dicts.

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    filepath = _MOCK_DIR / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Mock data file not found: {filepath}")
    with filepath.open(encoding="utf-8") as f:
        return json.load(f)

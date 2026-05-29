"""Supabase client — singleton connection to Supabase PostgreSQL.

SECURITY:
  - NEVER log supabase_key.
  - NEVER expose key in error messages.
  - Key comes from .env, never hardcoded.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from fintech_agent.config import Settings

_logger = logging.getLogger(__name__)

# Lazy import to avoid ImportError when supabase not installed
_Client = None


def _get_client_class():
    """Lazy import supabase Client and create_client."""
    global _Client
    if _Client is None:
        try:
            from supabase import create_client, Client  # noqa: F401
            _Client = (create_client, Client)
        except ImportError as exc:
            raise ImportError(
                "supabase package not installed. Run: pip install supabase>=2.0.0"
            ) from exc
    return _Client


class SupabaseConfigError(Exception):
    """Raised when Supabase config is invalid."""


def get_supabase_client(settings: Settings | None = None):
    """Create or return a Supabase client.

    Args:
        settings: App settings. If None, loads from env.

    Returns:
        supabase.Client instance.

    Raises:
        SupabaseConfigError: If SUPABASE_URL or SUPABASE_KEY is missing.
    """
    if settings is None:
        from fintech_agent.config import get_settings
        settings = get_settings()

    if not settings.supabase_url:
        raise SupabaseConfigError("SUPABASE_URL is not set")
    if not settings.supabase_key:
        raise SupabaseConfigError("SUPABASE_KEY is not set")

    create_client, _ = _get_client_class()

    _logger.info("Connecting to Supabase at %s", settings.supabase_url)
    # NEVER log settings.supabase_key
    client = create_client(settings.supabase_url, settings.supabase_key)
    return client

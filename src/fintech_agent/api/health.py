"""Health-check endpoint."""

from fastapi import APIRouter

from fintech_agent import __version__
from fintech_agent.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check() -> dict:
    """Return service health status.

    This endpoint is used by load balancers and monitoring tools
    to verify the service is running.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.app_env,
    }

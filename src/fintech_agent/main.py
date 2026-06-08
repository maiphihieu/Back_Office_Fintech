"""FastAPI application entrypoint."""

import os
import logging

from dotenv import load_dotenv

# Load .env BEFORE any other imports so all modules see env vars
load_dotenv()

logger = logging.getLogger(__name__)

from fastapi import FastAPI

from fintech_agent import __version__
from fintech_agent.api.health import router as health_router
from fintech_agent.api.cases import router as cases_router
from fintech_agent.api.approvals import router as approvals_router
from fintech_agent.api.customer_chat import router as customer_chat_router
from fintech_agent.api.mock_auth import router as mock_auth_router
from fintech_agent.api.chat_handoff import router as chat_handoff_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Fintech Agent — AI Back-office Workflow Agent",
        description=(
            "AI Agent hỗ trợ xử lý khiếu nại, hoàn tiền và đối soát "
            "giao dịch trong hệ sinh thái fintech."
        ),
        version=__version__,
    )

    # --- CORS ---
    from fastapi.middleware.cors import CORSMiddleware
    default_origins = (
        "http://localhost:5173,"
        "http://localhost:3000,"
        "https://backofficefintech-production-deda.up.railway.app"
    )
    cors_origins = os.getenv("CORS_ALLOWED_ORIGINS", default_origins)
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Register routers ---
    app.include_router(health_router)
    app.include_router(cases_router)
    app.include_router(approvals_router)
    app.include_router(customer_chat_router)  # Customer-facing chat (sanitized)
    app.include_router(mock_auth_router)       # Mock customer auth (demo login)
    app.include_router(chat_handoff_router)    # Back-office chat handoff tickets

    return app


app = create_app()

# ── Startup diagnostics (safe — never prints actual key) ──
_has_key = bool(os.getenv("OPENAI_API_KEY"))
_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
logger.info("[LLM] OPENAI_API_KEY loaded: %s", _has_key)
logger.info("[LLM] OPENAI_MODEL: %s", _model)


if __name__ == "__main__":
    import uvicorn

    from fintech_agent.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "fintech_agent.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )

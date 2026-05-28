"""FastAPI application entrypoint."""

from fastapi import FastAPI

from fintech_agent import __version__
from fintech_agent.api.health import router as health_router
from fintech_agent.api.cases import router as cases_router
from fintech_agent.api.approvals import router as approvals_router


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

    # --- CORS for frontend dev server ---
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Register routers ---
    app.include_router(health_router)
    app.include_router(cases_router)
    app.include_router(approvals_router)

    return app


app = create_app()


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

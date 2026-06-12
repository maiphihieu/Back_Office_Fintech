"""Mock authentication endpoints for customer demo login.

Endpoints:
  GET  /api/auth/mock-sessions   — list available demo sessions (internal/debug)
  POST /api/auth/mock-login      — validate session_id, return safe context
  POST /api/auth/customer-login  — wallet customer OR merchant phone + PIN login
  GET  /api/auth/me              — check current session

SECURITY:
  - Response MUST NOT include: user_id, wallet_id, merchant_id,
    tax_code, phone, email, pin_hash — these stay server-side only.
  - This is MOCK auth for demo — not production Supabase Auth.
  - PIN is NEVER logged, stored, or returned to frontend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from fintech_agent.api.server_runtime import SERVER_INSTANCE_ID
from fintech_agent.database.repository_factory import get_mock_session_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["mock-auth"])


# ─── Response Models ────────────────────────────────────────────

class MockSessionPublic(BaseModel):
    """Safe public session info — NO internal identity fields."""
    session_id: str
    subject_type: str = Field(description="wallet_user | merchant")
    display_name: str
    role: str


class MockSessionsListResponse(BaseModel):
    """List of available demo sessions."""
    sessions: list[MockSessionPublic]


class MockLoginRequest(BaseModel):
    """Login request — session_id only."""
    session_id: str = Field(..., min_length=1, max_length=200)


class CustomerLoginRequest(BaseModel):
    """Customer or merchant login — phone + PIN."""
    phone: str = Field(..., min_length=4, max_length=20)
    pin: str = Field(..., min_length=4, max_length=10)


class CustomerLoginResponse(BaseModel):
    """Customer login result — safe fields only."""
    session_id: str = ""
    subject_type: str = ""
    display_name: str = ""
    role: str = ""
    is_authenticated: bool = False
    message: str = ""


class MeResponse(BaseModel):
    """Current session context."""
    session_id: str = ""
    subject_type: str = ""
    display_name: str = ""
    role: str = ""
    is_authenticated: bool = False
    # Changes on every backend restart → lets the client reset a stale chat.
    server_instance_id: str = ""


# ─── Safe field extraction ──────────────────────────────────────

def _to_public(row: dict) -> MockSessionPublic:
    """Extract ONLY safe public fields from a session row.

    NEVER includes: user_id, wallet_id, merchant_id, tax_code, phone, email.
    """
    return MockSessionPublic(
        session_id=row["session_id"],
        subject_type=row["subject_type"],
        display_name=row["display_name"],
        role=row.get("role", "customer"),
    )


# ─── Endpoints ──────────────────────────────────────────────────

@router.get(
    "/mock-sessions",
    response_model=MockSessionsListResponse,
    summary="List available demo sessions",
    description="Returns safe public fields only. No internal identity data.",
)
async def list_mock_sessions() -> MockSessionsListResponse:
    """List all active demo sessions for the login page."""
    try:
        repo = get_mock_session_repo()
        rows = repo.list_active_sessions_with_expiry_filter()
    except Exception as exc:
        logger.error("[MockAuth] Failed to list sessions: %s", exc)
        raise HTTPException(status_code=503, detail="Service unavailable")

    sessions = [_to_public(r) for r in rows]
    return MockSessionsListResponse(sessions=sessions)


@router.post(
    "/mock-login",
    response_model=MockSessionPublic,
    summary="Mock login with session_id",
    description="Validate session and return safe context. No password required.",
)
async def mock_login(req: MockLoginRequest) -> MockSessionPublic:
    """Validate a demo session_id and return safe public context."""
    try:
        repo = get_mock_session_repo()
        session = repo.get_session(req.session_id)
    except Exception as exc:
        logger.error("[MockAuth] Failed to load session: %s", exc)
        raise HTTPException(status_code=503, detail="Service unavailable")

    if session is None:
        raise HTTPException(
            status_code=401,
            detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
        )

    logger.info(
        "[MockAuth] Login success: session=%s, type=%s",
        req.session_id, session.get("subject_type"),
    )
    return _to_public(session)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Check current session",
    description="Returns safe session context if valid, or is_authenticated=false.",
)
async def get_me(
    session_id: str = Query(default="", description="Demo session ID"),
) -> MeResponse:
    """Check if a session_id is still valid."""
    if not session_id:
        return MeResponse(is_authenticated=False, server_instance_id=SERVER_INSTANCE_ID)

    try:
        repo = get_mock_session_repo()
        session = repo.get_session(session_id)
    except Exception as exc:
        logger.error("[MockAuth] Failed to check session: %s", exc)
        return MeResponse(is_authenticated=False, server_instance_id=SERVER_INSTANCE_ID)

    if session is None:
        return MeResponse(is_authenticated=False, server_instance_id=SERVER_INSTANCE_ID)

    return MeResponse(
        session_id=session["session_id"],
        subject_type=session["subject_type"],
        display_name=session["display_name"],
        role=session.get("role", "customer"),
        is_authenticated=True,
        server_instance_id=SERVER_INSTANCE_ID,
    )


@router.post(
    "/customer-login",
    response_model=CustomerLoginResponse,
    summary="Customer or merchant login with phone + PIN",
    description=(
        "Verify phone + PIN via the verify_mock_customer_pin RPC. "
        "Works for both wallet_user and merchant subjects — subject_type is "
        "resolved server-side. Returns safe session context only."
    ),
)
async def customer_login(req: CustomerLoginRequest) -> CustomerLoginResponse:
    """Customer or merchant login: phone + PIN.

    The RPC resolves subject_type (wallet_user | merchant); both are allowed.

    SECURITY:
      - PIN is verified server-side via PostgreSQL crypt() RPC.
      - PIN is NEVER logged or returned.
      - Response exposes ONLY safe fields (no user_id/wallet_id/merchant_id/
        tax_code/pin_hash).
      - Response does not reveal whether phone exists.
      - On failure, returns generic error message.
    """
    try:
        repo = get_mock_session_repo()
        result = repo.verify_pin(req.phone, req.pin)
    except Exception as exc:
        logger.error("[CustomerLogin] Failed to verify PIN: %s", exc)
        return CustomerLoginResponse(
            is_authenticated=False,
            message="Hệ thống đang bận. Vui lòng thử lại sau.",
        )

    if result is None:
        return CustomerLoginResponse(
            is_authenticated=False,
            message="Số điện thoại hoặc mã PIN không đúng.",
        )

    logger.info(
        "[CustomerLogin] Success: session=%s",
        result["session_id"],
    )
    return CustomerLoginResponse(
        session_id=result["session_id"],
        subject_type=result["subject_type"],
        display_name=result["display_name"],
        role=result["role"],
        is_authenticated=True,
    )

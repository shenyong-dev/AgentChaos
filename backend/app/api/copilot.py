"""Explicit assistant writes store only assistant receipts; never execute business tools."""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from redis import Redis

from app.api.booking import get_booking_engine
from app.api.cases import case_identity
from app.copilot.service import CopilotService
from app.core.config import get_settings
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.copilot import CopilotAsk, CopilotCreate

router = APIRouter(
    prefix="/api/v1/copilot",
    tags=["platform-copilot"],
    responses={code: {"model": ErrorEnvelope} for code in (400, 401, 403, 404, 409, 429, 503, 504)},
)


@lru_cache
def get_copilot_service():
    settings = get_settings()
    return CopilotService(
        get_booking_engine(), Redis.from_url(settings.redis_url, socket_timeout=5), settings
    )


Service = Annotated[CopilotService, Depends(get_copilot_service)]
Identity = Annotated[int, Depends(case_identity)]


def binding(request, owner):
    from app.auth.service import digest, get_session_service

    return (
        digest(get_session_service().cookie(request))
        if getattr(request.state, "browser_session", False)
        else f"basic:{owner}"
    )


@router.get("/access")
def access(request: Request, owner: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.access(owner), request_id=request.state.request_id)


@router.post("/sessions", status_code=201)
def create(
    body: CopilotCreate, request: Request, owner: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.create(owner, binding(request, owner), body),
        request_id=request.state.request_id,
    )


@router.get("/sessions/{session_id}")
def read(session_id: UUID, request: Request, owner: Identity, service: Service) -> Envelope[dict]:
    return Envelope(
        data=service.public(service.load(owner, binding(request, owner), session_id)),
        request_id=request.state.request_id,
    )


@router.post("/sessions/{session_id}/messages")
def ask(
    session_id: UUID, body: CopilotAsk, request: Request, owner: Identity, service: Service
) -> Envelope[dict]:
    def check():
        if getattr(request.state, "browser_session", False):
            from app.auth.service import get_session_service

            get_session_service().resolve(request, touch=False)

    return Envelope(
        data=service.ask(owner, binding(request, owner), session_id, body, check=check),
        request_id=request.state.request_id,
    )


@router.get("/sessions/{session_id}/messages/{request_id}")
def receipt(
    session_id: UUID, request_id: UUID, request: Request, owner: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.read_turn(owner, binding(request, owner), session_id, request_id),
        request_id=request.state.request_id,
    )

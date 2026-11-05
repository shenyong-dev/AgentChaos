"""Booking Agent debugging with existing confirmation and revision contracts."""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from redis import Redis

from app.api.booking import booking_identity, get_booking_engine
from app.core.config import get_settings
from app.interactive.service import InteractionService
from app.schemas.agent import DemoMessage, DemoResume, DemoRetry
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.interactive import InteractionCreate

router = APIRouter(
    prefix="/api/v1/interactive",
    tags=["interactive-booking-agent"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 404, 409, 429, 503, 504)},
)


@lru_cache
def get_interaction_service():
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=15)
    return InteractionService(get_booking_engine(), redis, settings)


Service = Annotated[InteractionService, Depends(get_interaction_service)]
Identity = Annotated[int, Depends(booking_identity)]


@router.get("/access")
def access(request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.access(), request_id=request.state.request_id)


@router.post("/sessions", status_code=201)
def create(
    body: InteractionCreate, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.create(user, body.model_dump(mode="json")), request_id=request.state.request_id
    )


@router.get("/sessions")
def records(
    request: Request,
    user: Identity,
    service: Service,
    q: Annotated[str, Query(max_length=100)] = "",
    cursor: Annotated[int, Query(ge=0, le=18446744073709551615)] = 0,
) -> Envelope[dict]:
    return Envelope(data=service.records(user, q, cursor), request_id=request.state.request_id)


@router.get("/sessions/{session_id}")
def read(session_id: UUID, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.view(user, str(session_id)), request_id=request.state.request_id)


@router.get("/sessions/{session_id}/history")
def history(
    session_id: UUID,
    request: Request,
    user: Identity,
    service: Service,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Envelope[dict]:
    return Envelope(
        data=service.history(user, str(session_id), cursor, limit),
        request_id=request.state.request_id,
    )


@router.post("/sessions/{session_id}/messages")
def message(
    session_id: UUID, body: DemoMessage, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.act(user, str(session_id), body.model_dump(mode="json"), "message"),
        request_id=request.state.request_id,
    )


@router.get("/sessions/{session_id}/receipts/{request_id}")
def receipt(
    session_id: UUID, request_id: UUID, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.receipt(user, str(session_id), str(request_id)),
        request_id=request.state.request_id,
    )


@router.get("/sessions/{session_id}/trace")
def trace(
    session_id: UUID,
    request: Request,
    user: Identity,
    service: Service,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Envelope[dict]:
    return Envelope(
        data=service.trace(user, str(session_id), cursor, limit),
        request_id=request.state.request_id,
    )


@router.post("/sessions/{session_id}/resume")
def resume(
    session_id: UUID, body: DemoResume, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.act(user, str(session_id), body.model_dump(mode="json"), "resume"),
        request_id=request.state.request_id,
    )


@router.post("/sessions/{session_id}/retry")
def retry(
    session_id: UUID, body: DemoRetry, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.act(user, str(session_id), body.model_dump(mode="json"), "retry"),
        request_id=request.state.request_id,
    )

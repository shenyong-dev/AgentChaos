"""Authenticated reference refund chat; no payment network and no automatic experiments."""

from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis

from app.api.booking import booking_identity, get_booking_engine
from app.core.config import get_settings
from app.interactive.refund import RefundInteractionService
from app.schemas.agent import DemoMessage
from app.schemas.common import Envelope
from app.schemas.interactive import InteractionCreate

router = APIRouter(prefix="/api/v1/refund-interactive", tags=["interactive-refund-agent"])


class RefundDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    draft_id: UUID
    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["confirm", "reject"]


@lru_cache
def get_refund_interaction_service():
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=15)
    return RefundInteractionService(get_booking_engine(), redis, settings)


Service = Annotated[RefundInteractionService, Depends(get_refund_interaction_service)]
Identity = Annotated[int, Depends(booking_identity)]


@router.get("/access")
def access(request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.access(user), request_id=request.state.request_id)


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
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[dict]:
    return Envelope(data=service.records(user, q, cursor), request_id=request.state.request_id)


@router.get("/sessions/{sid}")
def view(sid: UUID, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.view(user, str(sid)), request_id=request.state.request_id)


@router.post("/sessions/{sid}/messages")
def message(
    sid: UUID, body: DemoMessage, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.act(user, str(sid), body.model_dump(mode="json"), "message"),
        request_id=request.state.request_id,
    )


@router.post("/sessions/{sid}/decision")
def decision(
    sid: UUID, body: RefundDecision, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.act(user, str(sid), body.model_dump(mode="json"), "decision"),
        request_id=request.state.request_id,
    )


@router.get("/sessions/{sid}/receipts/{request_id}")
def receipt(
    sid: UUID, request_id: UUID, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.receipt(user, str(sid), str(request_id)), request_id=request.state.request_id
    )


@router.get("/sessions/{sid}/history")
def history(
    sid: UUID,
    request: Request,
    user: Identity,
    service: Service,
    before: Annotated[int | None, Query(ge=0)] = None,
) -> Envelope[dict]:
    return Envelope(
        data=service.history(user, str(sid), before), request_id=request.state.request_id
    )


@router.get("/sessions/{sid}/events")
def events(
    sid: UUID,
    request: Request,
    user: Identity,
    service: Service,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[dict]:
    return Envelope(
        data=service.events(user, str(sid), cursor), request_id=request.state.request_id
    )

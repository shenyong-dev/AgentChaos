"""T04 interactive prototype; graph checkpoints persist, Worker dispatch is T05."""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from redis import Redis

from app.agents.runtime import AgentRuntime
from app.api.booking import booking_identity, get_booking_engine
from app.core.config import get_settings
from app.schemas.agent import DemoMessage, DemoResume, DemoRetry, DemoSessionCreate
from app.schemas.common import Envelope, ErrorEnvelope

router = APIRouter(
    prefix="/api/v1/demo",
    tags=["agent-demo"],
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 429, 503, 504)
    },
)


@lru_cache
def get_agent_runtime():
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=15)
    return AgentRuntime(get_booking_engine(), redis, settings)


RuntimeDependency = Annotated[AgentRuntime, Depends(get_agent_runtime)]
IdentityDependency = Annotated[int, Depends(booking_identity)]


@router.post("/sessions", status_code=201)
def create_session(
    body: DemoSessionCreate,
    request: Request,
    user_id: IdentityDependency,
    runtime: RuntimeDependency,
) -> Envelope[dict]:
    return Envelope(data=runtime.create(user_id), request_id=request.state.request_id)


@router.get("/sessions/{session_id}")
def read_session(
    session_id: UUID, request: Request, user_id: IdentityDependency, runtime: RuntimeDependency
) -> Envelope[dict]:
    return Envelope(data=runtime.view(user_id, session_id), request_id=request.state.request_id)


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: UUID,
    body: DemoMessage,
    request: Request,
    user_id: IdentityDependency,
    runtime: RuntimeDependency,
) -> Envelope[dict]:
    return Envelope(
        data=runtime.act(user_id, session_id, body.model_dump(mode="json")),
        request_id=request.state.request_id,
    )


@router.post("/sessions/{session_id}/resume")
def resume_session(
    session_id: UUID,
    body: DemoResume,
    request: Request,
    user_id: IdentityDependency,
    runtime: RuntimeDependency,
) -> Envelope[dict]:
    return Envelope(
        data=runtime.act(user_id, session_id, body.model_dump(mode="json"), kind="resume"),
        request_id=request.state.request_id,
    )


@router.post("/sessions/{session_id}/retry")
def retry_session(
    session_id: UUID,
    body: DemoRetry,
    request: Request,
    user_id: IdentityDependency,
    runtime: RuntimeDependency,
) -> Envelope[dict]:
    return Envelope(
        data=runtime.act(user_id, session_id, body.model_dump(mode="json"), kind="retry"),
        request_id=request.state.request_id,
    )


@router.get("/sessions/{session_id}/history")
def session_history(
    session_id: UUID,
    request: Request,
    user_id: IdentityDependency,
    runtime: RuntimeDependency,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[dict]:
    return Envelope(
        data=runtime.history(user_id, session_id, cursor, limit),
        request_id=request.state.request_id,
    )

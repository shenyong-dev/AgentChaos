"""Authenticated generator requests and reviewer decisions; no imported approval flags."""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPBasicCredentials
from redis import Redis

from app.agents.errors import AgentError
from app.api.booking import get_booking_engine, security
from app.cases.service import CaseService
from app.core.config import get_settings
from app.schemas.cases import CandidateReview, GenerateCandidates
from app.schemas.common import Envelope, ErrorEnvelope

router = APIRouter(
    prefix="/api/v1/case-generation",
    tags=["case-generation"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 404, 409, 429, 503)},
)


@lru_cache
def get_case_service():
    s = get_settings()
    return CaseService(get_booking_engine(), Redis.from_url(s.redis_url, socket_timeout=15), s)


Service = Annotated[CaseService, Depends(get_case_service)]


def case_identity(
    service: Service, credentials: Annotated[HTTPBasicCredentials | None, Depends(security)]
):
    if credentials is None:
        raise AgentError("case_authentication_required", "请登录生成或审核账号。", 401)
    return service.authenticate(credentials.username, credentials.password)


Identity = Annotated[int, Depends(case_identity)]


@router.post("", status_code=201)
def generate(
    body: GenerateCandidates, request: Request, owner: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=service.generate(owner, body), request_id=request.state.request_id)


@router.get("")
def page(
    request: Request,
    owner: Identity,
    service: Service,
    after: Annotated[int, Query(alias="cursor", ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[dict]:
    return Envelope(data=service.page(owner, after, limit), request_id=request.state.request_id)


@router.get("/access")
def access(request: Request, owner: Identity, service: Service) -> Envelope[dict]:
    from sqlalchemy.orm import Session

    with Session(service.engine) as session:
        user = service.role(session, owner)
        data = {
            "username": user.username,
            "role": user.role,
            "generation_enabled": service.settings.casegen_allow_paid_model_calls,
            "limits": {"max_profiles": 3, "calls": 2, "seconds": 90},
        }
    return Envelope(data=data, request_id=request.state.request_id)


@router.get("/{batch}")
def read(batch: UUID, request: Request, owner: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.read(owner, batch), request_id=request.state.request_id)


@router.get("/{batch}/evidence/{artifact_id}")
def artifact(
    batch: UUID, artifact_id: int, request: Request, owner: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.artifact(owner, batch, artifact_id), request_id=request.state.request_id
    )


@router.post("/candidates/{candidate_id}/review")
def review(
    candidate_id: int, body: CandidateReview, request: Request, reviewer: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.review(reviewer, candidate_id, body), request_id=request.state.request_id
    )

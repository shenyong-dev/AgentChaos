"""Explicit human calibration and independent judge API; no model labels accepted as human."""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPBasicCredentials
from redis import Redis
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.api.booking import get_booking_engine, security
from app.core.config import get_settings
from app.judging.executions import ExecutionJudgeService
from app.judging.service import JudgeService
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.judging import (
    DisagreementReview,
    ExecutionJudgeRequest,
    ExecutionJudgeReview,
    HumanLabel,
    RequestID,
    SealLabels,
)

router = APIRouter(
    prefix="/api/v1/judging",
    tags=["semantic-judging"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 404, 409, 429, 503)},
)


@lru_cache
def get_judge_service():
    s = get_settings()
    return JudgeService(get_booking_engine(), Redis.from_url(s.redis_url, socket_timeout=15), s)


Service = Annotated[JudgeService, Depends(get_judge_service)]


def identity(
    service: Service, credentials: Annotated[HTTPBasicCredentials | None, Depends(security)]
):
    if credentials is None:
        raise AgentError("judge_authentication_required", "请登录评价或审核账号。", 401)
    return service.authenticate(credentials.username, credentials.password)


Identity = Annotated[int, Depends(identity)]


@router.get("/access")
def access(request: Request, user: Identity, service: Service) -> Envelope[dict]:
    with Session(service.engine) as s:
        account = service.role(s, user)
        value = {
            "username": account.username,
            "role": account.role,
            "judge_enabled": service.settings.judge_allow_paid_model_calls,
        }
    return Envelope(data=value, request_id=request.state.request_id)


@router.get("/calibrations")
def page(request: Request, user: Identity, service: Service) -> Envelope[list]:
    return Envelope(data=service.page(user), request_id=request.state.request_id)


@router.get("/executions/{execution}")
def execution_read(
    execution: UUID, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    reader = ExecutionJudgeService(service.engine, service.redis, service.settings)
    return Envelope(data=reader.read(user, execution), request_id=request.state.request_id)


@router.post("/executions/{execution}")
def execution_evaluate(
    execution: UUID, body: ExecutionJudgeRequest, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    evaluator = ExecutionJudgeService(service.engine, service.redis, service.settings)
    return Envelope(
        data=evaluator.evaluate(user, execution, body), request_id=request.state.request_id
    )


@router.post("/executions/{execution}/review")
def execution_review(
    execution: UUID, body: ExecutionJudgeReview, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    evaluator = ExecutionJudgeService(service.engine, service.redis, service.settings)
    return Envelope(
        data=evaluator.review(user, execution, body), request_id=request.state.request_id
    )


@router.post("/calibrations", status_code=201)
def create(body: RequestID, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.create(user, body), request_id=request.state.request_id)


@router.get("/calibrations/{batch}")
def read(batch: UUID, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.read(user, batch), request_id=request.state.request_id)


@router.post("/calibrations/{batch}/samples/{sample}/label")
def label(
    batch: UUID, sample: str, body: HumanLabel, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.label(user, batch, sample, body), request_id=request.state.request_id
    )


@router.post("/calibrations/{batch}/seal")
def seal(
    batch: UUID, body: SealLabels, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=service.seal(user, batch, body), request_id=request.state.request_id)


@router.post("/calibrations/{batch}/samples/{sample}/evaluate")
def evaluate(
    batch: UUID, sample: str, body: RequestID, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.evaluate(user, batch, sample, body), request_id=request.state.request_id
    )


@router.post("/calibrations/{batch}/samples/{sample}/review")
def review(
    batch: UUID,
    sample: str,
    body: DisagreementReview,
    request: Request,
    user: Identity,
    service: Service,
) -> Envelope[dict]:
    return Envelope(
        data=service.review(user, batch, sample, body), request_id=request.state.request_id
    )

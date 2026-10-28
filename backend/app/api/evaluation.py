from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from app.api.booking import booking_identity, get_booking_engine
from app.core.config import get_settings
from app.evaluation.service import EvaluationService
from app.schemas.common import Envelope, ErrorEnvelope

router = APIRouter(
    prefix="/api/v1/executions",
    tags=["evaluation"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 404, 409, 503)},
)


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@lru_cache
def get_evaluation_service():
    return EvaluationService(get_booking_engine(), get_settings())


Service = Annotated[EvaluationService, Depends(get_evaluation_service)]
Identity = Annotated[int, Depends(booking_identity)]


@router.get("/{execution_id}/assertions")
def read(
    execution_id: UUID,
    request: Request,
    user_id: Identity,
    service: Service,
    version: Annotated[str | None, Query(max_length=128)] = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> Envelope[dict]:
    return Envelope(
        data=service.read(user_id, execution_id, version=version, after=cursor, limit=limit),
        request_id=request.state.request_id,
    )


@router.post("/{execution_id}/evaluate")
def evaluate(
    execution_id: UUID, body: EvaluateRequest, request: Request, user_id: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.evaluate(user_id, execution_id), request_id=request.state.request_id
    )

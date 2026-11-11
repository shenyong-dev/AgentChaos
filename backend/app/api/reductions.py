from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request

from app.api.booking import get_booking_engine
from app.api.reports import Identity
from app.core.config import get_settings
from app.reductions.service import ReductionService
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.reductions import (
    ReductionCreate,
    ReductionExecute,
    ReductionReview,
    ReductionStage,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["reductions"],
    responses={status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 503)},
)


@lru_cache
def get_reduction_service():
    return ReductionService(get_booking_engine(), get_settings())


Service = Annotated[ReductionService, Depends(get_reduction_service)]
Index = Annotated[int, Path(ge=0, le=2)]


@router.get("/defects/{key}/reduction")
def latest(key: str, request: Request, user: Identity, service: Service) -> Envelope[dict | None]:
    return Envelope(data=service.latest(user, key), request_id=request.state.request_id)


@router.post("/defects/{key}/reductions", status_code=201)
def create(
    key: str, body: ReductionCreate, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=service.create(user, key, body), request_id=request.state.request_id)


@router.get("/reductions/{identifier}")
def read(identifier: UUID, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.read(user, identifier), request_id=request.state.request_id)


@router.post("/reductions/{identifier}/variants/{index}/review")
def review(
    identifier: UUID,
    index: Index,
    body: ReductionReview,
    request: Request,
    user: Identity,
    service: Service,
) -> Envelope[dict]:
    return Envelope(
        data=service.review(user, identifier, index, body), request_id=request.state.request_id
    )


@router.post("/reductions/{identifier}/variants/{index}/execute")
def execute(
    identifier: UUID,
    index: Index,
    body: ReductionExecute,
    request: Request,
    user: Identity,
    service: Service,
) -> Envelope[dict]:
    return Envelope(
        data=service.execute(user, identifier, index, body), request_id=request.state.request_id
    )


@router.post("/reductions/{identifier}/variants/{index}/draft")
def stage(
    identifier: UUID,
    index: Index,
    body: ReductionStage,
    request: Request,
    user: Identity,
    service: Service,
) -> Envelope[dict]:
    return Envelope(
        data=service.stage(user, identifier, index, body), request_id=request.state.request_id
    )

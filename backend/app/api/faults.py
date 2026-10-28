from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.booking import booking_identity, get_booking_engine
from app.faults.service import FaultService
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.faults import FaultSpec

router = APIRouter(
    prefix="/api/v1/fault-profiles",
    tags=["faults"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 404, 409, 503)},
)


@lru_cache
def get_fault_service():
    return FaultService(get_booking_engine())


Service = Annotated[FaultService, Depends(get_fault_service)]
Identity = Annotated[int, Depends(booking_identity)]


@router.post("", status_code=201)
def create(
    body: FaultSpec, request: Request, user_id: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=service.create(body), request_id=request.state.request_id)


@router.get("")
def page(
    request: Request,
    user_id: Identity,
    service: Service,
    after: Annotated[int, Query(alias="cursor", ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[dict]:
    return Envelope(data=service.page(after, limit), request_id=request.state.request_id)


@router.get("/{identifier}")
def read(identifier: int, request: Request, user_id: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.read(identifier), request_id=request.state.request_id)

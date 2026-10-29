"""Operator workspace discovery; run mutations retain the existing run contracts."""

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.booking import get_booking_engine
from app.api.cases import case_identity
from app.core.config import get_settings
from app.schemas.common import Envelope, ErrorEnvelope
from app.workbench.service import WorkbenchService

router = APIRouter(
    prefix="/api/v1/workbench",
    tags=["workbench"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 404, 409, 503)},
)


@lru_cache
def get_workbench_service():
    return WorkbenchService(get_booking_engine(), get_settings())


Service = Annotated[WorkbenchService, Depends(get_workbench_service)]
Identity = Annotated[int, Depends(case_identity)]
Cursor = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=50)]
Search = Annotated[str, Query(max_length=80)]
Category = Literal[
    "normal", "tool_execution", "memory_and_tool", "retrieval", "security", "robustness"
]
RunStatus = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]


@router.get("/overview")
def overview(request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.overview(user), request_id=request.state.request_id)


@router.get("/versions")
def versions(
    request: Request, user: Identity, service: Service, cursor: Cursor = 0, limit: Limit = 20
) -> Envelope[dict]:
    return Envelope(
        data=service.versions(user, cursor=cursor, limit=limit), request_id=request.state.request_id
    )


@router.get("/cases")
def cases(
    request: Request,
    user: Identity,
    service: Service,
    cursor: Cursor = 0,
    limit: Limit = 20,
    q: Search = "",
    category: Category | None = None,
) -> Envelope[dict]:
    return Envelope(
        data=service.cases(user, cursor=cursor, limit=limit, query=q, category=category),
        request_id=request.state.request_id,
    )


@router.get("/cases/{identifier}")
def case(identifier: int, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.case(user, identifier), request_id=request.state.request_id)


@router.get("/runs")
def runs(
    request: Request,
    user: Identity,
    service: Service,
    cursor: Cursor = 0,
    limit: Limit = 20,
    q: Search = "",
    status: RunStatus | None = None,
) -> Envelope[dict]:
    return Envelope(
        data=service.runs(user, cursor=cursor, limit=limit, query=q, status=status),
        request_id=request.state.request_id,
    )

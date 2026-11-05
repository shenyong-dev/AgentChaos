"""Operator workspace discovery; run mutations retain the existing run contracts."""

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.booking import get_booking_engine
from app.api.cases import case_identity
from app.core.config import get_settings
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.workflow import CommitPlan, RunPlan
from app.workbench.service import WorkbenchService
from app.workbench.workflow import WorkflowService

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


@lru_cache
def get_workflow_service():
    return WorkflowService(get_booking_engine(), get_settings())


Workflow = Annotated[WorkflowService, Depends(get_workflow_service)]


@router.post("/run-plans")
async def preview_run(
    body: RunPlan, request: Request, user: Identity, service: Workflow
) -> Envelope[dict]:
    return Envelope(data=await service.preview(user, body), request_id=request.state.request_id)


@router.post("/run-plans/commit", status_code=201)
async def commit_run(
    body: CommitPlan, request: Request, user: Identity, service: Workflow
) -> Envelope[dict]:
    return Envelope(data=await service.commit(user, body), request_id=request.state.request_id)


@router.get("/runs/{run_id}/reproduction")
def reproduction(
    run_id: str, request: Request, user: Identity, service: Workflow
) -> Envelope[dict]:
    return Envelope(data=service.source(user, run_id), request_id=request.state.request_id)


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

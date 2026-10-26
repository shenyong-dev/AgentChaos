"""Authenticated persistent run control. Dispatch is performed by the scanner."""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.booking import booking_identity, get_booking_engine
from app.core.config import get_settings
from app.executors.dispatch import dispatch_pending
from app.executors.service import RunService
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.runs import CreateRun, ExecutionInput, RecoverExecution

router = APIRouter(
    prefix="/api/v1/runs",
    tags=["runs"],
    responses={status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 429, 503)},
)


@lru_cache
def get_run_service():
    return RunService(get_booking_engine(), get_settings())


Service = Annotated[RunService, Depends(get_run_service)]
Identity = Annotated[int, Depends(booking_identity)]


@router.post("", status_code=201)
def create(
    body: CreateRun, request: Request, user_id: Identity, service: Service
) -> Envelope[dict]:
    identifier = service.create(user_id, body)
    return Envelope(data=service.read(user_id, identifier), request_id=request.state.request_id)


@router.get("/{run_id}")
def read(run_id: UUID, request: Request, user_id: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.read(user_id, run_id), request_id=request.state.request_id)


@router.post("/{run_id}/cancel")
def cancel(run_id: UUID, request: Request, user_id: Identity, service: Service) -> Envelope[dict]:
    service.cancel(user_id, run_id)
    return Envelope(data=service.read(user_id, run_id), request_id=request.state.request_id)


@router.post("/{run_id}/dispatch")
def dispatch(run_id: UUID, request: Request, user_id: Identity, service: Service) -> Envelope[dict]:
    service.read(user_id, run_id)
    result = dispatch_pending(service.engine, service.settings, only_run=run_id)
    return Envelope(data=result, request_id=request.state.request_id)


@router.post("/{run_id}/executions/{execution_id}/input")
def supply_input(
    run_id: UUID,
    execution_id: UUID,
    body: ExecutionInput,
    request: Request,
    user_id: Identity,
    service: Service,
) -> Envelope[dict]:
    service.supply_input(user_id, run_id, execution_id, body)
    return Envelope(data=service.read(user_id, run_id), request_id=request.state.request_id)


@router.post("/{run_id}/executions/{execution_id}/recover")
def recover(
    run_id: UUID,
    execution_id: UUID,
    body: RecoverExecution,
    request: Request,
    user_id: Identity,
    service: Service,
) -> Envelope[dict]:
    service.recover(user_id, run_id, execution_id, body)
    return Envelope(data=service.read(user_id, run_id), request_id=request.state.request_id)

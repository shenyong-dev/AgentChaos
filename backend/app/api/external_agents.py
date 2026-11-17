"""Operators select server registrations; only reviewers approve external case drafts."""

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.booking import get_booking_engine
from app.api.cases import case_identity
from app.core.config import get_settings
from app.external.cases import CaseFile
from app.external.service import ExternalService
from app.schemas.common import Envelope

router = APIRouter(prefix="/api/v1/external-agents", tags=["external-agents"])


@lru_cache
def get_external_service():
    return ExternalService(get_booking_engine(), get_settings())


Service = Annotated[ExternalService, Depends(get_external_service)]
Identity = Annotated[int, Depends(case_identity)]


class Register(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registered_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    expected_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approved", "rejected"]


@router.get("")
def definitions(request: Request, user: Identity, service: Service):
    return Envelope(data=service.definitions(user), request_id=request.state.request_id)


@router.get("/check")
def check(
    request: Request,
    user: Identity,
    service: Service,
    registered_id: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_-]{1,40}$")],
):
    return Envelope(data=service.inspect(user, registered_id), request_id=request.state.request_id)


@router.post("/register")
def register(body: Register, request: Request, user: Identity, service: Service):
    return Envelope(
        data=service.register(user, body.registered_id, body.expected_config_hash),
        request_id=request.state.request_id,
    )


@router.get("/cases")
def cases(request: Request, user: Identity, service: Service):
    return Envelope(data=service.cases(user), request_id=request.state.request_id)


@router.post("/cases", status_code=201)
def import_case(body: CaseFile, request: Request, user: Identity, service: Service):
    return Envelope(data=service.import_case(user, body), request_id=request.state.request_id)


@router.post("/cases/{case_id}/review")
def review(case_id: int, body: Review, request: Request, user: Identity, service: Service):
    return Envelope(
        data=service.review(user, case_id, body.expected_content_hash, body.decision),
        request_id=request.state.request_id,
    )

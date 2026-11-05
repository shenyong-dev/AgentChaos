"""Authenticated GET-only help observations and navigation resolution."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.booking import get_booking_engine
from app.api.cases import case_identity
from app.core.config import get_settings
from app.guidance.service import GuidanceService
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.guidance import GuideAction, GuideContext

router = APIRouter(
    prefix="/api/v1/guidance",
    tags=["platform-guidance"],
    responses={status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 503)},
)


@lru_cache
def get_guidance_service():
    def interaction(owner, identifier):
        from app.api.interactive import get_interaction_service

        return get_interaction_service().view(owner, identifier)

    return GuidanceService(get_booking_engine(), get_settings(), interaction_reader=interaction)


Service = Annotated[GuidanceService, Depends(get_guidance_service)]
Identity = Annotated[int, Depends(case_identity)]


@router.get("/context")
def context(
    request: Request, values: Annotated[GuideContext, Query()], user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=service.context(user, values), request_id=request.state.request_id)


@router.get("/actions")
def action(
    request: Request, values: Annotated[GuideAction, Query()], user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=service.resolve(user, values), request_id=request.state.request_id)

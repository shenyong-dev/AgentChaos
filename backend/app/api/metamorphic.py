"""Derived relation evidence; neither generation nor semantic approval endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.booking import booking_identity, get_booking_engine
from app.metamorphic.service import RelationService
from app.schemas.common import Envelope

router = APIRouter(prefix="/api/v1/runs", tags=["metamorphic"])


def get_relation_service():
    return RelationService(get_booking_engine())


@router.get("/{run_id}/relations")
def read_relations(
    run_id: UUID,
    request: Request,
    owner: Annotated[int, Depends(booking_identity)],
    service: Annotated[RelationService, Depends(get_relation_service)],
) -> Envelope[dict]:
    return Envelope(data=service.read(owner, run_id), request_id=request.state.request_id)

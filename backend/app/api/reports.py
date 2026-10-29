from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.booking import get_booking_engine
from app.api.cases import case_identity, get_case_service
from app.cases.service import CaseService
from app.core.config import get_settings
from app.reports.service import ReportService
from app.schemas.common import Envelope, ErrorEnvelope
from app.schemas.reports import DefectReview, ExportReport

router = APIRouter(
    prefix="/api/v1",
    tags=["reports"],
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 410, 413, 503)
    },
)


@lru_cache
def get_report_service():
    return ReportService(get_booking_engine(), get_settings())


Service = Annotated[ReportService, Depends(get_report_service)]


def report_identity(
    identifier: Annotated[int, Depends(case_identity)],
    service: Annotated[CaseService, Depends(get_case_service)],
):
    with Session(service.engine) as session:
        CaseService.role(session, identifier, "operator")
    return identifier


Identity = Annotated[int, Depends(report_identity)]


@router.get("/reports/runs/{identifier}")
def report(identifier: UUID, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.read(user, identifier), request_id=request.state.request_id)


@router.get("/reports/compare")
def comparison(
    request: Request, user: Identity, service: Service, left: UUID, right: UUID
) -> Envelope[dict]:
    return Envelope(data=service.comparison(user, left, right), request_id=request.state.request_id)


@router.post("/reports/runs/{identifier}/exports")
def export(
    identifier: UUID, body: ExportReport, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(
        data=service.export(user, identifier, body), request_id=request.state.request_id
    )


@router.get("/reports/exports/{artifact_id}/download")
def download(
    artifact_id: Annotated[int, Path(ge=1, le=2**63 - 1)], user: Identity, service: Service
) -> Response:
    payload, media, filename, digest = service.download(user, artifact_id)
    return Response(
        content=payload,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": digest,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/reports/runs/{identifier}/defects")
def collect(identifier: UUID, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.collect(user, identifier), request_id=request.state.request_id)


@router.get("/defects")
def defects(
    request: Request,
    user: Identity,
    service: Service,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["open", "confirmed", "fixed", "verified", "rejected"] | None = None,
) -> Envelope[dict]:
    return Envelope(
        data=service.defects(user, after=cursor, limit=limit, status=status),
        request_id=request.state.request_id,
    )


@router.get("/defects/{key}")
def defect(key: str, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=service.defect(user, key), request_id=request.state.request_id)


@router.post("/defects/{key}/reviews")
def review(
    key: str, body: DefectReview, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=service.review(user, key, body), request_id=request.state.request_id)

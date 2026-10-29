"""Fixed-catalog inspection; reviewer approval is explicit and hash bound."""

from fastapi import APIRouter, Request

from app.api.judging import Identity, Service
from app.datasets.service import DatasetService
from app.schemas.common import Envelope
from app.schemas.datasets import DatasetReview

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


def catalog(service):
    return DatasetService(service.engine, service.settings)


@router.get("/current")
def summary(request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=catalog(service).summary(user), request_id=request.state.request_id)


@router.get("/current/cases/{key}")
def case(key: str, request: Request, user: Identity, service: Service) -> Envelope[dict]:
    return Envelope(data=catalog(service).case(user, key), request_id=request.state.request_id)


@router.post("/current/review")
def review(
    body: DatasetReview, request: Request, user: Identity, service: Service
) -> Envelope[dict]:
    return Envelope(data=catalog(service).review(user, body), request_id=request.state.request_id)

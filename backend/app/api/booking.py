"""Authenticated direct booking API; trusted context cannot be supplied in JSON."""

from datetime import datetime
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.db.engine import create_database_engine
from app.schemas.booking import (
    AvailabilityQuery,
    AvailabilityView,
    BookingToolResult,
    BookingView,
    ConfirmBooking,
    DraftView,
    PreviewBooking,
)
from app.schemas.common import Envelope, ErrorEnvelope
from app.services.booking import BookingContext, BookingError, BookingService, utc_now

router = APIRouter(
    prefix="/api/v1/booking",
    tags=["booking"],
    responses={status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 410, 503)},
)
security = HTTPBasic(auto_error=False)


@lru_cache
def get_booking_engine():
    return create_database_engine()


def get_booking_service():
    return BookingService(get_booking_engine())


ServiceDependency = Annotated[BookingService, Depends(get_booking_service)]


def booking_identity(
    service: ServiceDependency,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> int:
    if credentials is None:
        raise BookingError("authentication_required", "请使用演示账号认证。", 401)
    return service.authenticate(credentials.username, credentials.password)


def get_booking_context(
    user_id: Annotated[int, Depends(booking_identity)],
    reference_clock: Annotated[datetime, Depends(utc_now)],
) -> BookingContext:
    return BookingContext("demo", f"user:{user_id}", reference_clock)


ContextDependency = Annotated[BookingContext, Depends(get_booking_context)]


@router.post("/availability")
def availability(
    request: Request,
    values: AvailabilityQuery,
    context: ContextDependency,
    service: ServiceDependency,
) -> Envelope[BookingToolResult[AvailabilityView]]:
    return Envelope(
        data=service.query_availability(context, values), request_id=request.state.request_id
    )


@router.post("/drafts", status_code=201)
def preview(
    request: Request, values: PreviewBooking, context: ContextDependency, service: ServiceDependency
) -> Envelope[BookingToolResult[DraftView]]:
    return Envelope(
        data=service.preview_booking(context, values), request_id=request.state.request_id
    )


@router.post("/confirm")
def confirm(
    request: Request, values: ConfirmBooking, context: ContextDependency, service: ServiceDependency
) -> Envelope[BookingToolResult[BookingView]]:
    return Envelope(
        data=service.confirm_booking(context, values), request_id=request.state.request_id
    )


@router.get("/requests/{idempotency_key}")
def lookup(
    request: Request, idempotency_key: UUID, context: ContextDependency, service: ServiceDependency
) -> Envelope[BookingToolResult[BookingView | None]]:
    return Envelope(
        data=service.lookup_booking_by_request(context, idempotency_key),
        request_id=request.state.request_id,
    )


@router.delete("/bookings/{booking_id}")
def cancel(
    request: Request, booking_id: UUID, context: ContextDependency, service: ServiceDependency
) -> Envelope[BookingToolResult[BookingView]]:
    return Envelope(
        data=service.cancel_booking(context, booking_id), request_id=request.state.request_id
    )

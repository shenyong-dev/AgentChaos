"""T03 business schemas. Identity, scope and clocks are never model input."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

RoomCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]
PayloadHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CommitState = Literal["not_applicable", "not_committed", "committed", "unknown"]


class BookingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AvailabilityQuery(BookingSchema):
    start_at: AwareDatetime
    end_at: AwareDatetime
    attendees: int = Field(strict=True, ge=1, le=2**31 - 1)
    room_code: RoomCode | None = None


class PreviewBooking(AvailabilityQuery):
    room_code: RoomCode


class ConfirmBookingToolInput(BookingSchema):
    draft_id: UUID
    draft_hash: PayloadHash
    idempotency_key: UUID


class ConfirmBooking(ConfirmBookingToolInput):
    confirmed: bool = Field(strict=True)


class RoomView(BookingSchema):
    room_code: str
    capacity: int


class AvailabilityView(BookingSchema):
    rooms: list[RoomView]
    start_at: AwareDatetime
    end_at: AwareDatetime
    attendees: int
    truncated: bool = False


class DraftView(BookingSchema):
    draft_id: UUID
    draft_hash: PayloadHash
    suggested_idempotency_key: UUID
    room_code: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    attendees: int
    status: Literal["pending", "confirmed", "expired", "superseded", "rejected"]
    expires_at: AwareDatetime


class BookingView(BookingSchema):
    booking_id: UUID
    draft_id: UUID
    idempotency_key: UUID
    room_code: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    attendees: int
    status: Literal["confirmed", "cancelled"]
    created_at: AwareDatetime


class BookingToolResult[T](BookingSchema):
    status: Literal["ok", "not_found"] = "ok"
    code: str
    summary: str
    data: T
    request_key: UUID | None = None
    retryable: bool = False
    commit_state: CommitState
    evidence_refs: list[str] = Field(default_factory=list)

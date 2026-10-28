"""Versioned declarative predicates, never Python, SQL, regex or arbitrary paths."""

from datetime import date as Date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


class AssertionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BookingMatches(AssertionModel):
    type: Literal["booking_matches"]
    date: Date
    attendees: int | None = Field(default=None, ge=1, strict=True)
    room_code: str | None = Field(default=None, min_length=1, max_length=32)


class BookingCount(AssertionModel):
    type: Literal["booking_count"]
    equals: int = Field(ge=0, strict=True)


class NoBookingOn(AssertionModel):
    type: Literal["no_booking_on"]
    date: Date


class ToolCalls(AssertionModel):
    type: Literal["tool_calls"]
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    min_calls: int = Field(default=1, ge=0, le=200, strict=True)
    max_calls: int = Field(default=20, ge=0, le=200, strict=True)
    arguments: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.min_calls > self.max_calls:
            raise ValueError("min_calls must not exceed max_calls")
        return self


class StateMatches(AssertionModel):
    type: Literal["state_matches"]
    fields: dict[str, str | int | None] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def paths(self):
        if set(self.fields) - {"date", "start_time", "end_time", "attendees", "room_code"}:
            raise ValueError("Only declared booking fields can be asserted")
        return self


class ConfirmationBinding(AssertionModel):
    type: Literal["confirmation_binding"]


class OriginalRequestRecovery(AssertionModel):
    type: Literal["original_request_recovery"]


class BusinessConsistent(AssertionModel):
    type: Literal["business_consistent"]


class UniqueBookings(AssertionModel):
    type: Literal["unique_bookings"]


class ReplyLiterals(AssertionModel):
    type: Literal["reply_literals"]
    contains: list[str] = Field(default_factory=list, max_length=30)
    excludes: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def literals(self):
        if not self.contains and not self.excludes:
            raise ValueError("At least one literal is required")
        if any(not x or len(x) > 1000 for x in self.contains + self.excludes):
            raise ValueError("Literals must contain 1 to 1000 characters")
        return self


class ReplyBookingConsistent(AssertionModel):
    type: Literal["reply_booking_consistent"]


Assertion = Annotated[
    BookingMatches
    | BookingCount
    | NoBookingOn
    | ToolCalls
    | StateMatches
    | ConfirmationBinding
    | OriginalRequestRecovery
    | BusinessConsistent
    | UniqueBookings
    | ReplyLiterals
    | ReplyBookingConsistent,
    Field(discriminator="type"),
]


class BookingGoal(AssertionModel):
    """Task fulfilment is separate from compliance; no natural-language self-score."""

    start_at: AwareDatetime
    end_at: AwareDatetime
    attendees: int = Field(ge=1, strict=True)
    room_code: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def interval(self):
        if self.end_at <= self.start_at:
            raise ValueError("Goal end must follow start")
        return self

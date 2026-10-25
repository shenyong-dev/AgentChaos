"""Versioned T03 contracts; the model ToolRegistry adapter arrives in T04."""

from app.schemas.booking import (
    AvailabilityQuery,
    AvailabilityView,
    BookingToolResult,
    BookingView,
    ConfirmBookingToolInput,
    DraftView,
    PreviewBooking,
)
from app.schemas.common import ErrorEnvelope


def booking_tool_contracts():
    definitions = (
        ("query_availability", AvailabilityQuery, AvailabilityView, "read", False),
        ("preview_booking", PreviewBooking, DraftView, "draft_write", False),
        ("confirm_booking", ConfirmBookingToolInput, BookingView, "booking_write", True),
    )
    contracts = []
    for name, input_model, output_model, effect, idempotent in definitions:
        contracts.append(
            {
                "name": name,
                "version": "1.0",
                "effect": effect,
                "input_schema": input_model.model_json_schema(),
                "output_schema": BookingToolResult[output_model].model_json_schema(),
                "idempotent": idempotent,
            }
        )
    contracts.append(
        {
            "name": "lookup_booking_by_request",
            "version": "1.0",
            "effect": "read",
            "input_schema": {
                "type": "object",
                "properties": {"idempotency_key": {"type": "string", "format": "uuid"}},
                "required": ["idempotency_key"],
                "additionalProperties": False,
            },
            "output_schema": BookingToolResult[BookingView | None].model_json_schema(),
            "idempotent": True,
        }
    )
    return {
        "version": "1.0",
        "tools": contracts,
        "error_schema": ErrorEnvelope.model_json_schema(),
        "trusted_context": ["actor_key", "scope_id", "reference_clock", "confirmed"],
        "permission": "authenticated operator; confirmation requires explicit user action",
        "implemented_limits": {
            "actor_lock_wait_seconds": 3,
            "innodb_lock_wait_seconds": 5,
            "db_socket_seconds": 15,
            "candidate_rooms": 50,
        },
        "automatic_retries": 0,
        "unknown_commit": "lookup original idempotency key; never replace key",
        "adapter_limits_for_T04": {
            "max_response_bytes": 16384,
            "wall_timeout_and_confirmation_gate": "implemented in T04",
        },
    }

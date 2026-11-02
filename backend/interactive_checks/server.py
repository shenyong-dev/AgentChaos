"""Lab-only app; restrict mutations to the authenticated interactive workflow."""

import json
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.interactive import get_interaction_service
from app.main import app
from app.services.booking import BookingError, BookingService
from interactive_checks.offline import ControlledGateway

if os.getenv("AC_T17D_OFFLINE") == "1":
    service = get_interaction_service()
    service.gateway_factory = lambda _: ControlledGateway()

control_path = os.getenv("AC_T17D_CONTROL_FILE")
if control_path:
    # Lab-only, explicit after-commit tool-receipt control. It is not a provider outage.
    service = get_interaction_service()
    base_runtime = service.runtime

    class ReceiptControlBooking(BookingService):
        def confirm_booking(self, context, values):
            result = super().confirm_booking(context, values)
            path = Path(control_path)
            selected = json.loads(path.read_bytes()).get("sessions", []) if path.exists() else []
            if context.session_id in selected and service.redis.set(
                "ac:t17d:controlled-receipt:" + context.session_id, "fired", nx=True
            ):
                raise BookingError(
                    "controlled_after_commit_receipt_loss",
                    "明确实验控制：SQL已提交，工具回执丢失；需按原键查证。",
                    503,
                    commit_state="unknown",
                    request_key=values.idempotency_key,
                )
            return result

    def controlled_runtime(session_id):
        runtime = base_runtime(session_id)
        booking = ReceiptControlBooking(service.engine, clock=service.clock)
        runtime.booking = booking
        runtime.registry.booking = booking
        return runtime

    service.runtime = controlled_runtime


@app.middleware("http")
async def owned_scope(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith(
        "/api/v1/interactive/sessions"
    ):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "interactive_lab_only",
                    "message": "本验收环境只允许预约Agent独立会话操作。",
                }
            },
        )
    return await call_next(request)

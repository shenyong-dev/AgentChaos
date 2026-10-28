"""Authenticated read-only replay and native SSE. No model/tool invocation paths."""

import time
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from app.agents.errors import AgentError
from app.api.booking import booking_identity, get_booking_engine
from app.core.config import get_settings
from app.evidence.reader import TraceReader, cursor
from app.schemas.common import Envelope, ErrorEnvelope

router = APIRouter(
    prefix="/api/v1",
    tags=["evidence"],
    responses={status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 410, 503)},
)


@lru_cache
def get_trace_reader():
    return TraceReader(get_booking_engine(), get_settings())


Reader = Annotated[TraceReader, Depends(get_trace_reader)]
Identity = Annotated[int, Depends(booking_identity)]


@router.get("/runs/{run_id}/snapshot")
def snapshot(run_id: UUID, request: Request, user_id: Identity, reader: Reader) -> Envelope[dict]:
    return Envelope(data=reader.snapshot(user_id, run_id), request_id=request.state.request_id)


@router.get("/runs/{run_id}/events")
def events(
    run_id: UUID,
    request: Request,
    user_id: Identity,
    reader: Reader,
    after: Annotated[str | None, Query(alias="cursor", max_length=180)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> Envelope[dict]:
    return Envelope(
        data=reader.page(user_id, run_id, after=after, limit=limit),
        request_id=request.state.request_id,
    )


@router.get("/executions/{execution_id}")
def execution(
    execution_id: UUID, request: Request, user_id: Identity, reader: Reader
) -> Envelope[dict]:
    return Envelope(
        data=reader.execution(user_id, execution_id), request_id=request.state.request_id
    )


@router.get("/executions/{execution_id}/events")
def execution_events(
    execution_id: UUID,
    request: Request,
    user_id: Identity,
    reader: Reader,
    after: Annotated[str | None, Query(alias="cursor", max_length=180)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> Envelope[dict]:
    bound = reader.execution(user_id, execution_id)
    return Envelope(
        data=reader.page(
            user_id, bound["run_id"], after=after, limit=limit, execution_uuid=execution_id
        ),
        request_id=request.state.request_id,
    )


def stream_binding(
    run_id: UUID,
    user_id: Identity,
    reader: Reader,
    after: Annotated[str | None, Query(alias="cursor", max_length=180)] = None,
    last_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=180)] = None,
):
    if after and last_id and after != last_id:
        raise AgentError("cursor_conflict", "查询游标与Last-Event-ID不一致。", 400)
    bound = after or last_id or cursor(str(run_id))
    reader.page(user_id, run_id, after=bound, limit=1)  # Before response headers, via a dependency.
    return {"run_id": str(run_id), "user_id": user_id, "cursor": bound}


@router.get("/runs/{run_id}/events/stream", response_class=EventSourceResponse)
async def stream(
    request: Request, reader: Reader, binding: Annotated[dict, Depends(stream_binding)]
):
    current = binding["cursor"]
    began = time.monotonic()
    yield ServerSentEvent(
        event="stream.ready",
        data={
            "schema_version": "1.3",
            "run_id": binding["run_id"],
            "cursor": current,
            "heartbeat_seconds": 15,
        },
        retry=1000,
    )
    while time.monotonic() - began < reader.settings.sse_max_seconds:
        if await request.is_disconnected():
            return
        try:
            page = await run_in_threadpool(
                reader.page, binding["user_id"], binding["run_id"], after=current, limit=50
            )
        except AgentError as exc:
            yield ServerSentEvent(
                event="stream.reset",
                data={
                    "code": exc.code,
                    "snapshot_required": True,
                    "snapshot_url": f"/api/v1/runs/{binding['run_id']}/snapshot",
                },
            )
            return
        except Exception:
            yield ServerSentEvent(
                event="stream.error", data={"code": "evidence_unavailable", "retryable": True}
            )
            return
        if page["high_watermark"] - int(current.split(":")[2]) > reader.settings.sse_max_backlog:
            yield ServerSentEvent(
                event="stream.reset",
                data={
                    "code": "client_too_far_behind",
                    "snapshot_required": True,
                    "snapshot_url": f"/api/v1/runs/{binding['run_id']}/snapshot",
                },
            )
            return
        for fact in page["items"]:
            yield ServerSentEvent(event="trace", data=fact, id=fact["cursor"])
            current = fact["cursor"]
        if not page["has_more"]:
            if (
                page["run_status"] in ("completed", "failed", "cancelled")
                and not page["pending_model_observations"]
            ):
                yield ServerSentEvent(
                    event="stream.end",
                    data={
                        "cursor": current,
                        "run_status": page["run_status"],
                        "pending_model_observations": 0,
                    },
                )
                return
            await anyio.sleep(0.25)
    yield ServerSentEvent(event="stream.rotate", data={"cursor": current, "reconnect": True})


@router.get("/runs/{run_id}/artifacts/{artifact_id}")
def artifact(run_id: UUID, artifact_id: int, user_id: Identity, reader: Reader):
    path, digest = reader.artifact(user_id, run_id, artifact_id)
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"evidence-{artifact_id}.json",
        headers={"ETag": f'"{digest}"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/runs/{run_id}/events/{event_id}")
def event(
    run_id: UUID, event_id: UUID, request: Request, user_id: Identity, reader: Reader
) -> Envelope[dict]:
    return Envelope(
        data=reader.event(user_id, run_id, event_id), request_id=request.state.request_id
    )

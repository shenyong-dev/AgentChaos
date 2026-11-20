"""External Agent registration, testing and receipt-bound OTLP ingestion."""

import json
import re
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from app.agents.errors import AgentError
from app.api.booking import get_booking_engine
from app.api.cases import case_identity
from app.core.config import get_settings
from app.external.cases import CaseFile
from app.external.otel import BODY_LIMIT, ingest
from app.external.schema import unique_json
from app.external.self_service import Address, Connect
from app.external.service import ExternalService
from app.external.transport import invalid_number
from app.external.web_config import Connection
from app.schemas.common import Envelope

router = APIRouter(prefix="/api/v1/external-agents", tags=["external-agents"])


@lru_cache
def get_external_service():
    return ExternalService(get_booking_engine(), get_settings())


Service = Annotated[ExternalService, Depends(get_external_service)]
Identity = Annotated[int, Depends(case_identity)]


class Register(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registered_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    expected_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approved", "rejected"]


class RegisterConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    connection: Connection
    expected_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]


@router.get("")
def definitions(request: Request, user: Identity, service: Service):
    return Envelope(data=service.definitions(user), request_id=request.state.request_id)


@router.get("/check")
def check(
    request: Request,
    user: Identity,
    service: Service,
    registered_id: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_-]{1,40}$")],
):
    return Envelope(data=service.inspect(user, registered_id), request_id=request.state.request_id)


@router.post("/register")
def register(body: Register, request: Request, user: Identity, service: Service):
    return Envelope(
        data=service.register(user, body.registered_id, body.expected_config_hash),
        request_id=request.state.request_id,
    )


@router.post("/configuration/check")
def check_configuration(body: Connection, request: Request, user: Identity, service: Service):
    return Envelope(
        data=service.check_configuration(user, body), request_id=request.state.request_id
    )


@router.post("/configuration/register")
def register_configuration(
    body: RegisterConnection, request: Request, user: Identity, service: Service
):
    return Envelope(
        data=service.register_configuration(user, body.connection, body.expected_config_hash),
        request_id=request.state.request_id,
    )


@router.post("/self-service/challenge")
def self_challenge(body: Address, request: Request, user: Identity, service: Service):
    return Envelope(
        data=service.self_challenge(user, body.endpoint, body.protocol),
        request_id=request.state.request_id,
    )


@router.post("/self-service/connect")
def self_connect(body: Connect, request: Request, user: Identity, service: Service):
    return Envelope(data=service.connect(user, body), request_id=request.state.request_id)


@router.get("/trace-setup")
def trace_setup(
    request: Request,
    user: Identity,
    service: Service,
    registered_id: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_-]{1,40}$")],
):
    return Envelope(
        data=service.trace_setup(user, registered_id), request_id=request.state.request_id
    )


@router.post("/otlp/v1/traces")
async def receive_traces(request: Request):
    authorization = re.fullmatch(r"Bearer ([a-f0-9]{64})", request.headers.get("authorization", ""))
    if not authorization:
        raise AgentError("external_trace_auth", "请提供对象的轨迹接收凭据。", 401)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (
        content_type != "application/json"
        or request.headers.get("content-encoding", "identity") != "identity"
    ):
        raise AgentError("external_trace_format", "请选择 OTLP/HTTP JSON，使用未压缩发送。", 415)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > BODY_LIMIT:
            raise AgentError("external_trace_limit", "单批轨迹超过接收上限。", 413)
        body.extend(chunk)

    def write():
        settings = get_settings()
        payload = json.loads(body, object_pairs_hook=unique_json, parse_constant=invalid_number)
        with Redis.from_url(
            settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        ) as client:
            return ingest(client, settings, authorization[1], payload)

    try:
        return await run_in_threadpool(write)
    except (ValueError, RecursionError, TypeError, AttributeError):
        raise AgentError("external_trace_invalid", "轨迹格式不符合 OTLP/HTTP JSON。", 400) from None
    except RedisError:
        raise AgentError("external_trace_unavailable", "暂时无法接收轨迹。", 503) from None


@router.get("/cases")
def cases(request: Request, user: Identity, service: Service):
    return Envelope(data=service.cases(user), request_id=request.state.request_id)


@router.post("/cases", status_code=201)
def import_case(body: CaseFile, request: Request, user: Identity, service: Service):
    return Envelope(data=service.import_case(user, body), request_id=request.state.request_id)


@router.post("/cases/{case_id}/review")
def review(case_id: int, body: Review, request: Request, user: Identity, service: Service):
    return Envelope(
        data=service.review(user, case_id, body.expected_content_hash, body.decision),
        request_id=request.state.request_id,
    )

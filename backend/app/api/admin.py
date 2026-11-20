import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.agents.errors import AgentError
from app.auth.admin import AdminService
from app.auth.login_events import RETENTION_DAYS, list_login_events
from app.auth.service import SessionService, get_session_service
from app.core.roles import AccountRole
from app.executors.service import RunService
from app.schemas.common import Envelope

router = APIRouter(prefix="/api/v1/admin", tags=["platform-admin"])
Auth = Annotated[SessionService, Depends(get_session_service)]
log = logging.getLogger("agentchaos.admin")


class CreateAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,31}$")
    password: SecretStr = Field(min_length=12, max_length=128)
    role: AccountRole = "user"


class UpdateAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: AccountRole | None = None
    enabled: bool | None = Field(default=None, strict=True)
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)

    @model_validator(mode="after")
    def requested(self):
        if self.role is None and self.enabled is None and self.password is None:
            raise ValueError("No account changes")
        return self


class ModelCalls(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


def administrator(request: Request, auth: Auth):
    record = auth.resolve(request, touch=request.headers.get("x-ac-activity") == "1")
    if record["role"] != "admin":
        raise AgentError("admin_required", "此操作仅限平台管理员。", 403)
    return record["user_id"], AdminService(auth)


Admin = Annotated[tuple, Depends(administrator)]
Cursor = Annotated[int, Query(ge=0, le=18446744073709551615)]


@router.get("/summary")
def summary(request: Request, context: Admin):
    _, service = context
    return Envelope(data=service.summary(), request_id=request.state.request_id)


@router.patch("/model-calls")
def model_calls(body: ModelCalls, request: Request, context: Admin):
    actor, service = context
    return Envelope(
        data=service.change_model_calls(actor, body.enabled), request_id=request.state.request_id
    )


@router.patch("/accounts/{target}/model-calls")
def account_model_calls(
    body: ModelCalls, request: Request, context: Admin, target: Annotated[int, Path(gt=0)]
):
    actor, service = context
    return Envelope(
        data=service.change_model_calls(actor, body.enabled, target),
        request_id=request.state.request_id,
    )


@router.get("/accounts")
def accounts(
    request: Request,
    context: Admin,
    cursor: Cursor = 0,
    query: Annotated[str, Query(max_length=40)] = "",
):
    _, service = context
    return Envelope(
        data=service.accounts(cursor, query.strip()), request_id=request.state.request_id
    )


@router.post("/accounts", status_code=201)
def create_account(body: CreateAccount, request: Request, context: Admin):
    actor, service = context
    return Envelope(data=service.create(actor, body), request_id=request.state.request_id)


@router.patch("/accounts/{target}")
def update_account(
    body: UpdateAccount, request: Request, context: Admin, target: Annotated[int, Path(gt=0)]
):
    actor, service = context
    return Envelope(data=service.change(actor, target, body), request_id=request.state.request_id)


@router.get("/sessions")
def sessions(request: Request, context: Admin, cursor: Cursor = 0):
    _, service = context
    return Envelope(data=service.sessions(request, cursor), request_id=request.state.request_id)


@router.get("/login-events")
def login_events(
    request: Request,
    context: Admin,
    cursor: Cursor = 0,
    query: Annotated[str, Query(max_length=64)] = "",
    outcome: Literal["success", "failure", "limited"] | None = None,
    user_id: Annotated[int | None, Query(ge=1, le=18446744073709551615)] = None,
    days: Annotated[int, Query(ge=1, le=RETENTION_DAYS)] = RETENTION_DAYS,
):
    _, service = context
    return Envelope(
        data=list_login_events(
            service.engine,
            cursor=cursor,
            query=query.strip(),
            outcome=outcome,
            user_id=user_id,
            days=days,
        ),
        request_id=request.state.request_id,
    )


@router.delete("/sessions/{identifier}")
def revoke(
    request: Request, context: Admin, identifier: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]
):
    actor, service = context
    return Envelope(
        data=service.revoke(request, actor, identifier), request_id=request.state.request_id
    )


@router.get("/runs")
def runs(request: Request, context: Admin, cursor: Cursor = 0):
    _, service = context
    return Envelope(data=service.runs(cursor), request_id=request.state.request_id)


@router.get("/runs/{run_id}")
def run_detail(run_id: UUID, request: Request, context: Admin):
    actor, service = context
    run = RunService(service.engine, service.auth.settings).read(actor, run_id, administrator=True)
    return Envelope(
        data={
            "run_id": run["run_id"],
            "status": run["status"],
            "executions": [
                {key: row[key] for key in ("execution_id", "case_key", "status", "attempt")}
                for row in run["executions"]
            ],
        },
        request_id=request.state.request_id,
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: UUID, request: Request, context: Admin):
    from app.external.receipts import cancel_remote

    actor, service = context
    settings = service.auth.settings
    owner = RunService(service.engine, settings).cancel(actor, run_id, administrator=True)
    log.info("run cancellation requested actor=%s run=%s owner=%s", actor, run_id, owner)
    cancel_remote(service.engine, settings, owner, run_id)
    return Envelope(data={"cancel_requested": True}, request_id=request.state.request_id)

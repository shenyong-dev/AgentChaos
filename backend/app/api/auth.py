from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth.service import SessionService, get_session_service
from app.schemas.common import Envelope, ErrorEnvelope

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["browser-session"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 429, 503)},
)
Service = Annotated[SessionService, Depends(get_session_service)]


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)


def public(record, service):
    return {
        "user_id": record["user_id"],
        "username": record["username"],
        "role": record["role"],
        "csrf_token": record["csrf"],
        "idle_expires_at": record["idle"],
        "absolute_expires_at": record["absolute"],
        "idle_seconds": service.settings.session_idle_seconds,
    }


@router.post("/login")
def login(body: Login, request: Request, response: Response, service: Service) -> Envelope[dict]:
    token, record = service.login(request, body.username, body.password.get_secret_value())
    response.set_cookie(
        service.cookie_name,
        token,
        httponly=True,
        samesite="strict",
        secure=service.settings.app_env == "production" or request.url.scheme == "https",
        path="/api/v1",
        max_age=service.settings.session_absolute_seconds,
    )
    return Envelope(data=public(record, service), request_id=request.state.request_id)


@router.get("/session")
def current(request: Request, service: Service) -> Envelope[dict]:
    return Envelope(
        data=public(
            service.resolve(request, touch=request.headers.get("x-ac-activity") == "1"), service
        ),
        request_id=request.state.request_id,
    )


@router.post("/logout")
def logout(request: Request, response: Response, service: Service) -> Envelope[dict]:
    service.logout(request)
    response.delete_cookie(
        service.cookie_name,
        path="/api/v1",
        httponly=True,
        samesite="strict",
        secure=service.settings.app_env == "production" or request.url.scheme == "https",
    )
    return Envelope(data={"logged_out": True}, request_id=request.state.request_id)

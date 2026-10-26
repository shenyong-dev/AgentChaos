import logging
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app import __version__
from app.agents.errors import AgentError
from app.api.agent import router as agent_router
from app.api.booking import router as booking_router
from app.api.evidence import router as evidence_router
from app.api.routes import router
from app.api.runs import router as runs_router
from app.core.config import get_settings
from app.evidence.streaming import SSESendDeadlineMiddleware
from app.services.booking import BookingError

logger = logging.getLogger("agentchaos")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AgentChaos API",
        version=__version__,
        description="真实预约Agent、持久批量执行、事件证据与SSE；断言评价尚未实现。",
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        try:
            request_id = str(UUID(supplied))
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            # Keep credentials/driver details out of both the response and routine logs.
            logger.error("Unhandled request failure; request_id=%s", request_id)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {"code": "internal_error", "message": "服务内部错误。", "details": []},
                    "request_id": request_id,
                },
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"http_{exc.status_code}",
                    "message": "请求的资源不可用。",
                    "details": [],
                },
                "request_id": request.state.request_id,
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo rejected inputs, which can contain credentials or user data.
        details = [
            {"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "请求参数不符合契约。",
                    "details": details,
                },
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(BookingError)
    async def booking_error(request: Request, exc: BookingError):
        detail = {"retryable": exc.retryable, "commit_state": exc.commit_state}
        if exc.request_key is not None:
            detail["request_key"] = str(exc.request_key)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": [detail]},
                "request_id": request.state.request_id,
            },
            headers={"WWW-Authenticate": "Basic"} if exc.status_code == 401 else None,
        )

    app.include_router(router)
    app.include_router(booking_router)
    app.include_router(agent_router)
    app.include_router(runs_router)
    app.include_router(evidence_router)

    @app.exception_handler(AgentError)
    async def agent_error(request: Request, exc: AgentError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": []},
                "request_id": request.state.request_id,
            },
        )

    app.add_middleware(SSESendDeadlineMiddleware, timeout=get_settings().sse_send_timeout_seconds)
    return app


app = create_app()

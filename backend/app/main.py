import logging
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app import __version__
from app.api.routes import router

logger = logging.getLogger("agentchaos")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AgentChaos API",
        version=__version__,
        description="T01 工程基础；评测、预约与模型执行接口尚未实现。",
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

    app.include_router(router)
    return app


app = create_app()

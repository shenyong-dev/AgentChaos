"""Read-only HTTP view: no provider credential, no experiment actions."""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.main import app


@app.middleware("http")
async def read_only(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "t17c_read_only",
                    "message": "此验收入口只提供环境检查与已有记录浏览。",
                }
            },
        )
    return await call_next(request)

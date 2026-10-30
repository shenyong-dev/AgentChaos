"""Read-only T17A result browsing; provider keys and paid switches are absent."""

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
                    "code": "t17a_review_read_only",
                    "message": "T17A结果只读，不新增调用或预约。",
                }
            },
        )
    return await call_next(request)

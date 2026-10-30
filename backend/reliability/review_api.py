"""Free read-only review of the owned T17 control database."""

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
                    "code": "t17_review_read_only",
                    "message": "T17验收入口只读，不能续跑或新增调用。",
                }
            },
        )
    return await call_next(request)

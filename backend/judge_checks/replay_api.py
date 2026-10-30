"""Read-only T17B result replay; no annotation, provider key, worker or paid calls."""

import json

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
                    "code": "t17b_replay_read_only",
                    "message": "T17B原结果只读，保留人工标签与AI分歧。",
                }
            },
        )
    response = await call_next(request)
    if request.url.path == "/api/v1/judging/access" and response.status_code == 200:
        data = json.loads(b"".join([part async for part in response.body_iterator]))
        data["data"]["read_only"] = True
        return JSONResponse(
            content=data,
            headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
        )
    return response

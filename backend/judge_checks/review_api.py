"""Human labeling only; this HTTP process has no provider key or paid capability."""

import os
import re

from fastapi import Request
from fastapi.responses import JSONResponse

from app.main import app


@app.middleware("http")
async def labels_only(request: Request, call_next):
    batch = re.escape(os.environ["AC_T17B_BATCH"])
    permitted = re.fullmatch(
        rf"/api/v1/judging/calibrations/{batch}/(?:seal|samples/B\d{{3}}/label)",
        request.url.path,
    )
    if request.method not in ("GET", "HEAD", "OPTIONS") and not (
        request.method == "POST" and permitted
    ):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "t17b_human_label_only",
                    "message": "此入口仅允许本批真人标注与封存。",
                }
            },
        )
    return await call_next(request)

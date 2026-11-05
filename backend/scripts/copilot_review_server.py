"""Serve only an explicitly owned read-only T17E review database."""

import argparse
import json
import os
import time
from pathlib import Path

from fastapi.responses import JSONResponse

from app.core import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    path = Path(args.config).resolve()
    assert path.parent.parent == config.PROJECT_ROOT / ".local/t17e"
    updates = json.loads(path.read_bytes())
    assert updates["mysql_database"].startswith("agentchaos_t17e_verify_")
    settings = config.Settings(**updates)
    assert not any(
        (
            settings.allow_paid_model_calls,
            settings.executor_allow_paid_model_calls,
            settings.casegen_allow_paid_model_calls,
            settings.judge_allow_paid_model_calls,
        )
    )
    config.get_settings = lambda: settings
    from app.main import create_app

    app = create_app()

    @app.middleware("http")
    async def review_writes(request, call_next):
        path = request.url.path
        allowed = path in ("/api/v1/auth/login", "/api/v1/auth/logout") or path.startswith(
            "/api/v1/copilot/"
        )
        expired = time.time() >= float(os.getenv("AC_COPILOT_REVIEW_DEADLINE", "inf"))
        if expired and request.method not in ("GET", "HEAD", "OPTIONS"):
            allowed = False
        if request.method not in ("GET", "HEAD", "OPTIONS") and not allowed:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "review_read_only",
                        "message": "验收环境只允许助手提问和登录，不提交业务操作。",
                        "details": [],
                    },
                    "request_id": "review-read-only",
                },
            )
        return await call_next(request)

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=18023)


if __name__ == "__main__":
    main()

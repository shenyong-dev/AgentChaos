"""Bounded review supervisor; only owned processes and disposable SQL are retired."""

import json
import time
from datetime import UTC, datetime, timedelta

from app.core.config import PROJECT_ROOT
from reliability.lab import stop_process


def serve(lab, summary, seconds):
    assert 60 <= seconds <= 21600
    stop_process(lab.api)
    lab.api_module = "reliability.review_api:app"
    lab.start_api()
    lab.spawn(
        [
            "D:/node.js/node.exe",
            str(PROJECT_ROOT / "frontend/node_modules/vite/bin/vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            "5184",
            "--strictPort",
        ],
        {"AC_API_PROXY_TARGET": "http://127.0.0.1:18017"},
        PROJECT_ROOT / "frontend",
        "review-ui",
    )
    stop_file = lab.local / "stop-review"
    now = datetime.now(UTC)
    record = {
        "marker": lab.marker,
        "api": "http://127.0.0.1:18017",
        "ui": "http://127.0.0.1:5184",
        "started_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=seconds)).isoformat(),
        "stop_file": str(stop_file),
        "evidence": str(lab.output),
        "read_only": True,
        "paid_calls": 0,
        "summary": summary,
    }
    lab.save("review-session.json", record)
    (PROJECT_ROOT / ".local/t17/latest-review.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"review": record}, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not stop_file.exists():
        time.sleep(1)

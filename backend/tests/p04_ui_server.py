"""Disposable HTTP/browser fixture; never the actual participant or real model."""

import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from test_p04_trial import Double

from app.core.config import PROJECT_ROOT
from benchmarks.p04_trial import collector as c

controls = Path(
    os.getenv(
        "AC_P04_CONTROL_ROOT",
        str(PROJECT_ROOT / ".local/p04-usability-fix-20261115-r1/ui-controls"),
    )
)
controls.mkdir(parents=True, exist_ok=True)
c.ROOT = Path(tempfile.mkdtemp(prefix="mechanical-", dir=controls))
runtime = Double()
server = HTTPServer(
    ("127.0.0.1", int(os.getenv("AC_P04_CONTROL_PORT", "0"))), BaseHTTPRequestHandler
)
folder, config = c.prepare(runtime, "P01", server.server_port)
config = {**config, "port": server.server_port, "folder": str(folder)}
if os.getenv("AC_P04_PREVIEW") == "1":
    c.emit(
        folder,
        config,
        [],
        "start",
        {"attestation": "explicit_mechanical_preview_not_human", "task_id": "U01"},
    )
    c.mutate(
        folder,
        config,
        {
            "csrf": [config["csrf"]],
            "action": ["ask"],
            "request_id": ["00000000-0000-4000-8000-000000000055"],
            "question": ["机械预览：查看四个证据栏目"],
        },
        runtime,
    )
original_render = c.render


def control_render(*args, **kwargs):
    body = original_render(*args, **kwargs)
    body = body.replace(
        b"<main>",
        '<main><p role="note" class="cost">机械界面预览 · 回复为替身 · 不调用模型，不计真人试用</p>'.encode(),  # noqa: E501
    )
    return body.replace(
        (
            "发送将调用真实AI并计费，本页草稿一并保存。累计金额与次数不限，单次请求最多45秒。"
        ).encode(),
        "本页使用机械回复，提问不调用模型；草稿只保存在本机预览目录。".encode(),
    )


c.render = control_render
server.RequestHandlerClass = c.handler(folder, config, runtime)
print(
    json.dumps(
        {
            "url": f"http://127.0.0.1:{server.server_port}/trial/{config['token']}",
            "mode": "explicit_mechanical_browser_fixture",
        }
    ),
    flush=True,
)
server.serve_forever()

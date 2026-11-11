"""Disposable browser control; never the real E4 participant directory or gateway."""

import json
import os
import tempfile
from http.server import HTTPServer
from pathlib import Path

from test_t19b_ui import ExplicitRuntimeDouble

from app.core.config import PROJECT_ROOT
from benchmarks.t19b_ui import collector as c

controls = PROJECT_ROOT / ".local/t19b/ui-controls"
controls.mkdir(parents=True, exist_ok=True)
c.ROOT = Path(tempfile.mkdtemp(prefix="explicit-ui-", dir=controls))
runtime = ExplicitRuntimeDouble()
runtime.fail = os.getenv("AC_T19B_CONTROL_UNKNOWN") == "1"
folder, config = c.prepare(runtime, "P01", 18037)
server = HTTPServer(("127.0.0.1", 0), c.handler(folder, config, runtime))
config["port"] = server.server_port
server.RequestHandlerClass = c.handler(folder, config, runtime)
print(
    json.dumps(
        {
            "url": f"http://127.0.0.1:{server.server_port}/trial/{config['token']}",
            "records": str(folder / "events.jsonl"),
            "mode": "explicit_mechanical_control_not_human_evidence",
        }
    ),
    flush=True,
)
server.serve_forever()

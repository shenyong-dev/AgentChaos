"""Disposable browser control only; never uses the real P01 trial directory."""

import json
import tempfile
from http.server import HTTPServer
from pathlib import Path

from app.core.config import PROJECT_ROOT
from benchmarks.t19 import collector as c

controls = PROJECT_ROOT / ".local/t19/controls"
controls.mkdir(parents=True, exist_ok=True)
folder = Path(tempfile.mkdtemp(prefix="ui-", dir=controls))
c.ROOT = folder / "participants"
protocol = json.loads(c.PROTOCOL.read_text(encoding="utf-8"))
protocol["mode"] = "explicit_mechanical_ui_control_not_human_evidence"
control_protocol = folder / "protocol.json"
control_protocol.write_text(json.dumps(protocol, ensure_ascii=False), encoding="utf-8")
c.PROTOCOL = control_protocol
c.source_manifest = lambda: {"mechanical_ui_control": "0" * 64}
participant, config = c.prepare("P01", 18035)
server = HTTPServer(("127.0.0.1", 0), c.handler(participant, config))
config["port"] = server.server_port
server.RequestHandlerClass = c.handler(participant, config)
print(
    json.dumps(
        {
            "url": f"http://127.0.0.1:{server.server_port}/trial/{config['token']}",
            "records": str(participant / "events.jsonl"),
            "mode": protocol["mode"],
        }
    ),
    flush=True,
)
server.serve_forever()

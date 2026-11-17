"""Project-authored free HTTP dialogue example. No model or business actions.

Run from backend: python -m examples.external_agent.server --port 18056 --database PATH
Supply AGENTCHAOS_EXTERNAL_DEMO_TOKEN in the process environment. Never pass it in argv.
"""

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from app.external.schema import PROTOCOL, Binding, Envelope, Send, unique_json

CARD = {
    "protocol": PROTOCOL,
    "name": "本地协议示例（免费回声）",
    "revision": "echo-v1",
    "side_effects": False,
    "request_lookup": True,
    "cancellation": True,
    "cancel_before_execute": True,
    "billing": "free",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Store:
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(binding TEXT PRIMARY KEY, revision INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS receipts (binding TEXT, request_id TEXT, "
                "operation TEXT, input_hash TEXT, response TEXT, "
                "PRIMARY KEY(binding,request_id,operation))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cancellations (binding TEXT, request_id TEXT, "
                "input_hash TEXT, PRIMARY KEY(binding, request_id))"
            )

    def handle(self, envelope):
        binding = digest(envelope["binding"])
        operation, key, data = envelope["operation"], envelope["request_id"], envelope["data"]
        sha = digest(data)
        if operation == "capabilities":
            if data:
                raise ValueError("Empty capability body required")
            return CARD
        with sqlite3.connect(self.path, timeout=3) as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT input_hash,response FROM receipts "
                "WHERE binding=? AND request_id=? AND operation=?",
                (binding, key, operation),
            ).fetchone()
            if prior:
                if prior[0] != sha:
                    raise ValueError("Request conflict")
                return json.loads(prior[1])
            session = connection.execute(
                "SELECT revision FROM sessions WHERE binding=?", (binding,)
            ).fetchone()
            if operation == "lookup":
                if set(data) != {"original_operation", "input_hash"} or data[
                    "original_operation"
                ] not in {"prepare", "execute"}:
                    raise ValueError("Invalid lookup")
                prior = connection.execute(
                    "SELECT input_hash,response FROM receipts "
                    "WHERE binding=? AND request_id=? AND operation=?",
                    (binding, key, data["original_operation"]),
                ).fetchone()
                if prior and prior[0] != data["input_hash"]:
                    raise ValueError("Original request mismatch")
                return {
                    "status": "found" if prior else "not_found",
                    "original_operation": data["original_operation"],
                    "input_hash": data["input_hash"],
                    "result": json.loads(prior[1]) if prior else None,
                }
            if operation == "prepare":
                if data or session is not None:
                    raise ValueError("Only a new scope can be prepared")
                connection.execute("INSERT INTO sessions VALUES (?,0)", (binding,))
                response = {"ready": True, "input_hash": sha}
            elif operation == "execute":
                values = Send.model_validate(data)
                if session is None or session[0] != values.expected_revision:
                    raise ValueError("Session revision mismatch")
                stopped = connection.execute(
                    "SELECT input_hash FROM cancellations WHERE binding=? AND request_id=?",
                    (binding, key),
                ).fetchone()
                if stopped and stopped[0] != sha:
                    raise ValueError("Cancelled request hash mismatch")
                revision = session[0] + 1
                response = {
                    "state": "cancelled" if stopped else "completed",
                    "task_id": "echo_" + key.replace("-", ""),
                    "revision": revision,
                    "assistant_text": "" if stopped else "收到：" + values.text,
                    "input_hash": sha,
                    "usage": {"model_calls": 0, "tokens": 0, "cost_microusd": 0},
                }
                connection.execute(
                    "UPDATE sessions SET revision=? WHERE binding=?", (revision, binding)
                )
            elif operation == "cancel":
                if set(data) != {"original_request_id", "input_hash"}:
                    raise ValueError("Invalid cancellation")
                original = connection.execute(
                    "SELECT input_hash FROM receipts "
                    "WHERE binding=? AND request_id=? AND operation='execute'",
                    (binding, data["original_request_id"]),
                ).fetchone()
                if session is None or original and original[0] != data["input_hash"]:
                    raise ValueError("Unknown session or mismatched request")
                if original:
                    response = {"status": "already_terminal"}
                else:
                    previous = connection.execute(
                        "SELECT input_hash FROM cancellations WHERE binding=? AND request_id=?",
                        (binding, data["original_request_id"]),
                    ).fetchone()
                    if previous and previous[0] != data["input_hash"]:
                        raise ValueError("Cancelled request hash mismatch")
                    connection.execute(
                        "INSERT OR IGNORE INTO cancellations VALUES (?,?,?)",
                        (binding, data["original_request_id"], data["input_hash"]),
                    )
                    response = {"status": "confirmed"}
            else:
                raise ValueError("Unsupported operation")
            connection.execute(
                "INSERT INTO receipts VALUES (?,?,?,?,?)",
                (binding, key, operation, sha, json.dumps(response, ensure_ascii=False)),
            )
            return response


def make_server(path, token, *, port=0, drop_first_execute=False):
    """Loss mode is a local transport control, never evidence of real Agent quality."""
    store = Store(path)
    control = {"drop": drop_first_execute, "operations": []}
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log credentials, paths or message bodies.

        def do_POST(self):
            self.close_connection = True
            if self.path != "/agentchaos/v1" or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            ):
                self.send_error(403)
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Length required")
                size = int(lengths[0])
                if not 0 < size <= 65536:
                    raise ValueError("Body limit")
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError("Incomplete body")
                envelope = Envelope.model_validate(
                    json.loads(raw, object_pairs_hook=unique_json)
                ).model_dump()
                Binding.model_validate(envelope["binding"])
                if envelope["revision"] != CARD["revision"]:
                    raise ValueError("Revision mismatch")
                data = store.handle(envelope)
                with lock:
                    control["operations"].append(envelope["operation"])
                    if control["drop"] and envelope["operation"] == "execute":
                        control["drop"] = False
                        return  # SQLite commit already happened; do not return a receipt.
                result = json.dumps({**envelope, "data": data}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(result)
            except (ValueError, sqlite3.Error):
                self.send_error(409, "Protocol request rejected")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.socket.settimeout(3)
    return server, control


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18056)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("AGENTCHAOS_EXTERNAL_DEMO_TOKEN")
    if not token:
        parser.exit(2, "Set the demo token in the process environment.\n")
    if args.database.exists():
        parser.exit(2, "Use a new ledger path; preserve old demonstration evidence.\n")
    server, _ = make_server(args.database, token, port=args.port)
    try:
        print(f"Local free protocol example: http://127.0.0.1:{server.server_port}/agentchaos/v1")
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

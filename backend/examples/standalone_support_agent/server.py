"""Expose the standalone triage agent through a localhost HTTP adapter."""

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

from triage import reply

CARD = {
    "protocol": "agentchaos-http-1",
    "name": "售后工单分流 Agent",
    "revision": "support-v1",
    "side_effects": False,
    "request_lookup": True,
    "cancellation": True,
    "cancel_before_execute": True,
    "billing": "free",
}
FIELDS = {"protocol", "revision", "operation", "request_id", "binding", "data"}
LIMITS = {
    "max_model_calls": (1, 1),
    "max_tokens": (1, 131072),
    "max_cost_microusd": (0, 100_000),
    "timeout_seconds": (1, 45),
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return result


def validate(message):
    if not isinstance(message, dict) or message.keys() != FIELDS:
        raise ValueError("Invalid fields")
    if (message["protocol"], message["revision"]) != (CARD["protocol"], CARD["revision"]):
        raise ValueError("Version mismatch")
    binding = message["binding"]
    if not isinstance(binding, dict) or binding.keys() != {"tenant", "scope", "session_id"}:
        raise ValueError("Invalid binding")
    if not re.fullmatch(r"[a-f0-9]{64}", binding["tenant"]) or not re.fullmatch(
        r"test:[A-Za-z0-9][A-Za-z0-9:_-]{0,58}", binding["scope"]
    ):
        raise ValueError("Invalid tenant or scope")
    for identifier in (message["request_id"], binding["session_id"]):
        if str(UUID(identifier)) != identifier:
            raise ValueError("Invalid identifier")
    if not isinstance(message["data"], dict):
        raise ValueError("Invalid data")


class Store:
    def __init__(self, path, *, resume=False):
        self.path = path
        if resume:
            if not path.is_file():
                raise ValueError("Existing support ledger required")
            with closing(sqlite3.connect(path)) as db:
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("Unrecognized support ledger")
            return
        path.touch(exist_ok=False)
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE sessions (scope TEXT PRIMARY KEY, revision INTEGER, context TEXT);
                CREATE TABLE receipts (
                  scope TEXT, request_id TEXT, operation TEXT, digest TEXT, response TEXT,
                  PRIMARY KEY (scope, request_id, operation)
                );
                CREATE TABLE cancellations (
                  scope TEXT, request_id TEXT, digest TEXT, PRIMARY KEY (scope, request_id)
                );
                CREATE TABLE controls (name TEXT PRIMARY KEY);
                PRAGMA user_version=1;
            """)

    def handle(self, message, drop_on_text):
        scope = fingerprint(message["binding"])
        operation, key, data = message["operation"], message["request_id"], message["data"]
        digest = fingerprint(data)
        if operation == "capabilities":
            if data:
                raise ValueError("Expected empty data")
            return CARD, False
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.execute("BEGIN IMMEDIATE")

            def receipt(request_id, kind):
                return db.execute(
                    "SELECT digest, response FROM receipts "
                    "WHERE scope=? AND request_id=? AND operation=?",
                    (scope, request_id, kind),
                ).fetchone()

            prior = receipt(key, operation)
            if prior:
                if prior[0] != digest:
                    raise ValueError("Request conflict")
                return json.loads(prior[1]), False

            if operation == "lookup":
                original = data["original_operation"]
                wanted = data["input_hash"]
                if (
                    data.keys() != {"original_operation", "input_hash"}
                    or original not in {"execute", "prepare"}
                    or not re.fullmatch("[a-f0-9]{64}", wanted)
                ):
                    raise ValueError("Invalid lookup")
                prior = receipt(key, original)
                if prior and prior[0] != wanted:
                    raise ValueError("Lookup hash mismatch")
                return {
                    "status": "found" if prior else "not_found",
                    "original_operation": original,
                    "input_hash": wanted,
                    "result": json.loads(prior[1]) if prior else None,
                }, False

            session = db.execute(
                "SELECT revision, context FROM sessions WHERE scope=?", (scope,)
            ).fetchone()
            drop = False
            if operation == "prepare":
                if data or session:
                    raise ValueError("Expected a new session")
                db.execute("INSERT INTO sessions VALUES (?,0,'{}')", (scope,))
                response = {"ready": True, "input_hash": digest}
            elif operation == "execute":
                values = data["text"]
                requested_revision = data["expected_revision"]
                if (
                    data.keys() != {"text", "expected_revision", "limits"}
                    or not isinstance(values, str)
                    or not 0 < len(values) <= 12000
                    or type(requested_revision) is not int
                    or (not session or session[0] != requested_revision)
                    or not isinstance(data["limits"], dict)
                ):
                    raise ValueError("Invalid message or revision")
                limits = data["limits"]
                if limits.keys() != LIMITS.keys() or any(
                    type(limits[field]) is not int or not low <= limits[field] <= high
                    for field, (low, high) in LIMITS.items()
                ):
                    raise ValueError("Invalid request limits")
                stopped = db.execute(
                    "SELECT digest FROM cancellations WHERE scope=? AND request_id=?", (scope, key)
                ).fetchone()
                if stopped and stopped[0] != digest:
                    raise ValueError("Cancellation bound to another message")
                context = json.loads(session[1])
                context, answer = (context, "") if stopped else reply(context, values)
                revision = requested_revision + 1
                db.execute(
                    "UPDATE sessions SET revision=?, context=? WHERE scope=?",
                    (revision, encode(context), scope),
                )
                response = {
                    "state": "cancelled" if stopped else "completed",
                    "task_id": "support_" + UUID(key).hex,
                    "revision": revision,
                    "assistant_text": answer,
                    "input_hash": digest,
                    "usage": {"model_calls": 0, "tokens": 0, "cost_microusd": 0},
                }
                if not stopped and drop_on_text is not None and values == drop_on_text:
                    drop = (
                        db.execute(
                            "INSERT OR IGNORE INTO controls VALUES ('lost_response')"
                        ).rowcount
                        == 1
                    )
            elif operation == "cancel":
                original, wanted = data["original_request_id"], data["input_hash"]
                if (
                    not session
                    or data.keys() != {"original_request_id", "input_hash"}
                    or str(UUID(original)) != original
                    or not re.fullmatch("[a-f0-9]{64}", wanted)
                ):
                    raise ValueError("Invalid cancellation")
                prior = receipt(original, "execute")
                stopped = db.execute(
                    "SELECT digest FROM cancellations WHERE scope=? AND request_id=?",
                    (scope, original),
                ).fetchone()
                if (prior and prior[0] != wanted) or (stopped and stopped[0] != wanted):
                    raise ValueError("Cancellation hash mismatch")
                if not prior:
                    db.execute(
                        "INSERT OR IGNORE INTO cancellations VALUES (?,?,?)",
                        (scope, original, wanted),
                    )
                response = {"status": "already_terminal" if prior else "confirmed"}
            else:
                raise ValueError("Unknown operation")
            db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?,?)",
                (scope, key, operation, digest, encode(response)),
            )
            return response, drop


def make_server(port, token, store, drop_on_text):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(6)
            self.close_connection = True
            if self.path != "/agentchaos/v1" or not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
            ):
                self.send_error(403)
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Invalid framing")
                length = int(lengths[0])
                if not 0 < length <= 65536:
                    raise ValueError("Invalid length")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                message = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
                validate(message)
                result, drop = store.handle(message, drop_on_text)
                if drop:
                    return  # The transaction committed before the intentional test-only loss.
                body = encode({**message, "data": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (KeyError, TypeError, ValueError, sqlite3.Error):
                self.send_error(409, "Protocol request rejected")
            except OSError:
                pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18064)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--resume", action="store_true", help="Reopen the same support ledger")
    parser.add_argument(
        "--drop-on-text", help="Local test only: omit one matching committed receipt"
    )
    args = parser.parse_args()
    token = os.environ.get("AGENTCHAOS_EXTERNAL_SUPPORT_TOKEN")
    if not token:
        parser.error("Set AGENTCHAOS_EXTERNAL_SUPPORT_TOKEN.")
    store = Store(args.database, resume=args.resume)
    with make_server(args.port, token, store, args.drop_on_text) as server:
        print(f"Support triage listening on localhost:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()

"""Independent, no-model helpdesk sandbox for trying mapped HTTP, Dify and OTLP locally."""

import argparse
import hmac
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

POLICY = "returns-policy-v1"


class TestToolError(Exception):
    pass


class Helpdesk:
    def __init__(self, database):
        self.database = database
        with self.open() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tickets (request_id TEXT, session_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS requests (request_id TEXT, session_id TEXT)")

    def open(self):
        return sqlite3.connect(self.database, timeout=3)

    def facts(self, request_id, session_id):
        with self.open() as db:
            counts = {
                name: db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE request_id=? AND session_id=?",
                    (request_id, session_id),
                ).fetchone()[0]
                for name, table in (("write_count", "tickets"), ("request_count", "requests"))
            }
        return {"request_id": request_id, "session_id": session_id, **counts}

    def respond(self, request_id, session_id, query, *, tool_fault):
        submit = "创建工单" in query
        with self.open() as db:
            db.execute("INSERT INTO requests VALUES (?,?)", (request_id, session_id))
        actions = [
            ("lookup_policy", {"policy_id": POLICY}, False),
        ]
        if submit:
            try:
                self.create_ticket(request_id, session_id, fail=tool_fault)
            except TestToolError:
                actions.append(("create_ticket", {"category": "return"}, True))
                self.create_ticket(request_id, session_id, fail=False)
            actions.append(("create_ticket", {"category": "return"}, False))
        return ("工单已创建" if submit else "已找到退货政策"), actions

    def create_ticket(self, request_id, session_id, *, fail):
        if fail:
            raise TestToolError("Sandbox tool failed before writing")
        with self.open() as db:
            db.execute("INSERT INTO tickets VALUES (?,?)", (request_id, session_id))


def otlp(traceparent, actions):
    _, trace_id, upstream, _ = traceparent.split("-")
    root = uuid4().hex[:16]
    now = time.time_ns()

    def span(identifier, parent, offset, start, end, attributes, **more):
        return {
            "traceId": trace_id,
            "spanId": identifier,
            "parentSpanId": parent,
            "startTimeUnixNano": str(now + start * (offset + 1)),
            "endTimeUnixNano": str(now + end * (offset + 1)),
            "attributes": [
                {"key": key, "value": {"stringValue": value}} for key, value in attributes.items()
            ],
            **more,
        }

    spans = [
        span(root, upstream, 0, 1, 1000, {"gen_ai.operation.name": "invoke_agent"}, kind=2),
        span(
            uuid4().hex[:16],
            root,
            0,
            2,
            3,
            {
                "gen_ai.operation.name": "retrieval",
                "gen_ai.retrieval.documents": json.dumps([{"id": POLICY}]),
            },
        ),
    ]
    for index, (name, args, failed) in enumerate(actions):
        spans.append(
            span(
                uuid4().hex[:16],
                root,
                index,
                10,
                11,
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": name,
                    "gen_ai.tool.call.arguments": json.dumps(args),
                },
                status={"code": 2 if failed else 1},
            )
        )
    return {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}


def handler(store, secret, export):
    class TestAgent(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send_json(self, status, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_dify_stream(self, conversation, answer, metadata):
            common = {"conversation_id": conversation}
            midpoint = len(answer) // 2
            events = [
                {"event": "agent_message", "answer": answer[:midpoint], **common},
                {"event": "agent_message", "answer": answer[midpoint:], **common},
                {"event": "message", "answer": answer, **common},
                {"event": "message_end", "metadata": metadata, **common},
            ]
            body = b"event: ping\r\n\r\n" + b"".join(
                b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\r\n\r\n"
                for event in events
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            if hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + secret):
                return True
            self.send_json(401, {"error": "authentication_required"})
            return False

        def do_GET(self):
            if not self.allowed():
                return
            url = urlsplit(self.path)
            if url.path == "/v1/parameters":
                self.send_json(200, {"user_input_form": []})
                return
            if url.path != "/facts":
                self.send_json(404, {"error": "unknown_endpoint"})
                return
            query = parse_qs(url.query)
            request_id = query.get("request_id", [""])[0]
            session_id = query.get("session_id", [""])[0]
            self.send_json(200, store.facts(request_id, session_id))

        def do_POST(self):
            if not self.allowed():
                return
            if self.path not in {"/chat", "/v1/chat-messages"}:
                self.send_json(404, {"error": "unknown_endpoint"})
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 65536:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                dify = self.path == "/v1/chat-messages"
                field = "conversation_id" if dify else "session_id"
                query = payload["query" if dify else "message"]
                request_id = self.headers["X-Request-ID"]
                session_id = self.headers["X-AgentChaos-Session"]
                if not isinstance(query, str) or not request_id or not session_id:
                    raise ValueError
                conversation = payload.get(field) or session_id
            except (KeyError, ValueError, TypeError):
                self.send_json(400, {"error": "invalid_input"})
                return
            answer, actions = store.respond(
                request_id,
                session_id,
                query,
                tool_fault=self.headers.get("X-AgentChaos-Test-Fault") == "tool_error",
            )
            traceparent = self.headers.get("traceparent")
            if traceparent and export is not None:
                export(otlp(traceparent, actions), dify)
            resources = [{"document_id": POLICY}]
            if dify and payload.get("response_mode") == "streaming":
                self.send_dify_stream(conversation, answer, {"retriever_resources": resources})
                return
            self.send_json(
                200,
                {
                    "answer": answer,
                    field: conversation,
                    "metadata": {"retriever_resources": resources, "documents": resources},
                },
            )

    return TestAgent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--private-config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.private_config.read_text(encoding="utf-8"))
    collector = config.get("collector")
    if collector and urlsplit(collector).hostname != "127.0.0.1":
        raise ValueError("The demo exporter only runs against a local preview collector")

    def export(payload, dify):
        token = config.get("dify_trace_token" if dify else "trace_token")
        if not collector or not token:
            return
        req = Request(
            collector,
            method="POST",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=2):
                pass
        except OSError:
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        handler(Helpdesk(args.database), config["agent_token"], export),
    )
    print(f"Local generic/Dify-shape test service on 127.0.0.1:{args.port}", flush=True)
    with server:
        server.serve_forever()


if __name__ == "__main__":
    main()

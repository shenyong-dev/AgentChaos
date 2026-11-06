"""Loopback-only blinded human labels, separate from production judge admission."""

import argparse
import html
import json
import secrets
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT

from .common import write_json

CHOICES = {
    "problem": "有明确问题",
    "no_visible_problem": "未见明确问题",
    "insufficient": "信息不足",
}


def validate_labels(packet, data):
    expected = {item["id"] for item in packet["items"]}
    if set(data) != expected | {"attestation"} or data["attestation"] != ["independent"]:
        raise ValueError("Complete all labels and independent annotation declaration")
    labels = {}
    for identifier in expected:
        value = data[identifier]
        if len(value) != 1 or value[0] not in CHOICES:
            raise ValueError("Each item requires exactly one valid label")
        labels[identifier] = value[0]
    return labels


def render(packet, action):
    sections = []
    for item in packet["items"]:
        radios = "".join(
            f'<label><input required type="radio" name="{item["id"]}" value="{key}">{text}</label>'
            for key, text in CHOICES.items()
        )
        inputs = "\n".join(f"{i}. {text}" for i, text in enumerate(item["user_inputs"], 1))
        sections.append(
            f"<section><h2>{item['id']}</h2><h3>实际输入</h3><pre>{html.escape(inputs)}</pre>"
            f"<h3>最终回答</h3><pre>{html.escape(item['answer'])}</pre><fieldset>"
            f"<legend>仅按回答可见内容判断</legend>{radios}</fieldset></section>"
        )
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>T17L 回答视图独立标注</title><style>"
        "body{font:16px/1.65 system-ui;background:#f2f5fa;color:#152238;margin:24px auto;"
        "padding:0 20px;max-width:1000px}section{background:white;border:1px solid #dce3ed;"
        "border-radius:14px;padding:24px;margin:20px 0}h2{margin:0}h3{font-size:14px;color:#526078}"
        "pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}"
        "fieldset{border:0;padding:8px 0;"
        "display:flex;gap:24px;flex-wrap:wrap}label{cursor:pointer}input{accent-color:#2563eb;"
        "margin-right:8px}button{font:inherit;background:#2563eb;color:white;padding:12px 24px;"
        "border:0;border-radius:8px;cursor:pointer}:focus-visible{outline:3px solid #6b91ed}"
        "footer{background:white;padding:20px;border-radius:12px}small{display:block;margin:12px 0}"
        "</style><h1>回答视图独立标注</h1><p>" + html.escape(packet["instructions"]) + "</p>"
        f'<form method="post" action="{html.escape(action, quote=True)}">'
        + "".join(sections)
        + '<footer><label><input required type="checkbox" name="attestation" value="independent">'
        "我仅依据本页输入和回答独立判断，未查看组别、SQL或规则结论。</label>"
        "<small>封存后不能覆盖原标签；后续复核会另存。</small>"
        f'<button type="submit">封存{len(packet["items"])}条独立判断</button>'
        "</footer></form></html>"
    ).encode()


def serve(directory, port=18025):
    directory = directory.resolve()
    if not directory.is_relative_to((PROJECT_ROOT / ".local/t17l").resolve()):
        raise ValueError("Only local T17L output is supported")
    packet = json.loads((directory / "answer-only.json").read_text(encoding="utf-8"))
    digest, token = canonical_hash(packet), secrets.token_urlsafe(24)
    endpoint = "/review/" + token
    expires = time.time() + 86400
    sealed = directory / "human-labels-sealed.json"
    first_served = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not echo the private URL into shared terminal logs.

        def respond(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def valid(self):
            return self.path == endpoint and time.time() < expires

        def do_GET(self):
            nonlocal first_served
            if not self.valid():
                return self.respond(404, b"Not found or expired")
            if sealed.exists():
                return self.respond(
                    200,
                    (
                        "<meta charset='utf-8'><h1>已封存</h1>"
                        "<p>原标签已保存，可以返回回复“已封存”。</p>"
                    ).encode(),
                )
            if first_served is None:
                first_served = datetime.now(UTC)
            self.respond(200, render(packet, endpoint))

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.valid() or origin not in (None, f"http://127.0.0.1:{port}"):
                return self.respond(403, b"Forbidden")
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("Invalid size")
                labels = validate_labels(
                    packet, parse_qs(self.rfile.read(size).decode(), strict_parsing=True)
                )
                value = {
                    "schema": "t17l-independent-labels-v1",
                    "packet_hash": digest,
                    "sealed_at": datetime.now(UTC).isoformat(),
                    "labels": labels,
                    "attestation": "human_independent_answer_only",
                    "assistant_prefilled": False,
                    "first_page_served_at": first_served.isoformat() if first_served else None,
                    "timing_boundary": "Wall time includes idle; not active review time.",
                }
                with sealed.open("x", encoding="utf-8") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2)
            except FileExistsError:
                return self.respond(409, "<meta charset='utf-8'>标签已封存，不能覆盖。".encode())
            except (ValueError, UnicodeError):
                return self.respond(
                    400, "<meta charset='utf-8'>请为每条选择一个判断并勾选独立声明。".encode()
                )
            self.respond(
                200, "<meta charset='utf-8'><h1>已封存</h1><p>请返回会话回复“已封存”。</p>".encode()
            )

    write_json(
        directory / "review-entry.json",
        {
            "url": f"http://127.0.0.1:{port}{endpoint}",
            "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
            "packet_hash": digest,
        },
    )
    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 2
    while time.time() < expires:
        server.handle_request()
    server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=18025)
    args = parser.parse_args()
    serve(args.directory, args.port)

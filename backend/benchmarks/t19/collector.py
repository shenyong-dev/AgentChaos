"""Loopback human trial recorder. Answers remain blank; each submission is append-only."""

import argparse
import hashlib
import html
import json
import os
import re
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT

PROTOCOL = PROJECT_ROOT / "docs/fixtures/T19/protocol.json"
ROOT = PROJECT_ROOT / ".local/t19/participants"
STATES = {"completed": "已完成", "blocked": "遇到阻塞", "unsure": "不确定"}
MAX_FORM_BYTES = 100_000  # Includes URL-encoded Unicode in the answer and note fields.


def clock():
    return datetime.now(UTC).isoformat()


def source_manifest():
    paths = []
    for folder in (
        "backend/app",
        "backend/reference_agents",
        "backend/alembic/versions",
        "frontend/src",
    ):
        paths.extend(
            p
            for p in (PROJECT_ROOT / folder).rglob("*")
            if p.is_file() and p.suffix in {".py", ".txt", ".json", ".ts", ".vue", ".css", ".svg"}
        )
    paths.extend(PROJECT_ROOT / p for p in ("backend/uv.lock", "frontend/package-lock.json"))
    paths.extend(Path(__file__).parent.glob("*.py"))
    paths.extend(Path(__file__).parent.glob("*.css"))
    paths.append(PROTOCOL)
    return {
        p.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding="utf-8").encode()
        ).hexdigest()
        for p in sorted(paths)
    }


def exclusive_json(path, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def prepare(participant, port):
    if not re.fullmatch(r"P0[1-3]", participant) or not 1024 <= port <= 65535:
        raise ValueError("Only P01–P03 and an unprivileged local port are supported")
    folder = (ROOT / participant).resolve()
    if not folder.is_relative_to(ROOT.resolve()):
        raise ValueError("Invalid local participant directory")
    folder.mkdir(parents=True, exist_ok=True)
    config_path = folder / "entry.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config["port"] != port:
            raise ValueError("Existing participant port is frozen; do not replace the entry")
        return folder, config
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    sources = source_manifest()
    config = {
        "schema": "t19-recorder-entry-v1",
        "participant": participant,
        "port": port,
        "token": secrets.token_urlsafe(24),
        "csrf": secrets.token_urlsafe(24),
        "protocol": protocol,
        "protocol_hash": canonical_hash(protocol),
        "sources": sources,
        "source_hash": canonical_hash(sources),
        "created_at": clock(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
        "phase": "A",
        "assistant_prefilled": False,
    }
    exclusive_json(config_path, config)
    return folder, config


def history(folder):
    path = folder / "events.jsonl"
    records, previous = [], None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            digest = row.pop("event_hash")
            if row.get("previous_hash") != previous or canonical_hash(row) != digest:
                raise ValueError("Trial record integrity mismatch; do not overwrite")
            row["event_hash"] = digest
            records.append(row)
            previous = digest
    return records


def append(folder, config, records, kind, data):
    if len(records) >= 100:
        raise ValueError("Record limit reached")
    row = {
        "sequence": len(records) + 1,
        "kind": kind,
        "at": clock(),
        "participant": config["participant"],
        "protocol_hash": config["protocol_hash"],
        "source_hash": config["source_hash"],
        "previous_hash": records[-1]["event_hash"] if records else None,
        "data": data,
    }
    row["event_hash"] = canonical_hash(row)
    with (folder / "events.jsonl").open("ab") as handle:
        handle.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    return row


def state(records):
    answers = [r for r in records if r["kind"] == "answer"]
    started = next(
        (
            r
            for r in reversed(records)
            if r["kind"] == "start" and r["data"]["step"] == len(answers)
        ),
        None,
    )
    return answers, started, any(r["kind"] == "seal" for r in records)


def one(data, key, *, maximum=6000):
    value = data.get(key, [])
    if len(value) != 1 or len(value[0]) > maximum:
        raise ValueError("Invalid or repeated form field")
    return value[0]


def mutate(folder, config, data):
    records = history(folder)
    answers, started, sealed = state(records)
    if one(data, "csrf") != config["csrf"]:
        raise ValueError("Invalid form token")
    action = one(data, "action")
    if sealed:
        raise ValueError("Records are sealed and cannot be replaced")
    tasks = config["protocol"]["tasks"]
    if action == "start":
        step = int(one(data, "step"))
        if step != len(answers) or step >= len(tasks):
            raise ValueError("Task order changed; refresh the current page")
        if not started:
            if not records:
                familiarity = one(data, "familiarity")
                if familiarity not in {"new", "seen", "used"} or one(data, "independent") != "yes":
                    raise ValueError("Independent participation declaration is required")
            else:
                familiarity = None
            append(
                folder,
                config,
                records,
                "start",
                {
                    "step": step,
                    "task_id": tasks[step]["id"],
                    "familiarity": familiarity,
                    "attestation": "human_not_implementation_author" if not records else None,
                },
            )
    elif action == "answer":
        step = int(one(data, "step"))
        status, answer = one(data, "status"), one(data, "answer").strip()
        help_count = int(one(data, "help_count"))
        aid, interruption = one(data, "used_ai"), one(data, "interrupted")
        note = one(data, "note", maximum=2000).strip()
        if status not in STATES or not answer or not 0 <= help_count <= 20:
            raise ValueError("Please fill the answer, status and help count")
        if aid not in {"no", "yes"} or interruption not in {"no", "yes"}:
            raise ValueError("Please record AI use and interruptions")
        if (help_count or aid == "yes" or interruption == "yes") and not note:
            raise ValueError("Please describe help, AI use or interruptions")
        payload = {
            "step": step,
            "status_self_reported": status,
            "answer": answer,
            "help_count": help_count,
            "used_ai": aid,
            "interrupted": interruption,
            "note": note,
        }
        digest = canonical_hash(payload)
        if step < len(answers):
            if answers[step]["data"]["submission_hash"] == digest:
                return
            raise ValueError("An earlier answer is already saved and cannot be replaced")
        if not started or step != len(answers) or step >= len(tasks):
            raise ValueError("Start the current task first")
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(started["at"])).total_seconds()
        if elapsed < 0:
            raise ValueError(
                "Server clock moved backwards; preserve records and contact coordinator"
            )
        append(
            folder,
            config,
            records,
            "answer",
            {
                **payload,
                "task_id": tasks[step]["id"],
                "submission_hash": digest,
                "started_at": started["at"],
                "wall_seconds": round(elapsed, 3),
                "timing_boundary": "Includes thinking, navigation and idle; not active input time",
                "correctness": "pending_independent_review",
            },
        )
    elif action == "seal":
        if len(answers) != len(tasks) or one(data, "attestation") != "independent":
            raise ValueError("Complete all tasks and confirm independent participation")
        feedback = one(data, "feedback", maximum=4000).strip()
        if not feedback:
            raise ValueError("Please record your feedback, including none if applicable")
        append(
            folder,
            config,
            records,
            "seal",
            {
                "feedback": feedback,
                "answers": len(answers),
                "attestation": "human_personally_operated_no_proxy_answers",
                "completion_boundary": (
                    "Phase A only; not clean install, full new run, E4 or calibration"
                ),
            },
        )
    else:
        raise ValueError("Unknown action")


def render(config, records, endpoint):
    def escape(value):
        return html.escape(str(value), quote=True)

    def hidden(name, value):
        return f'<input type="hidden" name="{name}" value="{escape(value)}">'

    def select(name, label, choices):
        options = '<option value="">请选择</option>' + "".join(
            f'<option value="{escape(k)}">{escape(v)}</option>' for k, v in choices.items()
        )
        return f'<label>{label}<select name="{name}" required>{options}</select></label>'

    answers, started, sealed = state(records)
    tasks = config["protocol"]["tasks"]
    step = len(answers)
    form = f'<form action="{escape(endpoint)}" method="post">' + hidden("csrf", config["csrf"])
    if sealed:
        content = (
            "<h1>试用记录已封存</h1>"
            f"<p>请交还电脑，由协调者回复“{escape(config['participant'])}已封存”。</p>"
            "<p>这是基础界面试用，答案正确性仍待复核。</p>"
        )
    elif step == len(tasks):
        content = (
            "<h1>试用反馈</h1>" + form + hidden("action", "seal") + "<label>最困惑的地方或改进建议"
            '<textarea name="feedback" required maxlength="4000"></textarea></label>'
            '<label class="check"><input type="checkbox" name="attestation" '
            'value="independent" required>我亲自操作和作答，没有让开发者代填；'
            "求助和AI使用已如实记录。</label>"
            "<p>封存后不能覆盖原答案；本阶段不代表独立安装复现或助手效果验证完成。</p>"
            "<button>封存本次试用记录</button></form>"
        )
    elif not started:
        label = "开始试用" if not records else "开始下一项"
        content = (
            f"<h1>{label}</h1><p>{step + 1} / {len(tasks)} · "
            f"{escape(tasks[step]['title'])}</p>"
            "<p>点击后显示任务并开始服务器计时；思考、切换页面与空闲均计入。"
            "可在开始下一项前休息。</p>" + form + hidden("action", "start") + hidden("step", step)
        )
        if not records:
            content += "<p>" + escape(config["protocol"]["instructions"]) + "</p>"
            content += select(
                "familiarity",
                "对平台的熟悉程度",
                {"new": "从未用过", "seen": "看过展示，没操作过", "used": "以前操作过"},
            )
            content += (
                '<label class="check"><input type="checkbox" name="independent" '
                'value="yes" required>我未参与实现，将独立操作，遇到困难会如实记录。</label>'
            )
        content += f"<button>{label}</button></form>"
    else:
        task = tasks[step]
        url = escape(config["protocol"]["platform_url"] + task["path"])
        content = (
            f"<small>{escape(config['participant'])} · {step + 1} / {len(tasks)}</small>"
            f"<h1>{escape(task['title'])}</h1><p>{escape(task['instruction'])}</p>"
            f'<a class="platform" href="{url}" target="_blank" rel="noopener noreferrer">'
            "打开平台任务页面 ↗</a>"
            + form
            + hidden("action", "answer")
            + hidden("step", step)
            + select("status", "完成情况", STATES)
            + "<label>你的操作、判断与证据位置"
            '<textarea name="answer" required maxlength="6000"></textarea></label>'
            '<div class="fields"><label>向人求助次数'
            '<input name="help_count" type="number" min="0" max="20" required></label>'
        )
        for key, title in (("used_ai", "是否使用了AI"), ("interrupted", "是否中途离开或暂停")):
            content += select(key, title, {"no": "没有", "yes": "有"})
        content += (
            "</div><label>求助、中断或偏离任务的说明"
            '<textarea name="note" maxlength="2000"></textarea></label>'
            "<p>保存后保留原答案，不能返回覆盖；不确定和阻塞也是有效记录。"
            "不要填写密码或密钥。</p><button>保存本项记录</button></form>"
        )
    style = Path(__file__).with_name("style.css").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>T19 独立试用记录</title><style>{style}</style><main>{content}</main></html>"
    ).encode()


@contextmanager
def process_lock(folder):
    with (folder / "collector.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def handler(folder, config):
    endpoint = "/trial/" + config["token"]
    origin = f"http://127.0.0.1:{config['port']}"

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def send(self, status, body):
            self.send_response(status)
            for key, value in {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
                "Referrer-Policy": "same-origin",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'"
                ),
            }.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def valid(self):
            return self.path == endpoint and self.headers.get("Host") == origin.removeprefix(
                "http://"
            )

        def check(self):
            if not self.valid():
                self.send(404, b"Not found")
                return False
            if datetime.now(UTC) >= datetime.fromisoformat(config["expires_at"]):
                self.send(
                    410, "<meta charset='utf-8'>入口已过期，原记录保留，请联系协调者。".encode()
                )
                return False
            if canonical_hash(source_manifest()) != config["source_hash"]:
                self.send(
                    409,
                    (
                        "<meta charset='utf-8'>平台源码已变化，原记录保留，请停止试用并联系协调者。"
                    ).encode(),
                )
                return False
            return True

        def do_GET(self):
            if self.check():
                try:
                    records = history(folder)
                except (ValueError, KeyError):
                    return self.send(
                        409, "<meta charset='utf-8'>记录校验失败，请联系协调者。".encode()
                    )
                self.send(200, render(config, records, endpoint))

        def do_POST(self):
            if not self.valid() or self.headers.get("Origin") != origin:
                return self.send(403, b"Forbidden")
            if not self.check():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if (
                    not 0 < size <= MAX_FORM_BYTES
                    or self.headers.get_content_type() != "application/x-www-form-urlencoded"
                ):
                    raise ValueError("Invalid form size or content type")
                data = parse_qs(
                    self.rfile.read(size).decode("utf-8"),
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=15,
                )
                mutate(folder, config, data)
            except (ValueError, UnicodeError, KeyError, IndexError):
                return self.send(
                    409,
                    (
                        "<meta charset='utf-8'>保存未受理：请检查必填项、求助说明或当前任务。"
                        "原记录不会覆盖；返回原表单核对，或刷新查看已保存结果。"
                    ).encode(),
                )
            self.send_response(303)
            self.send_header("Location", endpoint)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participant", choices=("P01", "P02", "P03"), default="P01")
    parser.add_argument("--port", type=int, default=18035)
    args = parser.parse_args()
    folder, config = prepare(args.participant, args.port)
    with process_lock(folder):
        server = HTTPServer(("127.0.0.1", args.port), handler(folder, config))
        server.timeout = 1
        server_url = f"http://127.0.0.1:{args.port}/trial/{config['token']}"
        print(
            json.dumps(
                {
                    "url": server_url,
                    "expires_at": config["expires_at"],
                    "participant": args.participant,
                    "protocol_hash": config["protocol_hash"],
                    "phase": "A",
                    "model_calls_by_recorder": 0,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            while datetime.now(UTC) < datetime.fromisoformat(config["expires_at"]):
                server.handle_request()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()

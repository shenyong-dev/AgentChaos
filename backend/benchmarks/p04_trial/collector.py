"""Loopback single-task human pilot; GET never starts a task or calls a model."""

import argparse
import html
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from redis import Redis

from app.agents.versions import canonical_hash
from app.core.config import Settings
from app.db.engine import create_database_engine
from benchmarks.t19.collector import clock, exclusive_json, history, process_lock

from .assistant import Assistant
from .budget import initialize, initialized
from .case_reader import citation_links, render_case
from .package import ROOT, approval, check

STATES = {"completed": "完成", "blocked": "卡住", "uncertain": "不确定"}


class FormError(ValueError):
    def __init__(self, fields):
        self.fields = fields
        super().__init__("请检查标出的项目；已填写内容保留。")


def one(data, key, maximum=4000):
    values = data.get(key, [])
    if len(values) != 1 or len(values[0]) > maximum:
        raise FormError({key: "请填写此项，且不要重复提交同名字段。"})
    return values[0]


def emit(folder, config, records, kind, data):
    row = {
        "sequence": len(records) + 1,
        "kind": kind,
        "at": clock(),
        "participant": config["participant"],
        "protocol_hash": config["package_hash"],
        "source_hash": config["source_hash"],
        "previous_hash": records[-1]["event_hash"] if records else None,
        "data": data,
    }
    row["event_hash"] = canonical_hash(row)
    with (folder / "events.jsonl").open("ab") as stream:
        stream.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    records.append(row)
    return row


class Runtime:
    def __init__(self, package, engine, redis, settings):
        self.package, self.engine, self.redis, self.settings = (
            Path(package),
            engine,
            redis,
            settings,
        )

    def check(self):
        approval(self.package)
        if (ROOT / "initialization.json").exists():
            initialized(self.redis, self.package)
        return check(self.package, self.engine, self.settings)

    def assistant(self, participant):
        return Assistant(participant, self.package, self.engine, self.redis, self.settings)


def prepare(runtime, participant, port):
    if participant not in {"P01", "P02", "P03"} or not 1024 <= port <= 65535:
        raise ValueError("Invalid participant slot or port")
    frozen = runtime.check()
    folder = ROOT / "participants" / participant
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "receipts").mkdir(exist_ok=True)
    path = folder / "entry.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["package_hash"] != frozen["package_hash"] or value["port"] != port:
            raise ValueError("Original entry differs; never replace it")
        return folder, value
    value = {
        "participant": participant,
        "port": port,
        "token": secrets.token_urlsafe(24),
        "csrf": secrets.token_urlsafe(24),
        "package_hash": frozen["package_hash"],
        "source_hash": frozen["source_hash"],
        "created_at": clock(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
        "task": frozen["cases"][int(participant[-1]) - 1],
    }
    exclusive_json(path, value)
    return folder, value


def records(folder, config):
    rows = history(folder)
    if any(
        row["participant"] != config["participant"]
        or row["protocol_hash"] != config["package_hash"]
        or row["source_hash"] != config["source_hash"]
        for row in rows
    ):
        raise ValueError("Pilot record binding changed")
    for row in rows:
        if row["kind"] == "receipt":
            read_receipt(folder, rows, row["data"]["request_id"])
    return rows


def state(rows):
    return any(row["kind"] == "start" for row in rows), any(
        row["kind"] in {"answer_seal", "withdraw"} for row in rows
    )


def receipt_path(folder, request_id):
    return folder / "receipts" / (str(UUID(request_id)) + ".json")


def read_receipt(folder, rows, request_id):
    event = next(
        (
            row
            for row in rows
            if row["kind"] == "receipt" and row["data"]["request_id"] == request_id
        ),
        None,
    )
    if not event:
        return None
    value = json.loads(receipt_path(folder, request_id).read_text(encoding="utf-8"))
    if (
        canonical_hash(value) != event["data"]["receipt_hash"]
        or value.get("request_id") != request_id
    ):
        raise ValueError("Original model receipt changed")
    return value


def mutate(folder, config, data, runtime):
    rows = records(folder, config)
    if one(data, "csrf") != config["csrf"]:
        raise ValueError("Invalid form token")
    action = one(data, "action")
    if action.startswith("receipt:"):
        data = {**data, "request_id": [str(UUID(action.split(":", 1)[1]))]}
        action = "receipt"
    if action == "finish" and data.get("withdraw") == ["yes"]:
        action = "withdraw"
    started, ended = state(rows)
    if action == "start":
        if started:
            return
        familiarity, involvement = one(data, "familiarity"), one(data, "involvement")
        if (
            familiarity not in {"new", "seen", "used"}
            or involvement not in {"author", "other", "none"}
            or one(data, "consent") != "yes"
        ):
            raise FormError({"consent": "请如实选择熟悉程度、参与情况并确认亲自操作。"})
        emit(
            folder,
            config,
            rows,
            "start",
            {
                "familiarity": familiarity,
                "involvement": involvement,
                "task_id": config["task"]["id"],
                "attestation": "human_personally_operating",
                "reference_review": "pending_no_correctness_scoring",
            },
        )
        return
    if not started:
        raise ValueError("Start this task first")
    if (
        not ended
        and action in {"ask", "receipt"}
        and all(key in data for key in ("judgment", "evidence", "next_action", "status"))
    ):
        saved = {key: one(data, key) for key in ("judgment", "evidence", "next_action", "status")}
        previous = next((row["data"] for row in reversed(rows) if row["kind"] == "draft"), None)
        if saved != previous:
            emit(folder, config, rows, "draft", saved)
    if action == "receipt":
        rid = str(UUID(one(data, "request_id")))
        old = next(
            (row for row in rows if row["kind"] == "question" and row["data"]["request_id"] == rid),
            None,
        )
        if not old:
            raise ValueError("Original request unavailable")
        path = receipt_path(folder, rid)
        if read_receipt(folder, rows, rid) is None:
            result = runtime.assistant(config["participant"]).receipt(rid)
            if result.get("status") == "processing":
                raise ValueError("Original request is still processing; do not resend")
            if result.get("request_id") != rid:
                raise ValueError("Receipt identity mismatch")
            if path.exists():
                if json.loads(path.read_text(encoding="utf-8")) != result:
                    raise ValueError("Unbound receipt differs; preserve both observations")
            else:
                exclusive_json(path, result)
            emit(
                folder,
                config,
                rows,
                "receipt",
                {"request_id": rid, "receipt_hash": canonical_hash(result)},
            )
        return
    if ended:
        raise ValueError("The original record is sealed or withdrawn")
    if action == "withdraw":
        emit(
            folder,
            config,
            rows,
            "withdraw",
            {
                "reason": one(data, "reason", 1000),
                "draft": {
                    key: data.get(key, [""])[0][:4000]
                    for key in ("judgment", "evidence", "next_action")
                },
                "human_seal": False,
            },
        )
        return
    if action == "finish":
        values = {key: one(data, key).strip() for key in ("judgment", "evidence", "next_action")}
        status = one(data, "status")
        errors = {
            key: "请填写；不确定或未找到证据也可如实说明。"
            for key, value in values.items()
            if not value
        }
        if status not in STATES:
            errors["status"] = "请选择完成情况。"
        if data.get("seal") != ["yes"]:
            errors["seal"] = "请确认由本人填写并封存。"
        if errors:
            raise FormError(errors)
        emit(
            folder,
            config,
            rows,
            "answer_seal",
            {
                **values,
                "status_self_reported": status,
                "attestation": "human_personally_operated_and_answered",
                "scoring": "not_performed_reference_pending",
                "observer_help": "not_observed",
                "observer_interruptions": "not_observed",
            },
        )
        return
    if action != "ask":
        raise ValueError("Unknown action")
    rid = str(UUID(one(data, "request_id")))
    assistant = runtime.assistant(config["participant"])
    question = assistant.question(one(data, "question", 1500))
    if not question:
        raise FormError({"question": "请输入要问助手的问题。"})
    old = next(
        (row for row in rows if row["kind"] == "question" and row["data"]["request_id"] == rid),
        None,
    )
    if old:
        if old["data"]["question_hash"] != canonical_hash(question):
            raise ValueError("Original question cannot change")
        # An existing question can only be inspected, never sent again.
        return mutate(
            folder,
            config,
            {"csrf": [config["csrf"]], "action": ["receipt"], "request_id": [rid]},
            runtime,
        )
    unresolved = [
        row
        for row in rows
        if row["kind"] == "question"
        and read_receipt(folder, rows, row["data"]["request_id"]) is None
    ]
    if unresolved:
        raise ValueError("Resolve the original receipt first")
    emit(
        folder,
        config,
        rows,
        "question",
        {"request_id": rid, "question": question, "question_hash": canonical_hash(question)},
    )
    result = assistant.ask(question, rid)
    if result.get("status") == "processing":
        raise ValueError("Original request remains in progress")
    path = receipt_path(folder, rid)
    if result.get("request_id") != rid:
        raise ValueError("Receipt identity mismatch")
    path.parent.mkdir(exist_ok=True)
    exclusive_json(path, result)
    emit(
        folder, config, rows, "receipt", {"request_id": rid, "receipt_hash": canonical_hash(result)}
    )


def esc(value):
    return html.escape(str(value), quote=True)


def render(config, rows, endpoint, draft=None, errors=None, message=""):
    saved = next((row["data"] for row in reversed(rows) if row["kind"] == "draft"), {})
    draft, errors = (
        {**{key: [value] for key, value in saved.items()}, **(draft or {})},
        errors or {},
    )
    started, ended = state(rows)
    task = config["task"]

    def field(key):
        return esc(draft.get(key, [""])[0])

    def base(action):
        return f'<form method="post" action="{endpoint}"><input type="hidden" name="csrf" value="{config["csrf"]}"><input type="hidden" name="action" value="{action}">'  # noqa: E501 -- standalone HTML literal

    def error(key):
        return (
            f'<span class="field-error" id="error-{key}">{esc(errors[key])}</span>'
            if key in errors
            else ""
        )

    def textarea(key, label, height=3):
        attr = f' aria-invalid="true" aria-describedby="error-{key}"' if key in errors else ""
        maximum = 1500 if key == "question" else 4000
        return f'<label for="{key}">{label}</label><textarea id="{key}" name="{key}" rows="{height}" maxlength="{maximum}"{attr}>{field(key)}</textarea>{error(key)}'  # noqa: E501 -- standalone HTML literal

    def select(key, label, options):
        return (
            f'<label for="{key}">{label}</label><select id="{key}" name="{key}" required><option value="">请选择</option>'  # noqa: E501 -- standalone HTML literal
            + "".join(
                f'<option value="{value}"'
                + (" selected" if draft.get(key) == [value] else "")
                + ">"
                + text
                + "</option>"
                for value, text in options
            )
            + "</select>"
        )

    content = (
        '<header><a class="brand" href="'
        + endpoint
        + '">AC · AgentChaos</a><span>单题试用 · 不评分</span></header>'
    )
    if message:
        content += f'<p class="error" role="alert">{esc(message)}</p>'
    if not started:
        content += "<h1>用助手理解一条用例</h1><p>阅读原文、向助手提问，再写下你的判断。只做一题，可随时结束。</p>"  # noqa: E501 -- standalone HTML literal
        content += (
            base("start")
            + select(
                "familiarity",
                "使用经验",
                [("new", "第一次接触"), ("seen", "看过介绍或演示"), ("used", "以前用过")],
            )
            + select(
                "involvement",
                "项目参与情况",
                [
                    ("none", "未参与开发或测试"),
                    ("other", "参与过测试或评审"),
                    ("author", "参与过开发"),
                ],
            )
            + '<label class="check"><input type="checkbox" name="consent" value="yes" required>我会亲自操作、如实填写，不由他人代答。</label>'  # noqa: E501 -- standalone HTML literal
            + error("consent")
            + '<button class="primary">开始本题</button></form>'
        )
    elif ended:
        last = next(row for row in reversed(rows) if row["kind"] in {"answer_seal", "withdraw"})
        content += (
            "<h1>"
            + ("本题记录已封存" if last["kind"] == "answer_seal" else "本次试用已结束")
            + "</h1><p>原记录已保留。谢谢参与。</p>"
        )
        if last["kind"] == "answer_seal":
            content += "".join(
                f'<section><h2>{title}</h2><p class="copy">{esc(last["data"][key])}</p></section>'
                for key, title in (
                    ("judgment", "你的判断"),
                    ("evidence", "证据"),
                    ("next_action", "下一步"),
                )
            )
    else:
        rid = draft.get("request_id", [str(uuid4())])[0]
        content += f'<h1>{esc(task["title"])}</h1><p class="task">{esc(task["instruction"])}</p>'
        content += f'<form class="pilot-form" method="post" action="{endpoint}"><input type="hidden" name="csrf" value="{config["csrf"]}"><input type="hidden" name="request_id" value="{esc(rid)}"><div class="columns"><div>'  # noqa: E501 -- standalone HTML literal
        content += render_case(task)
        content += "<section><h2>问助手</h2>"
        for row in rows:
            if row["kind"] != "question":
                continue
            receipt_id = row["data"]["request_id"]
            content += (
                '<div class="conversation"><strong>你</strong><p class="copy">'
                + esc(row["data"]["question"])
                + "</p>"
            )
            result = read_receipt(Path(config["folder"]), rows, receipt_id)
            if result is not None:
                content += "<strong>助手</strong>"
                if result.get("answer"):
                    answer = result["answer"]
                    for group in ("facts", "possible_causes", "missing_evidence", "next_steps"):
                        if answer.get(group):
                            title = {
                                "facts": "事实",
                                "possible_causes": "待核实原因",
                                "missing_evidence": "缺少证据",
                                "next_steps": "下一步",
                            }[group]
                            content += '<h3 class="answer-group">' + title + "</h3>"
                        for line in answer.get(group, []):
                            content += (
                                '<p class="copy">'
                                + esc(line.get("text", "") if isinstance(line, dict) else line)
                                + "</p>"
                            )
                    links = citation_links(task, result)
                    if links:
                        content += links
                    elif "case.read" in answer.get("action_ids", []):
                        content += '<a class="action" href="#guide-inputs">查看所选用例说明 ↑</a>'
                else:
                    content += (
                        '<p role="status">'
                        + esc(result.get("error") or "本次没有可展示的回答。")
                        + "</p>"
                    )
                content += (
                    '<details><summary>来源、状态与调用记录</summary><pre tabindex="0">'
                    + esc(json.dumps(result, ensure_ascii=False, indent=2))
                    + "</pre></details>"
                )
            else:
                content += (
                    '<p role="status">原问题尚无最终回执，请勿重发。</p>'
                    + f'<button name="action" value="receipt:{receipt_id}">读取原回执</button>'
                )
            content += "</div>"
        pending = any(
            row["kind"] == "question"
            and read_receipt(Path(config["folder"]), rows, row["data"]["request_id"]) is None
            for row in rows
        )
        if not pending:
            content += (
                textarea("question", "你的问题")
                + '<p class="cost">发送将调用真实AI并计费，本页草稿一并保存。累计金额与次数不限，单次请求最多45秒。</p><button class="primary" name="action" value="ask">发送给助手</button>'  # noqa: E501 -- standalone HTML literal
            )
        content += "</section></div><div><section><h2>记下你的理解</h2>"
        for key, label in (
            ("judgment", "我的判断"),
            ("evidence", "证据位置与一小段原文"),
            ("next_action", "下一步点哪里"),
        ):
            content += textarea(key, label)
        content += (
            '<label for="status">完成情况</label><select id="status" name="status"><option value="">请选择</option>'  # noqa: E501 -- standalone HTML literal
            + "".join(
                f'<option value="{key}"'
                + (" selected" if draft.get("status") == [key] else "")
                + ">"
                + label
                + "</option>"
                for key, label in STATES.items()
            )
            + "</select>"
            + error("status")
        )
        content += (
            '<label class="check"><input type="checkbox" name="seal" value="yes">以上由我亲自填写，确认封存本题。</label>'  # noqa: E501 -- standalone HTML literal
            + error("seal")
            + '<p class="cost">提交后记录封存，不能覆盖；助手回复与您的答案分别保留。</p><button class="primary" name="action" value="finish">提交并封存本题</button>'  # noqa: E501 -- standalone HTML literal
        )
        content += '<details class="withdraw"><summary>提前结束</summary><label for="reason">原因（可留空）</label><textarea id="reason" name="reason" rows="2" maxlength="1000"></textarea><button name="action" value="withdraw" formnovalidate>结束并保留记录</button></details></section></div></div></form>'  # noqa: E501 -- standalone HTML literal
    css = Path(__file__).with_name("style.css").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentChaos · 单题试用</title><style>'  # noqa: E501 -- standalone HTML literal
        + css
        + "</style><main>"
        + content
        + "</main></html>"
    ).encode()


def handler(folder, config, runtime):
    endpoint = "/trial/" + config["token"]
    origin = f"http://127.0.0.1:{config['port']}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body):
            self.send_response(status)
            for key, value in {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Length": str(len(body)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",  # noqa: E501 -- standalone HTML literal
                "Connection": "close",
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
            try:
                runtime.check()
                if datetime.now(UTC) >= datetime.fromisoformat(config["expires_at"]):
                    raise ValueError("Expired")
            except Exception:
                self.send(
                    409,
                    '<meta charset="utf-8">本轮来源、授权或期限不可确认，记录保留，请联系协调者。'.encode(),  # noqa: E501 -- standalone HTML literal
                )
                return False
            return True

        def do_GET(self):
            if self.check():
                try:
                    self.send(200, render(config, records(folder, config), endpoint))
                except Exception:
                    self.send(409, b"Record integrity cannot be confirmed")

        def do_POST(self):
            if not self.valid() or self.headers.get("Origin") != origin:
                return self.send(403, b"Forbidden")
            if not self.check():
                return
            data = {}
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if (
                    not 0 < size <= 150000
                    or self.headers.get_content_type() != "application/x-www-form-urlencoded"
                ):
                    raise ValueError("Invalid form")
                self.connection.settimeout(10)
                data = parse_qs(
                    self.rfile.read(size).decode(),
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=20,
                )
                mutate(folder, config, data, runtime)
            except Exception as exc:
                try:
                    self.send(
                        409,
                        render(
                            config,
                            records(folder, config),
                            endpoint,
                            draft=data,
                            errors=exc.fields if isinstance(exc, FormError) else {},
                            message=str(exc)
                            if isinstance(exc, FormError)
                            else "当前操作未完成，原记录已保留。若已发送问题，请只读取原回执。",
                        ),
                    )
                except Exception:
                    self.send(409, b"Preserve records and contact coordinator")
                return
            self.send_response(303)
            self.send_header("Location", endpoint)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--participant", choices=("P01", "P02", "P03"), default="P01")
    parser.add_argument("--port", type=int, default=18054)
    args = parser.parse_args()
    settings = Settings()
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    runtime = Runtime(args.package, engine, redis, settings)
    runtime.check()
    initialize(redis, args.package)
    folder, config = prepare(runtime, args.participant, args.port)
    config = {**config, "folder": str(folder)}
    with process_lock(folder):
        server = HTTPServer(("127.0.0.1", args.port), handler(folder, config, runtime))
        print(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{args.port}/trial/{config['token']}",
                    "mode": "human_exploratory_pilot",
                    "participant_started": False,
                }
            ),
            flush=True,
        )
        server.serve_forever()


if __name__ == "__main__":
    main()

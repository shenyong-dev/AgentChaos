"""Loopback E4 record forms. Only explicit AI-condition sends can call the approved port."""

import argparse
import hashlib
import html
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from redis import Redis

from app.agents.versions import canonical_hash
from app.copilot.sources import clean
from app.core.config import Settings
from app.db.engine import create_database_engine
from benchmarks.t19 import collector as original
from benchmarks.t19b.budget import approval, initialize
from benchmarks.t19b.copilot import TrialAssistant
from benchmarks.t19b.preparation import ROOT, EvidenceAnswer, check_current, exclusive_json


def ui_manifest():
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.iterdir())
        if p.suffix in {".py", ".css"}
    }


class Runtime:
    def __init__(self, package, engine, redis, settings):
        self.package, self.engine, self.redis, self.settings = package, engine, redis, settings

    def check(self):
        approval(self.package)
        return check_current(self.package, self.engine)

    def question(self, value):
        return clean(value.strip(), self.settings)

    def port(self, config, task):
        return TrialAssistant(
            config["participant"], task["id"], self.package, self.engine, self.redis, self.settings
        )


def prepare(runtime, participant, port):
    frozen = runtime.check()
    if participant not in frozen["protocol"]["allocation"] or not 1024 <= port <= 65535:
        raise ValueError("Unknown participant or invalid local port")
    folder = ROOT / "participants" / participant
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "entry.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            value["port"] != port
            or value["package_hash"] != frozen["package_hash"]
            or value["recording_hash"] != canonical_hash(ui_manifest())
        ):
            raise ValueError("Existing round differs; preserve it, do not replace")
        return folder, value
    tasks = {t["id"]: t for t in frozen["protocol"]["tasks"]}
    value = {
        "participant": participant,
        "port": port,
        "token": secrets.token_urlsafe(24),
        "csrf": secrets.token_urlsafe(24),
        "protocol_hash": frozen["protocol_hash"],
        "source_hash": frozen["source_hash"],
        "package_hash": frozen["package_hash"],
        "recording_hash": canonical_hash(ui_manifest()),
        "tasks": [
            {**tasks[identifier], "condition": condition}
            for identifier, condition in frozen["protocol"]["allocation"][participant]
        ],
        "created_at": original.clock(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
        "phase": "B-E4",
    }
    exclusive_json(path, value)
    return folder, value


def emit(folder, config, records, kind, value):
    return original.append(
        folder, config, records, kind, {**value, "recording_hash": config["recording_hash"]}
    )


def active(records):
    return original.state(records)


class FormError(ValueError):
    def __init__(self, fields):
        self.fields = fields
        super().__init__("请检查标出的项目；已填写内容保留。")


def answer_fields(data):
    labels = {
        "conclusion": "请填写你的判断；不确定也可以。",
        "next_action": "请填写下一步安全操作。",
        "evidence_location": "请填写证据所在页面或栏目。",
        "evidence_excerpt": "请摘录支持判断的原文。",
        "evidence_reason": "请说明这段原文为什么支持判断。",
        "missing_reason": "请说明没有找到哪项证据，或哪里不确定。",
        "deviation_note": "请具体说明实际求助、AI使用或中断情况，不能只填“无”。",
    }
    errors, fields = {}, {}
    for key in EvidenceAnswer.model_fields:
        values = data.get(key, [])
        if len(values) != 1 or len(values[0]) > 6000:
            errors[key] = "请填写此项，且不要重复提交同名字段。"
        else:
            fields[key] = values[0]
    for flag in ("evidence_found", "used_ai", "interrupted"):
        if fields.get(flag) not in {"yes", "no"}:
            errors[flag] = "请选择一项。"
        else:
            fields[flag] = fields[flag] == "yes"
    try:
        fields["help_count"] = int(fields.get("help_count", ""))
        if not 0 <= fields["help_count"] <= 20:
            raise ValueError
    except ValueError:
        errors["help_count"] = "请填写0到20之间的求助次数。"
    for key in ("conclusion", "next_action"):
        if not str(fields.get(key, "")).strip():
            errors[key] = labels[key]
    required = (
        ("evidence_location", "evidence_excerpt", "evidence_reason")
        if fields.get("evidence_found") is True
        else ("missing_reason",)
    )
    for key in required:
        if not str(fields.get(key, "")).strip():
            errors[key] = labels[key]
    if (
        fields.get("help_count")
        or fields.get("used_ai") is True
        or fields.get("interrupted") is True
    ):
        note = re.sub(r"[\s，。,.!！]", "", str(fields.get("deviation_note", ""))).lower()
        if note in {"", "无", "没有", "none", "无求助", "不适用", "na", "n/a"}:
            errors["deviation_note"] = labels["deviation_note"]
    if data.get("status") not in [[key] for key in original.STATES]:
        errors["status"] = "请选择本项完成情况。"
    if errors:
        raise FormError(errors)
    try:
        return EvidenceAnswer.model_validate(fields).model_dump()
    except ValidationError as exc:
        raise FormError(
            {
                str(e["loc"][0]): "内容过长或格式不正确，请修改此项。"
                for e in exc.errors()
                if e["loc"]
            }
        ) from None


def history(folder, config):
    rows = original.history(folder)
    for row in rows:
        if (
            row["participant"] != config["participant"]
            or row["protocol_hash"] != config["protocol_hash"]
            or row["source_hash"] != config["source_hash"]
            or row["data"].get("recording_hash") != config["recording_hash"]
        ):
            raise ValueError("Recording does not belong to this frozen entry")
    return rows


def requests(records, step):
    return [r for r in records if r["kind"] == "ai_request" and r["data"]["step"] == step]


def reply(records, request_id):
    return next(
        (
            r
            for r in reversed(records)
            if r["kind"] == "ai_reply" and r["data"]["request_id"] == request_id
        ),
        None,
    )


def pending(records, step):
    for row in requests(records, step):
        result = reply(records, row["data"]["request_id"])
        if not result or result["data"]["receipt"].get("status") == "processing":
            return row
    return None


def request_id(config, records, step):
    return str(uuid5(NAMESPACE_URL, f"{config['token']}:{step}:{len(requests(records, step))}"))


def mutate(folder, config, data, runtime):
    def one(key, maximum=6000):
        return original.one(data, key, maximum=maximum)

    if one("csrf") != config["csrf"]:
        raise ValueError("Invalid form token")
    records = history(folder, config)
    answers, started, sealed = active(records)
    if sealed or any(r["kind"] == "withdraw" for r in records):
        raise ValueError("Ended records cannot change")
    action = one("action")
    if action == "withdraw":
        if one("stop_confirmation") != "yes":
            raise FormError(
                {"stop_confirmation": "请确认提前结束；已有答案保留，未答题不会自动补填。"}
            )
        emit(
            folder,
            config,
            records,
            "withdraw",
            {
                "answers": len(answers),
                "reason": one("stop_reason", 2000).strip(),
                "status": "participant_ended_incomplete",
                "human_seal": False,
                "pending_request": bool(pending(records, len(answers))),
            },
        )
        return
    if action == "seal":
        if len(answers) != 4 or one("attestation") != "independent":
            raise ValueError("Complete the round and attest personally")
        feedback = one("feedback", 4000).strip()
        if not feedback:
            raise ValueError("Record feedback, including none")
        emit(
            folder,
            config,
            records,
            "seal",
            {
                "feedback": feedback,
                "answers": 4,
                "attestation": "human_personally_operated_no_proxy_answers",
            },
        )
        return
    step = int(one("step"))
    if not 0 <= step < 4:
        raise ValueError("Invalid step")
    if action == "answer":
        answer = answer_fields(data)
        status = one("status")
        if status not in original.STATES:
            raise ValueError("Record status")
        payload = {"step": step, "status_self_reported": status, **answer}
        digest = canonical_hash(payload)
        if step < len(answers):
            if answers[step]["data"]["submission_hash"] == digest:
                return
            raise ValueError("The original answer cannot be overwritten")
        if step != len(answers) or not started:
            raise ValueError("Start the current task first")
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(started["at"])).total_seconds()
        if elapsed < 0:
            raise ValueError("Server clock moved backwards")
        emit(
            folder,
            config,
            records,
            "answer",
            {
                **payload,
                "submission_hash": digest,
                "task_id": config["tasks"][step]["id"],
                "condition": config["tasks"][step]["condition"],
                "wall_seconds": round(elapsed, 3),
                "correctness": "pending_human_evidence_review",
                "observed_ai_requests": len(requests(records, step)),
            },
        )
        return
    if step != len(answers):
        raise ValueError("Task order changed")
    if action == "start":
        if not started:
            extra = {}
            if step == 0:
                if (
                    one("independent") != "yes"
                    or one("familiarity") not in {"new", "seen", "used"}
                    or one("seen_cases") not in {"yes", "no", "unsure"}
                ):
                    raise ValueError("Record familiarity and personal participation")
                extra = {
                    "familiarity": one("familiarity"),
                    "seen_cases": one("seen_cases"),
                    "attestation": "human_not_implementation_author",
                }
            emit(
                folder,
                config,
                records,
                "start",
                {
                    "step": step,
                    "task_id": config["tasks"][step]["id"],
                    "condition": config["tasks"][step]["condition"],
                    **extra,
                },
            )
        return
    task = config["tasks"][step]
    if not started or task["condition"] != "ai" or action not in {"ask", "receipt"}:
        raise ValueError("AI is unavailable in this condition")
    rid = str(UUID(one("request_id")))
    existing = next((r for r in requests(records, step) if r["data"]["request_id"] == rid), None)
    if action == "ask":
        question = runtime.question(one("question", 1500))
        if not question:
            raise ValueError("Enter a question")
        if existing:
            if question != existing["data"]["question"]:
                raise ValueError("Original request cannot change its question")
            if reply(records, rid):
                return
            action = "receipt"  # Recovery only: never resend a locally pending question.
        elif (
            pending(records, step)
            or len(requests(records, step)) >= 4
            or rid != request_id(config, records, step)
        ):
            raise ValueError("Resolve the original question or preserve the limit")
        else:
            emit(
                folder,
                config,
                records,
                "ai_request",
                {"step": step, "task_id": task["id"], "request_id": rid, "question": question},
            )
    elif not existing:
        raise ValueError("No original question exists")
    prior = reply(records, rid)
    if action == "receipt" and prior and prior["data"]["receipt"].get("status") != "processing":
        return
    try:
        port = runtime.port(config, task)
        result = port.receipt(rid) if action == "receipt" else port.ask(question, rid)
        records = history(folder, config)
        emit(
            folder,
            config,
            records,
            "ai_reply",
            {"step": step, "request_id": rid, "receipt": result},
        )
    except Exception as exc:
        # Original admission and provider audit remain; no second supplier attempt.
        records = history(folder, config)
        unknown = {
            "step": step,
            "request_id": rid,
            "error_code": getattr(exc, "code", "receipt_unavailable"),
            "message": "请求未取得可展示回执；保留未知，只查询原请求。",
        }
        if not any(
            r["kind"] == "ai_unknown" and all(r["data"].get(k) == v for k, v in unknown.items())
            for r in records
        ):
            emit(folder, config, records, "ai_unknown", unknown)


def render(config, records, endpoint, error="", draft=None, field_errors=None):
    esc = html.escape
    draft = draft or {}
    field_errors = field_errors or {}

    def error_attrs(k):
        return f' aria-invalid="true" aria-describedby="error-{k}"' if k in field_errors else ""

    def field_error(k):
        return (
            f'<p class="field-error" id="error-{k}">{esc(field_errors[k])}</p>'
            if k in field_errors
            else ""
        )

    def entered(k):
        return draft.get(k, [""])[0] if len(draft.get(k, [""])) == 1 else ""

    def hidden(k, v):
        return f'<input type="hidden" name="{k}" value="{esc(str(v), quote=True)}">'

    def select(k, title, options):
        return (
            f'<label for="e4-{k}">'
            + title
            + f'</label><select id="e4-{k}" name="{k}" required{error_attrs(k)}>'
            '<option value="">请选择</option>'
            + "".join(
                f'<option value="{v}"'
                + (" selected" if entered(k) == v else "")
                + f">{esc(t)}</option>"
                for v, t in options.items()
            )
            + "</select>"
            + field_error(k)
        )

    def textarea(k, title, limit):
        return (
            f'<label for="e4-{k}">{title}</label>'
            f'<textarea id="e4-{k}" name="{k}" maxlength="{limit}"{error_attrs(k)}>'
            f"{esc(entered(k))}</textarea>" + field_error(k)
        )

    answers, started, sealed = active(records)
    form = f'<form method="post" action="{esc(endpoint)}">' + hidden("csrf", config["csrf"])
    content = f'<p role="alert">{esc(error)}</p>' if error else ""
    if field_errors:
        content += (
            '<ul class="error-summary" aria-label="需要修改的项目">'
            + "".join(
                f'<li><a href="#e4-{esc(k, quote=True)}">{esc(message)}</a></li>'
                for k, message in field_errors.items()
            )
            + "</ul>"
        )
    withdrawn = any(r["kind"] == "withdraw" for r in records)
    if withdrawn:
        content += (
            "<h1>本轮已提前结束</h1><p>已有记录保留，未答项目没有补填，"
            "不计为完整试用或本人封存。</p>"
        )
    elif sealed:
        content += (
            "<h1>本轮记录已封存</h1><p>请交还电脑，告知协调者已完成；正确性仍待按证据复核。</p>"
        )
    elif len(answers) == 4:
        content += (
            "<h1>本轮反馈</h1>"
            + form
            + hidden("action", "seal")
            + textarea("feedback", "最困惑的地方或建议", 4000)
            + '<label class="check"><input type="checkbox" name="attestation" '
            'value="independent" required>我亲自操作和作答，求助、AI使用和中断已如实记录；'
            "封存后不能覆盖原答案。</label><button>封存本轮四项记录</button></form>"
        )
    elif not started:
        label = "开始本轮试用" if not records else "开始下一项"
        content += (
            f"<h1>{label}</h1><p>点击开始后显示任务并计时；思考、切页和空闲计入。仅阅读，不提交运行或预约。</p>"
            + form
            + hidden("action", "start")
            + hidden("step", len(answers))
        )
        if not records:
            content += (
                select(
                    "familiarity",
                    "对平台的熟悉程度",
                    {"new": "从未使用", "seen": "只看过展示", "used": "曾使用过"},
                )
                + select(
                    "seen_cases",
                    "是否看过本轮四条用例（N003至N006）",
                    {"yes": "看过", "no": "没看过", "unsure": "不确定"},
                )
                + '<label class="check"><input type="checkbox" name="independent" '
                'value="yes" required>我未参与项目实现，将亲自操作和作答，不让开发者代填。</label>'
            )
        content += f"<button>{label}</button></form>"
    else:
        step = len(answers)
        task = config["tasks"][step]
        content += (
            f"<small>{esc(config['participant'])} · {step + 1} / 4</small>"
            f"<h1>{esc(task['case_key'])}</h1><p>{esc(task['instruction'])}</p>"
            '<p class="condition">'
            + (
                "可使用本页实验助手"
                if task["condition"] == "ai"
                else "仅使用基础指南；本题不使用AI"
            )
            + "</p>"
            + '<a class="platform" target="_blank" rel="noopener noreferrer" '
            f'href="http://127.0.0.1:5173/library?guide_case={task["case_id"]}">'
            "打开原开发用例 ↗</a>"
        )
        if task["condition"] == "ai":
            content += '<section class="assistant" aria-label="实验助手"><h2>实验助手</h2>'
            for request in requests(records, step):
                rid = request["data"]["request_id"]
                value = reply(records, rid)
                content += (
                    "<article><strong>你的问题</strong><p>"
                    + esc(request["data"]["question"])
                    + "</p>"
                )
                if value:
                    receipt = value["data"]["receipt"]
                    content += (
                        '<p role="status">'
                        + esc(
                            "回答待核对"
                            if receipt.get("status") == "completed"
                            else receipt.get("error") or "未取得有效回答"
                        )
                        + "</p>"
                    )
                    answer = receipt.get("answer") or {}
                    for key, title in [
                        ("facts", ""),
                        ("possible_causes", "可能原因"),
                        ("missing_evidence", "证据缺口"),
                        ("next_steps", "下一步"),
                    ]:
                        if answer.get(key):
                            content += ("<h3>" + title + "</h3>" if title else "") + "".join(
                                "<p>"
                                + esc(line if isinstance(line, str) else line["text"])
                                + "</p>"
                                for line in answer[key]
                            )
                    if "case.read" in answer.get("action_ids", []):
                        content += (
                            '<a class="platform" target="_blank" rel="noopener noreferrer" '
                            f'href="http://127.0.0.1:5173/library?guide_case={int(task["case_id"])}">'
                            "查看所选用例说明 ↗</a>"
                        )
                    content += (
                        "<details><summary>原回执、来源与调用明细</summary><pre>"
                        + esc(json.dumps(receipt, ensure_ascii=False, indent=2))
                        + "</pre></details>"
                    )
                else:
                    content += '<p role="status">结果未知；只查询原回执，不重新发送。</p>'
                content += "</article>"
            outstanding = pending(records, step)
            if outstanding:
                content += (
                    form
                    + hidden("action", "receipt")
                    + hidden("step", step)
                    + hidden("request_id", outstanding["data"]["request_id"])
                    + "<button>查询原问题回执</button></form>"
                )
            elif len(requests(records, step)) < 4:
                content += (
                    form
                    + hidden("action", "ask")
                    + hidden("step", step)
                    + hidden("request_id", request_id(config, records, step))
                    + textarea("question", "询问当前用例", 1500)
                    + "<p>发送将调用付费模型；本题会话上限约¥0.67（项目参考换算），"
                    "原始账本以美元计量。</p><button>发送给实验助手</button></form>"
                )
            else:
                content += '<p role="status">本题问题额度已用完，请根据已有证据作答。</p>'
            content += "</section>"
        content += (
            form
            + hidden("action", "answer")
            + hidden("step", step)
            + select("status", "自报完成情况", original.STATES)
        )
        for key, title, limit in [
            ("conclusion", "你的判断", 2000),
            ("evidence_location", "证据位置（找到时填写）", 1000),
            ("evidence_excerpt", "原文片段（找到时填写）", 2000),
            ("evidence_reason", "为什么支持判断（找到时填写）", 1500),
            ("missing_reason", "未找到的证据或不确定原因", 1500),
            ("next_action", "下一步安全操作", 1000),
        ]:
            if key == "evidence_location":
                content += select(
                    "evidence_found",
                    "是否找到了支持判断的证据",
                    {"yes": "找到了", "no": "未找到或不确定"},
                )
            content += textarea(key, title, limit)
        content += (
            '<label for="e4-help_count">向人求助次数</label>'
            '<input id="e4-help_count" type="number" name="help_count" '
            'min="0" max="20" required value="'
            + esc(entered("help_count"), quote=True)
            + '"'
            + error_attrs("help_count")
            + ">"
            + field_error("help_count")
            + select("used_ai", "本题是否使用或尝试使用AI", {"yes": "有", "no": "没有"})
            + select("interrupted", "是否中断或离开", {"yes": "有", "no": "没有"})
            + textarea(
                "deviation_note", "实际求助、AI使用、中断或任务外操作（有发生时具体说明）", 2000
            )
            + "<p>保存后保留原答案；不确定有效。不要填写密钥、密码或真实个人信息。</p>"
            "<button>保存本项判断与证据</button></form>"
        )
    if records and not sealed and not withdrawn:
        content += (
            '<details class="early-exit"><summary>提前结束本轮试用</summary>'
            + form
            + hidden("action", "withdraw")
            + textarea("stop_reason", "停止原因或反馈（可选）", 2000)
            + "<p>结束后已有答案和调用记录保留；当前未保存内容不会提交，剩余题目无需补答。"
            "不能恢复本轮，也不会自动退款或解除未知调用预留。</p>"
            + '<label class="check" for="e4-stop_confirmation">'
            '<input id="e4-stop_confirmation" type="checkbox" '
            'name="stop_confirmation" value="yes" required'
            + error_attrs("stop_confirmation")
            + ">我确认提前结束本轮。</label>"
            + field_error("stop_confirmation")
            + "<button>确认提前结束</button></form></details>"
        )
    style = Path(__file__).with_name("style.css").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>T19 E4试用</title><style>" + style + "</style><main>" + content + "</main></html>"
    ).encode()


def handler(folder, config, runtime):
    endpoint = "/trial/" + config["token"]
    parent = original.handler(folder, config)

    class Handler(parent):
        def send(self, status, body):
            self.body_length = len(body)
            super().send(status, body)

        def end_headers(self):
            self.send_header("Content-Length", str(getattr(self, "body_length", 0)))
            self.send_header("Connection", "close")
            super().end_headers()

        def check(self):
            if not self.valid():
                self.send(404, b"Not found")
                return False
            try:
                runtime.check()
                if canonical_hash(ui_manifest()) != config["recording_hash"] or datetime.now(
                    UTC
                ) >= datetime.fromisoformat(config["expires_at"]):
                    raise ValueError("Recording source changed or expired")
                return True
            except Exception:
                self.send(
                    409,
                    (
                        '<meta charset="utf-8">本轮来源、授权或期限不可确认，'
                        "原记录保留，请联系协调者。"
                    ).encode(),
                )
                return False

        def do_GET(self):
            if self.check():
                try:
                    self.send(200, render(config, history(folder, config), endpoint))
                except (ValueError, KeyError):
                    self.send(
                        409, '<meta charset="utf-8">原记录校验失败，已停止本轮。</meta>'.encode()
                    )

        def do_POST(self):
            if (
                not self.valid()
                or self.headers.get("Origin") != f"http://127.0.0.1:{config['port']}"
            ):
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
                data = parse_qs(
                    self.rfile.read(size).decode(),
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=24,
                )
                mutate(folder, config, data, runtime)
            except (ValueError, KeyError, IndexError, UnicodeError) as exc:
                try:
                    return self.send(
                        409,
                        render(
                            config,
                            history(folder, config),
                            endpoint,
                            str(exc)
                            if isinstance(exc, FormError)
                            else "未受理：操作或原记录状态不匹配。已填写内容保留，请核对当前步骤。",
                            draft=data,
                            field_errors=exc.fields if isinstance(exc, FormError) else None,
                        ),
                    )
                except (ValueError, KeyError):
                    return self.send(409, b"Record integrity failed; preserve original files")
            self.send_response(303)
            self.send_header("Location", endpoint)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--participant", choices=("P01", "P02", "P03"), default="P01")
    parser.add_argument("--port", type=int, default=18037)
    args = parser.parse_args()
    settings = Settings()
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    runtime = Runtime(args.package, engine, redis, settings)
    runtime.check()
    initialize(redis, args.package)
    folder, config = prepare(runtime, args.participant, args.port)
    with original.process_lock(folder):
        server = HTTPServer(("127.0.0.1", args.port), handler(folder, config, runtime))
        server.timeout = 1
        print(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{args.port}/trial/{config['token']}",
                    "participant": args.participant,
                    "expires_at": config["expires_at"],
                    "recording_hash": config["recording_hash"],
                    "package_hash": config["package_hash"],
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

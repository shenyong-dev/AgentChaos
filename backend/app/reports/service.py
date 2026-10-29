"""Owned, immutable report files and separately audited human defect decisions."""

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.evaluation.service import load_evidence
from app.evidence.reader import item as event_item
from app.evidence.recorder import EVIDENCE_ROOT, redact
from app.executors.control import advisory
from app.executors.service import RunService
from app.models import (
    Artifact,
    AssertionResult,
    Defect,
    KnowledgeDocument,
    Target,
    TargetVersion,
    TestExecution,
    TestRun,
    TraceEvent,
    User,
)
from app.reports.analysis import (
    candidate_fingerprint,
    compare,
    encoded,
    latest_rows,
    markdown,
    reporter_manifest,
    statistics,
)

ROOT = PROJECT_ROOT / ".local/reports"
EXPORTS = {
    "report_json": ("json", "application/json"),
    "report_markdown": ("md", "text/markdown; charset=utf-8"),
    "report_bundle": ("zip", "application/zip"),
}
MAX_BYTES = 64 * 1024 * 1024
VERIFY_SCRIPT = b'''"""Read-only verification: python verify.py report.zip"""
import hashlib,json,sys,zipfile
from pathlib import PurePosixPath
with zipfile.ZipFile(sys.argv[1]) as archive:
    names=archive.namelist()
    assert len(names)==len(set(names)), "duplicate members"
    for name in names:
        path=PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts, "unsafe path"
        assert "\\\\" not in name, "unsafe separator"
    manifest=json.loads(archive.read("manifest.json"))
    assert set(names)==set(manifest["files"])|{"manifest.json"}, "member list mismatch"
    for name,record in manifest["files"].items():
        value=archive.read(name)
        assert len(value)==record["bytes"], name
        assert hashlib.sha256(value).hexdigest()==record["sha256"], name
    print("Verified",len(manifest["files"]),"files.")
    print("Matching hashes do not establish Agent quality or semantic correctness.")
'''


def assertion_view(row):
    return {
        "id": row.id,
        "key": row.assertion_key,
        "metric": row.metric_name,
        "evaluator_kind": row.evaluator_kind,
        "evaluator_version": row.evaluator_version,
        "verdict": row.verdict,
        "expected": row.expected_json,
        "actual": row.actual_json,
        "evidence_refs": row.evidence_refs_json,
        "reason": row.reason,
        "judge_usage": row.judge_usage_json,
    }


def defect_view(row):
    value = {
        "id": row.id,
        "defect_key": row.defect_key,
        "target_id": row.target_id,
        "first_execution_id": row.first_execution_id,
        "category": row.category,
        "severity": row.severity,
        "status": row.status,
        "fingerprint": row.fingerprint,
        "evidence_refs": row.evidence_refs_json,
        "suspected_cause": row.suspected_cause,
        "reviewer_id": row.reviewer_id,
        "review_note": row.review_note,
        "fixed_version_id": row.fixed_version_id,
    }
    return {**value, "revision_hash": canonical_hash(value)}


class ReportService:
    def __init__(self, engine, settings, *, clock=None):
        self.engine, self.settings = engine, settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.directory = ROOT / hashlib.sha256(engine.url.database.encode()).hexdigest()[:12]

    def _owned_defect(self, session, owner, key, *, lock=False):
        query = (
            select(Defect).join(Target).where(Defect.defect_key == key, Target.owner_id == owner)
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise AgentError("defect_not_found", "找不到缺陷。", 404)
        execution = session.get(TestExecution, row.first_execution_id)
        RunService.owned_run(session, owner, session.get(TestRun, execution.run_id).run_uuid)
        return row, execution

    def _snapshot(self, session, owner, identifier):
        run = RunService.owned_run(session, owner, identifier)
        if run.status not in {"completed", "failed", "cancelled"}:
            raise AgentError("report_not_terminal", "运行尚未结束，不能冻结报告或提前下结论。", 409)
        if canonical_hash(run.manifest_json) != run.manifest_hash:
            raise AgentError("report_manifest_integrity", "运行清单哈希不符。", 503)
        version = session.get(TargetVersion, run.target_version_id)
        if (
            run.manifest_json.get("target_version_id") != version.id
            or run.manifest_json.get("target") != version.model_config_json
            or canonical_hash(
                {
                    key: value
                    for key, value in version.model_config_json.items()
                    if key != "config_hash"
                }
            )
            != version.config_hash
        ):
            raise AgentError("report_target_integrity", "目标版本与冻结配置不一致。", 503)
        target = session.get(Target, version.target_id)
        if target.owner_id != owner:
            raise AgentError("run_not_found", "找不到运行。", 404)
        executions = list(
            session.scalars(
                select(TestExecution)
                .where(TestExecution.run_id == run.id)
                .order_by(TestExecution.id)
            )
        )
        planned = len(run.manifest_json.get("cases", [])) * run.manifest_json.get("repetitions", 0)
        if not planned or len(executions) != planned:
            raise AgentError("report_execution_gap", "计划与持久执行数量不一致。", 503)
        rows = list(
            session.scalars(
                select(AssertionResult)
                .join(TestExecution)
                .where(TestExecution.run_id == run.id)
                .order_by(AssertionResult.id)
            )
        )
        entries = []
        for execution in executions:
            evidence = load_evidence(session, run, execution)
            content = execution.input_snapshot_json.get("content", {})
            entries.append(
                {
                    **RunService.execution_view(execution),
                    "case_key": content.get("case_key"),
                    "case_hash": execution.input_snapshot_json.get("content_hash"),
                    "case": execution.input_snapshot_json,
                    "database_execution_id": execution.id,
                    "gateway_mode": (execution.timing_json or {}).get("gateway_mode"),
                    "assertions": [
                        assertion_view(row) for row in rows if row.execution_id == execution.id
                    ],
                    "external_user_inputs": [
                        event["payload"].get("text")
                        for event in evidence.events
                        if event["type"] == "input.received"
                        and event["payload"].get("source") == "input"
                    ],
                    "evidence_integrity": {
                        "complete": evidence.complete,
                        "issues": evidence.issues,
                        "errors": evidence.integrity_errors,
                    },
                }
            )
        event_rows = list(
            session.scalars(
                select(TraceEvent)
                .where(TraceEvent.run_id == run.id)
                .order_by(TraceEvent.run_seq_no)
            )
        )
        if [event.run_seq_no for event in event_rows] != list(range(1, run.event_seq + 1)):
            raise AgentError("report_event_gap", "运行事件序列不完整，不能生成完整证据包。", 503)
        mapping = {execution.id: execution.execution_uuid for execution in executions}
        events = [event_item(event, run, mapping.get(event.execution_id)) for event in event_rows]
        assertion_refs = {"assertion:" + str(row.id) for row in rows}
        defects = []
        for row in session.scalars(
            select(Defect).where(Defect.target_id == target.id).order_by(Defect.id)
        ):
            if not assertion_refs.intersection(row.evidence_refs_json):
                continue
            audits = list(
                session.scalars(
                    select(Artifact)
                    .join(TestRun)
                    .where(
                        TestRun.created_by == owner,
                        Artifact.kind == "defect_review",
                        Artifact.metadata_json["defect_key"].as_string() == row.defect_key,
                    )
                    .order_by(Artifact.id)
                )
            )
            defects.append(
                {
                    **defect_view(row),
                    "reviews": [self._read_file(artifact, json_value=True) for artifact in audits],
                    "review_artifacts": [
                        {
                            "artifact_id": artifact.id,
                            "sha256": artifact.sha256,
                            "bytes": artifact.size_bytes,
                        }
                        for artifact in audits
                    ],
                }
            )
        knowledge = []
        for frozen in run.manifest_json.get("target", {}).get("retrieval", {}).get("documents", []):
            document = session.get(KnowledgeDocument, frozen.get("id"))
            matches = (
                document
                and document.content_hash == frozen.get("content_hash")
                and hashlib.sha256(document.content.encode()).hexdigest() == document.content_hash
            )
            knowledge.append(
                {
                    "frozen": frozen,
                    "available": bool(matches),
                    "content": document.content if matches else None,
                }
            )
        report = {
            "schema": "agentchaos-report-v1",
            "reporter": reporter_manifest(),
            "run": {
                "run_id": run.run_uuid,
                "status": run.status,
                "manifest_hash": run.manifest_hash,
                "manifest": run.manifest_json,
                "budget": run.budget_json,
                "summary": run.summary_json,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            },
            "target": {
                "target_id": target.id,
                "name": target.name,
                "version_id": version.id,
                "version_no": version.version_no,
                "config_hash": version.config_hash,
                "prompt_hash": version.prompt_hash,
                "graph_revision": version.graph_revision,
                "adapter_revision": version.adapter_revision,
                "tool_revision": version.tool_revision,
            },
            "executions": entries,
            "statistics": statistics(entries),
            "defects": defects,
            "knowledge": knowledge,
            "source_event_count": len(events),
            "boundary": (
                "Observed snapshots, original assertions and separately recorded reviews. "
                "Fault injection, cancellation, unknown evidence and platform errors "
                "are not automatically Agent defects. No formal V0/V1 quality claim."
            ),
        }
        return run, redact(report, self.settings), events

    def read(self, owner, identifier):
        with Session(self.engine) as session:
            _, report, _ = self._snapshot(session, owner, identifier)
            return report

    def comparison(self, owner, left, right):
        with Session(self.engine) as session:
            _, before, _ = self._snapshot(session, owner, left)
            _, after, _ = self._snapshot(session, owner, right)
            return compare(before, after)

    def collect(self, owner, identifier):
        # Serialize grouping across runs of the same owner; no cross-owner membership.
        with (
            advisory(self.engine, f"T15-defects:{owner}"),
            Session(self.engine) as session,
            session.begin(),
        ):
            _, report, _ = self._snapshot(session, owner, identifier)
            found = []
            for execution in report["executions"]:
                if execution["status"] not in {"passed", "failed", "inconclusive"}:
                    continue
                for kind in ("rule", "ai"):
                    for result in latest_rows(execution["assertions"], kind):
                        if result["verdict"] != "failed":
                            continue
                        fingerprint = candidate_fingerprint(report, execution, result)
                        key = "ACD-" + fingerprint[:40]
                        row = session.scalar(select(Defect).where(Defect.defect_key == key))
                        refs = [
                            "assertion:" + str(result["id"]),
                            "execution:" + execution["execution_id"],
                            *result["evidence_refs"],
                        ]
                        if row is None:
                            row = Defect(
                                defect_key=key,
                                target_id=report["target"]["target_id"],
                                first_execution_id=execution["database_execution_id"],
                                category="rule_violation" if kind == "rule" else "ai_signal",
                                severity="medium",
                                fingerprint=fingerprint,
                                status="open",
                                evidence_refs_json=list(dict.fromkeys(refs)),
                                suspected_cause=None,
                            )
                            session.add(row)
                            session.flush()
                        else:
                            self._owned_defect(session, owner, key)
                            merged = list(dict.fromkeys([*row.evidence_refs_json, *refs]))
                            if merged != row.evidence_refs_json:
                                row.evidence_refs_json = merged
                        found.append(defect_view(row))
            return {
                "items": list({item["defect_key"]: item for item in found}.values()),
                "boundary": (
                    "Unreviewed candidates; medium is a provisional default, "
                    "not an assessed severity."
                ),
            }

    def defects(self, owner, *, after=0, limit=20, status=None):
        with Session(self.engine) as session:
            query = select(Defect).join(Target).where(Target.owner_id == owner, Defect.id > after)
            if status:
                query = query.where(Defect.status == status)
            rows = list(session.scalars(query.order_by(Defect.id).limit(limit + 1)))
            return {
                "items": [defect_view(row) for row in rows[:limit]],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    def defect(self, owner, key):
        with Session(self.engine) as session:
            row, first = self._owned_defect(session, owner, key)
            audits = [
                self._read_file(artifact, json_value=True)
                for artifact in session.scalars(
                    select(Artifact)
                    .where(Artifact.run_id == first.run_id, Artifact.kind == "defect_review")
                    .order_by(Artifact.id)
                )
                if artifact.metadata_json.get("defect_key") == key
            ]
            ids = [
                int(ref.removeprefix("assertion:"))
                for ref in row.evidence_refs_json
                if ref.startswith("assertion:") and ref.removeprefix("assertion:").isdigit()
            ]
            occurrences = []
            for assertion, execution, run in session.execute(
                select(AssertionResult, TestExecution, TestRun)
                .join(TestExecution, AssertionResult.execution_id == TestExecution.id)
                .join(TestRun, TestExecution.run_id == TestRun.id)
                .where(AssertionResult.id.in_(ids), TestRun.created_by == owner)
            ):
                occurrences.append(
                    {
                        "run_id": run.run_uuid,
                        "execution_id": execution.execution_uuid,
                        "target_version_id": run.target_version_id,
                        "assertion": assertion_view(assertion),
                    }
                )
            return redact(
                {**defect_view(row), "occurrences": occurrences, "reviews": audits}, self.settings
            )

    def _write(self, session, run, kind, payload, metadata, written):
        directory = self.directory / run.run_uuid
        directory.mkdir(parents=True, exist_ok=True)
        suffix = EXPORTS.get(kind, ("json", ""))[0]
        path = directory / (uuid4().hex + "." + suffix)
        if len(payload) > MAX_BYTES:
            raise AgentError("report_size_limit", "报告或证据包超过64MiB上限。", 413)
        with path.open("xb") as file:
            file.write(payload)
        written.append(path)
        row = Artifact(
            run_id=run.id,
            kind=kind,
            relative_path=path.relative_to(PROJECT_ROOT).as_posix(),
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            generated_by_revision="T15-v1",
            metadata_json=metadata,
        )
        session.add(row)
        session.flush()
        return row

    def _read_file(self, row, *, json_value=False):
        path = (PROJECT_ROOT / row.relative_path).resolve()
        if not path.is_relative_to(self.directory.resolve()):
            raise AgentError("report_path_invalid", "报告路径或数据库归属不符。", 503)
        try:
            payload = path.read_bytes()
            if len(payload) != row.size_bytes or hashlib.sha256(payload).hexdigest() != row.sha256:
                raise ValueError()
            return json.loads(payload) if json_value else payload
        except (OSError, ValueError):
            raise AgentError("report_integrity_error", "报告文件缺失或哈希不符。", 503) from None

    def review(self, owner, key, request):
        written = []
        try:
            with (
                advisory(self.engine, f"T15-defects:{owner}"),
                Session(self.engine) as session,
                session.begin(),
            ):
                row, first = self._owned_defect(session, owner, key, lock=True)
                body = request.model_dump(mode="json")
                for artifact in session.scalars(
                    select(Artifact).where(
                        Artifact.run_id == first.run_id, Artifact.kind == "defect_review"
                    )
                ):
                    if artifact.metadata_json.get("request_id") == str(request.request_id):
                        old = self._read_file(artifact, json_value=True)
                        if old["request_hash"] != canonical_hash(body) or old["defect_key"] != key:
                            raise AgentError(
                                "review_id_conflict", "复核请求标识已绑定其他内容。", 409
                            )
                        return old
                before = defect_view(row)
                if before["revision_hash"] != request.expected_hash:
                    raise AgentError(
                        "defect_revision_conflict", "缺陷已更新，请重新读取后复核。", 409
                    )
                if not request.note.strip():
                    raise AgentError("review_note_required", "请填写人工复核依据。", 400)
                allowed = {
                    "open": {"confirmed", "rejected"},
                    "confirmed": {"fixed", "rejected"},
                    "rejected": {"confirmed"},
                    "fixed": {"verified", "confirmed"},
                    "verified": {"confirmed"},
                }
                if request.decision not in allowed[row.status]:
                    raise AgentError(
                        "defect_transition_invalid", "缺陷状态变更不符合确认、修复、验证顺序。", 409
                    )
                if request.decision not in {"fixed", "verified"} and (
                    request.fixed_version_id or request.verification_execution_id
                ):
                    raise AgentError(
                        "review_extra_verification", "此决定不接受修复或验证参数。", 400
                    )
                if request.decision in {"fixed", "verified"}:
                    version = session.get(TargetVersion, request.fixed_version_id)
                    if (
                        not version
                        or version.target_id != row.target_id
                        or version.id == session.get(TestRun, first.run_id).target_version_id
                    ):
                        raise AgentError(
                            "fixed_version_mismatch", "请选择该测试对象的修复版本。", 409
                        )
                verification = None
                if request.decision == "verified":
                    if not request.verification_execution_id:
                        raise AgentError(
                            "verification_required", "验证修复需要匹配的实际通过执行。", 409
                        )
                    execution = session.scalar(
                        select(TestExecution)
                        .join(TestRun)
                        .where(
                            TestExecution.execution_uuid == str(request.verification_execution_id),
                            TestRun.created_by == owner,
                        )
                    )
                    if execution is None:
                        raise AgentError("verification_not_found", "找不到验证执行。", 404)
                    first_run = session.get(TestRun, first.run_id)
                    verification_run = session.get(TestRun, execution.run_id)
                    _, original, _ = self._snapshot(session, owner, first_run.run_uuid)
                    _, repaired, _ = self._snapshot(session, owner, verification_run.run_uuid)
                    peer = next(
                        item
                        for item in repaired["executions"]
                        if item["execution_id"] == str(request.verification_execution_id)
                    )
                    matched = [
                        item
                        for item in latest_rows(peer["assertions"])
                        if candidate_fingerprint(repaired, peer, item) == row.fingerprint
                        and item["verdict"] == "passed"
                    ]
                    if (
                        row.category != "rule_violation"
                        or verification_run.target_version_id != request.fixed_version_id
                        or not peer["evidence_integrity"]["complete"]
                        or not compare(original, repaired)["conditions_match"]
                        or not matched
                    ):
                        raise AgentError(
                            "verification_mismatch",
                            "执行、预期、证据完整性或对比条件不匹配；不能标为已验证。",
                            409,
                        )
                    verification = {
                        "run_id": verification_run.run_uuid,
                        "execution_id": execution.execution_uuid,
                        "assertions": [item["id"] for item in matched],
                    }
                row.status, row.severity = request.decision, request.severity
                row.review_note = redact(request.note.strip(), self.settings)
                row.reviewer_id = owner
                row.fixed_version_id = (
                    request.fixed_version_id if request.decision in {"fixed", "verified"} else None
                )
                value = redact(
                    {
                        "defect_key": key,
                        "request_hash": canonical_hash(body),
                        "request": body,
                        "reviewed_at": self.clock().isoformat(),
                        "actor_id": owner,
                        "actor_username": session.get(User, owner).username,
                        "source": "owner_operator_review_not_independent_blind_label",
                        "before": before,
                        "after": defect_view(row),
                        "verification": verification,
                        "boundary": (
                            "Does not overwrite rules, AI judgments, events or booking facts."
                        ),
                    },
                    self.settings,
                )
                run = session.get(TestRun, first.run_id)
                self._write(
                    session,
                    run,
                    "defect_review",
                    encoded(value),
                    {"defect_key": key, "request_id": str(request.request_id)},
                    written,
                )
                return value
        except BaseException:
            self._discard(written)
            raise

    def _discard(self, paths):
        for path in paths:
            if path.resolve().is_relative_to(self.directory.resolve()):
                path.unlink(missing_ok=True)

    def _source_files(self, session, run):
        files = {}
        for row in session.scalars(
            select(Artifact).where(
                Artifact.run_id == run.id,
                Artifact.kind.in_(
                    ["trace_payload", "judge_request", "judge_result", "judge_call", "judge_review"]
                ),
            )
        ):
            path = (PROJECT_ROOT / row.relative_path).resolve()
            allowed = (
                (EVIDENCE_ROOT / run.run_uuid).resolve()
                if row.kind == "trace_payload"
                else (PROJECT_ROOT / ".local/judging" / self.directory.name).resolve()
            )
            try:
                if not path.is_relative_to(allowed) or row.size_bytes > MAX_BYTES:
                    raise ValueError()
                payload = path.read_bytes()
                if (
                    len(payload) != row.size_bytes
                    or hashlib.sha256(payload).hexdigest() != row.sha256
                ):
                    raise ValueError()
                value = json.loads(payload)
                if redact(value, self.settings) != value:
                    raise ValueError()
            except (OSError, ValueError):
                raise AgentError(
                    "report_source_invalid", "原始证据缺失、越界、未脱敏或哈希不符。", 503
                ) from None
            files[f"evidence/artifacts/{row.id}.json"] = payload
            if sum(map(len, files.values())) > MAX_BYTES:
                raise AgentError("report_size_limit", "原始证据总量超过64MiB。", 413)
        return files

    @staticmethod
    def export_view(rows):
        return {
            "items": [
                {
                    "artifact_id": row.id,
                    "kind": row.kind,
                    "sha256": row.sha256,
                    "bytes": row.size_bytes,
                    "expires_at": row.metadata_json["expires_at"],
                    "download": f"/api/v1/reports/exports/{row.id}/download",
                }
                for row in rows
            ]
        }

    def export(self, owner, identifier, request):
        written = []
        try:
            with (
                advisory(self.engine, "T15-export:" + str(identifier)),
                Session(self.engine) as session,
                session.begin(),
            ):
                run = RunService.owned_run(session, owner, identifier, lock=True)
                body_hash = canonical_hash(request.model_dump(mode="json"))
                old = [
                    row
                    for row in session.scalars(
                        select(Artifact)
                        .where(Artifact.run_id == run.id, Artifact.kind.in_(EXPORTS))
                        .order_by(Artifact.id)
                    )
                    if row.metadata_json.get("request_id") == str(request.request_id)
                ]
                if old:
                    if len(old) != 3 or any(
                        row.metadata_json.get("request_hash") != body_hash for row in old
                    ):
                        raise AgentError("export_id_conflict", "导出请求标识已绑定其他参数。", 409)
                    return self.export_view(old)
                run, report, events = self._snapshot(session, owner, identifier)
                if not report["knowledge"] or any(
                    not item["available"] for item in report["knowledge"]
                ):
                    raise AgentError(
                        "report_knowledge_unavailable",
                        "冻结知识缺失或哈希不符，不能导出完整证据包。",
                        503,
                    )
                report["export"] = {
                    "request_id": str(request.request_id),
                    "generated_at": self.clock().isoformat(),
                    "expires_at": (
                        self.clock() + timedelta(hours=request.expires_hours)
                    ).isoformat(),
                }
                data, md = encoded(report), markdown(report)
                files = self._source_files(session, run)
                for defect in report["defects"]:
                    for reference in defect["review_artifacts"]:
                        row = session.get(Artifact, reference["artifact_id"])
                        files[f"evidence/reviews/{row.id}.json"] = self._read_file(row)
                files.update(
                    {
                        "report.json": data,
                        "report.md": md,
                        "evidence/events.json": encoded(events),
                        "evidence/executions.json": encoded(report["executions"]),
                        "frozen/run-manifest.json": encoded(report["run"]["manifest"]),
                        "frozen/knowledge.json": encoded(report["knowledge"]),
                        "verify.py": VERIFY_SCRIPT,
                        "README.txt": (
                            b"Run: python verify.py report.zip\n"
                            b"The manifest verifies internal consistency, "
                            b"not source authenticity.\n"
                            b"Compare the ZIP SHA256 with the authenticated download record.\n"
                            b"Reports retain observed results and separately recorded reviews.\n"
                        ),
                    }
                )
                if sum(map(len, files.values())) > MAX_BYTES:
                    raise AgentError(
                        "report_size_limit", "导出内容超过64MiB，未产生部分报告。", 413
                    )
                files["manifest.json"] = encoded(
                    {
                        "schema": "agentchaos-evidence-package-v1",
                        "run_id": str(identifier),
                        "reporter": report["reporter"],
                        "files": {
                            name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                            for name, value in files.items()
                        },
                    }
                )
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, payload in sorted(files.items()):
                        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(info, payload)
                metadata = {
                    **report["export"],
                    "request_hash": body_hash,
                    "reporter": report["reporter"],
                }
                rows = [
                    self._write(session, run, kind, payload, metadata, written)
                    for kind, payload in (
                        ("report_json", data),
                        ("report_markdown", md),
                        ("report_bundle", buffer.getvalue()),
                    )
                ]
                return self.export_view(rows)
        except BaseException:
            self._discard(written)
            raise

    def download(self, owner, artifact_id):
        with Session(self.engine) as session:
            row = session.scalar(
                select(Artifact)
                .join(TestRun)
                .where(
                    Artifact.id == artifact_id,
                    Artifact.kind.in_(EXPORTS),
                    TestRun.created_by == owner,
                )
            )
            if row is None:
                raise AgentError("report_not_found", "找不到报告。", 404)
            if self.clock() >= datetime.fromisoformat(row.metadata_json["expires_at"]):
                raise AgentError(
                    "report_expired", "报告下载已过期；原记录保留，可显式生成新报告。", 410
                )
            suffix, media = EXPORTS[row.kind]
            return self._read_file(row), media, f"agentchaos-report-{row.id}.{suffix}", row.sha256

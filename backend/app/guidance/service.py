"""Read-only explanations and allowlisted navigation, with fresh object authorization."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.executors.service import RunService
from app.models import Artifact, Target, TargetVersion, TestExecution
from app.workbench.service import WorkbenchService


def catalog():
    value = json.loads(Path(__file__).with_name("catalog.json").read_bytes())
    return value, canonical_hash(value)


RUN_STEPS = {
    "queued": "已保存或排队，尚不能认为执行成功。检查Worker、Dispatcher和排队期限。",
    "running": "正在执行；查看具体执行和工具证据，不能仅凭运行状态判断预约成功。",
    "cancelling": "正在取消；已发生的工具副作用仍需核对，不会自动撤销预约。",
    "cancelled": "已取消；保留已发生的副作用，取消不等于业务回滚。",
    "completed": "执行已结束；继续核对各执行的规则、任务目标与SQL，不把结束当作通过。",
    "failed": "运行未正常完成；查看错误与原回执，不通过更换请求标识无条件重跑。",
    "waiting_input": "等待你的输入；先核对实际草稿，再通过原输入流程确认或拒绝。",
    "passed": "已声明规则通过；仍以对应SQL、任务目标和人工核对范围解释结论。",
    "inconclusive": "证据不足，不能当成业务通过，也不能把没有证据解释成0笔预约。",
    "error": "执行发生平台或调用错误；保留错误及未知提交状态，先查证原请求。",
    "skipped": "该执行已跳过，没有因此得到业务通过结论。",
}


class GuidanceService:
    def __init__(self, engine, settings, *, interaction_reader=None):
        self.engine, self.settings = engine, settings
        self.interaction_reader = interaction_reader

    @staticmethod
    def checked_catalog(values):
        value, digest = catalog()
        if values.catalog_hash != digest:
            raise AgentError("guide_catalog_changed", "指引版本已变化，请刷新页面后重新选择。", 409)
        return value, digest

    @staticmethod
    def identifier(kind, value):
        try:
            if kind in ("case", "version"):
                number = int(value)
                if number < 1 or str(number) != value:
                    raise ValueError
                return number
            return str(UUID(value))
        except (TypeError, ValueError):
            raise AgentError(
                "guide_identifier_invalid", "请提供当前页面的有效对象标识。", 400
            ) from None

    def selection(self, owner, values):
        kind, raw = values.object_kind, values.object_id
        if kind is None:
            return None
        identifier = self.identifier(kind, raw)
        if kind == "case":
            value = WorkbenchService(self.engine, self.settings).case(owner, identifier)
            guide = value["guidance"]
            return {
                "kind": kind,
                "id": raw,
                "title": f"{guide['case_key']} · 修订{guide['revision']}",
                "summary": guide["title"],
                "status": "approved_development",
                "facts": [
                    guide["risk"],
                    "前置知识：" + guide["prerequisites"]["knowledge_profile"],
                    "；".join(a["meaning"] for a in guide["assertions"]),
                ],
                "knowledge_compatible": guide["prerequisites"]["default_knowledge_compatible"],
                "references": [f"case:{identifier}:{value['content_hash']}"],
            }
        if kind == "interaction":
            if self.interaction_reader is None:
                raise AgentError("guide_observation_unavailable", "当前无法读取会话状态。", 503)
            result = self.interaction_reader(owner, identifier)
            return {
                "kind": kind,
                "id": raw,
                "title": "当前预约Agent会话",
                "summary": "结果未知先查原请求；新的确认仍需核对当前草稿和修订。",
                "status": str(result.get("status", "unknown")),
                "facts": [
                    "实际来源：" + str(result.get("mode", "unknown")),
                    "消息和草稿不会由基础指引自动发送或确认。",
                ],
                "references": ["interaction:" + identifier],
            }
        with Session(self.engine) as session:
            user = CaseService.role(session, owner)
            if kind == "version":
                CaseService.role(session, owner, "operator")
                version = session.scalar(
                    select(TargetVersion)
                    .join(Target)
                    .where(
                        TargetVersion.id == identifier,
                        Target.owner_id == owner,
                        Target.archived_at.is_(None),
                    )
                )
                if version is None:
                    raise AgentError("guide_object_not_found", "当前身份下找不到该版本。", 404)
                return {
                    "kind": kind,
                    "id": raw,
                    "title": f"对象版本 {version.version_no}",
                    "status": "frozen",
                    "summary": "版本冻结模型、源码、Prompt、知识和工具配置；"
                    "编号增大不证明可靠性提升。",
                    "facts": [
                        "配置哈希：" + version.config_hash,
                        "图版本：" + version.graph_revision,
                    ],
                    "references": [f"version:{identifier}:{version.config_hash}"],
                }
            if kind in ("generation", "calibration"):
                request_kind = "casegen_request" if kind == "generation" else "judge_request"
                request = session.scalar(
                    select(Artifact).where(
                        Artifact.kind == request_kind,
                        Artifact.run_id.is_(None),
                        Artifact.metadata_json["generation_id"].as_string() == identifier,
                    )
                )
                if not request or (user.role != "reviewer" and request.owner_id != owner):
                    raise AgentError("guide_object_not_found", "当前身份下找不到该批次。", 404)
                sealed = False
                if kind == "calibration":
                    sealed = (
                        session.scalar(
                            select(Artifact.id)
                            .where(
                                Artifact.kind == "judge_label",
                                Artifact.owner_id == request.owner_id,
                                Artifact.metadata_json["generation_id"].as_string() == identifier,
                                Artifact.metadata_json["phase"].as_string() == "seal",
                            )
                            .limit(1)
                        )
                        is not None
                    )
                return {
                    "kind": kind,
                    "id": raw,
                    "title": "校准批次" if kind == "calibration" else "生成候选批次",
                    "status": ("sealed" if sealed else "independent_labeling")
                    if kind == "calibration"
                    else "candidate_review",
                    "summary": (
                        "原人工标签已封存；分歧另存复核，不覆盖原标签。"
                        if sealed
                        else "独立标注尚未封存；这里只说明操作，不分析样本、不推荐标签。"
                    )
                    if kind == "calibration"
                    else "候选仍需按原审核流程逐条检查和确认。",
                    "facts": ["基础指引不读取样本正文、原标签、AI答案或候选内容。"],
                    "references": [f"{kind}:{identifier}:metadata"],
                }
            CaseService.role(session, owner, "operator")
            if kind == "execution":
                execution = session.scalar(
                    select(TestExecution).where(TestExecution.execution_uuid == identifier)
                )
                if execution is None:
                    raise AgentError("guide_object_not_found", "当前身份下找不到该执行。", 404)
                from app.models import TestRun

                run_uuid = session.scalar(
                    select(TestRun.run_uuid).where(TestRun.id == execution.run_id)
                )
            else:
                execution, run_uuid = None, identifier
            run = RunService.owned_run(session, owner, run_uuid)
            states = list(
                session.scalars(select(TestExecution.status).where(TestExecution.run_id == run.id))
            )
            status = (
                execution.status
                if execution
                else "waiting_input"
                if "waiting_input" in states
                else run.status
            )
            development = all(
                c.get("content", {}).get("dataset_split") == "dev"
                for c in run.manifest_json.get("cases", [])
            )
            return {
                "kind": kind,
                "id": raw,
                "run_id": run.run_uuid,
                "title": "当前执行" if execution else "当前运行",
                "status": status,
                "summary": RUN_STEPS.get(status, "状态尚未识别，请核对原始运行记录。"),
                "facts": [
                    "运行状态：" + run.status,
                    "缺少SQL证据不等于0笔预约。",
                    "仅说明流程和状态；校准、保留集内容不进入开发指引。",
                ],
                "restricted_content": not development,
                "references": [f"run:{run.run_uuid}:{run.manifest_hash}:status"],
            }

    def context(self, owner, values):
        value, digest = self.checked_catalog(values)
        with Session(self.engine) as session:
            user = CaseService.role(session, owner)
            role = user.role
        allowed = role in value["pages"][values.page]["roles"]
        if not allowed and values.object_kind:
            raise AgentError(
                "guide_role_required", "当前账号只能阅读通用说明，不能读取此对象。", 403
            )
        selected = self.selection(owner, values) if allowed else None
        now = datetime.now(UTC)
        actions = list(value["pages"][values.page]["actions"])
        if selected and selected["kind"] in ("run", "execution"):
            actions = (
                ["run.trace", "environment.worker"]
                if selected["status"] in ("queued", "running", "waiting_input", "error")
                else ["run.assertions", "run.trace", "run.semantic"]
            )
        return {
            "page": values.page,
            "catalog_version": value["version"],
            "catalog_hash": digest,
            "role": role,
            "allowed": allowed,
            "selection": selected,
            "actions": actions,
            "observed_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=30)).isoformat(),
            "boundary": "只读当前状态与确定性说明；不能推断故障根因、代替判断或执行操作。",
            "references": [f"help:{value['version']}:{digest}"],
        }

    def resolve(self, owner, values):
        value, _ = self.checked_catalog(values)
        action = value["actions"].get(values.action_id)
        if action is None:
            raise AgentError("guide_action_unknown", "此动作不在已实现的导航目录内。", 400)
        context = self.context(owner, values)
        if context["role"] not in action["roles"]:
            raise AgentError("guide_role_required", "当前角色不能使用此导航入口。", 403)
        selected = context["selection"]
        required = action.get("object_kind")
        if required and (
            not selected
            or (
                selected["kind"] not in ("run", "execution")
                if required == "run_or_execution"
                else selected["kind"] != required
            )
        ):
            raise AgentError("guide_selection_required", "请先在当前页面选择或载入对应对象。", 409)
        if values.action_id == "case.prepare" and not selected["knowledge_compatible"]:
            raise AgentError(
                "guide_knowledge_mismatch", "该修订的知识条件不匹配，不能带入默认运行。", 409
            )
        route = {"path": action["path"], "query": {}, "hash": action.get("hash", "")}
        if required == "case":
            route["query"][action["query_key"]] = selected["id"]
        elif required == "run_or_execution":
            route["path"] += "/" + selected["run_id"]
            route["query"]["view"] = action["view"]
        return {
            **context,
            "action_id": values.action_id,
            "route": route,
            "kind": "navigate_only",
            "boundary": "仅导航或带入未提交表单；执行、确认、审核和付费仍需原流程显式确认。",
        }

"""One bounded execution per task; waiting for a person never occupies a Worker."""

import threading
from datetime import datetime
from uuid import UUID, uuid5

from redis import Redis

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.runtime import AgentRuntime
from app.core.config import PROJECT_ROOT
from app.evidence.recorder import EvidenceRecorder
from app.executors.budget import ExecutorBudget
from app.executors.control import ExecutionControl
from app.executors.service import RunService, role_configs


def category(code):
    if code.startswith("model_"):
        return "model"
    if code.startswith(("budget_", "execution_step", "execution_timeout", "agent_deadline")):
        return "limit"
    if code in ("lease_lost", "execution_cancelled"):
        return "control"
    return "platform"


def run_execution(engine, redis, settings, execution_uuid, *, gateway_factory=None):
    service = RunService(engine, settings)
    claim = service.claim(execution_uuid)
    if claim is None:
        return {"claimed": False}
    control = ExecutionControl(engine, claim["id"], claim["token"])
    recorder = EvidenceRecorder(control, settings, claim)
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(settings.executor_lease_seconds / 4):
            try:
                service.heartbeat(claim["id"], claim["token"])
            except Exception:
                # The main path still checks DB time/token before every state/write boundary.
                return

    thread = threading.Thread(target=heartbeat, daemon=True, name="execution-heartbeat")
    thread.start()
    output = None
    try:
        if claim["manifest"]["roles"] != role_configs(settings):
            raise AgentError("run_config_mismatch", "Worker角色参数与冻结运行清单不一致。", 409)
        budget = ExecutorBudget(redis, settings, control, claim["run_uuid"], str(execution_uuid))
        if claim["recovery"]:
            with control.transaction() as (_, _, e):
                unsettled = [
                    ticket
                    for ticket in e.usage_json.get("tickets", {}).values()
                    if ticket["state"] == "reserved"
                ]
            for ticket in unsettled:
                budget.unknown(ticket)
        gateway = (
            gateway_factory(budget, control)
            if gateway_factory
            else ModelGateway(
                settings,
                budget,
                control=control,
                audit_directory=PROJECT_ROOT
                / ".local/t05/model-calls"
                / claim["run_uuid"]
                / str(execution_uuid),
            )
        )
        runtime = AgentRuntime(
            engine,
            redis,
            settings,
            prefix=settings.executor_namespace + ":session",
            control=control,
            budget=budget,
            gateway=gateway,
            recorder=recorder,
        )
        if runtime.manifest["config_hash"] != claim["manifest"]["target"]["config_hash"]:
            raise AgentError(
                "target_version_unavailable", "当前Worker代码/依赖不匹配冻结版本。", 409
            )
        sid, owner = claim["session_id"], claim["owner_id"]
        with control.transaction() as (_, _, e):
            timing = dict(e.timing_json)
            if claim["previous_token"]:
                # Compare-delete only the old token, after MySQL has fenced that attempt.
                redis.eval(
                    "if redis.call('GET',KEYS[1])==ARGV[1] then "
                    "return redis.call('DEL',KEYS[1]) end return 0",
                    1,
                    runtime._key(sid) + ":lock",
                    claim["previous_token"],
                )
            if not redis.exists(runtime._key(sid)) and (timing.get("inflight") or timing["cursor"]):
                raise AgentError(
                    "recovery_uncertain", "原会话元数据缺失，保留业务数据并等待人工恢复。", 409
                )
        output = runtime.create(
            owner,
            session_id=sid,
            scope_id=claim["scope_id"],
            reference_clock=datetime.fromisoformat(claim["case"]["dsl"]["reference_clock"]),
        )
        _, _, checkpoint = runtime.snapshot(owner, sid)
        if checkpoint.values:
            recorder.emit(
                "checkpoint.restored",
                checkpoint_id=(checkpoint.config or {})
                .get("configurable", {})
                .get("checkpoint_id"),
                next_nodes=list(checkpoint.next),
            )
        turns = claim["case"]["dsl"]["turns"]
        while True:
            with control.transaction() as (_, _, e):
                timing = dict(e.timing_json)
                inflight = timing.get("inflight")
                if inflight:
                    cache = redis.get(
                        runtime._key(sid) + ":action:" + inflight["body"]["request_id"]
                    )
                    if not cache:
                        # A tool may already have committed. Automatic reinvocation here is unsafe.
                        raise AgentError(
                            "recovery_uncertain",
                            "上次输入未形成完整回执，保留检查点与业务事实，需人工核对恢复。",
                            409,
                        )
                elif timing.get("manual_recovery"):
                    metadata, state, snapshot = runtime.snapshot(owner, sid)
                    if not snapshot.next or snapshot.interrupts or not state["turn_index"]:
                        raise AgentError(
                            "recovery_uncertain", "检查点不能证明存在可重试的失败节点。", 409
                        )
                    inflight = {
                        "kind": "retry",
                        "source": "recovery",
                        "body": {
                            "request_id": str(
                                uuid5(UUID(str(execution_uuid)), f"recovery:{e.attempt_no}")
                            ),
                            "expected_revision": metadata["revision"],
                        },
                    }
                    timing.pop("manual_recovery")
                elif timing.get("pending_input"):
                    supplied = timing["pending_input"]
                    inflight = {
                        "kind": "message",
                        "source": "input",
                        "body": {
                            "request_id": supplied["request_id"],
                            "text": supplied["text"],
                            "expected_revision": output["revision"],
                        },
                    }
                elif timing["cursor"] < len(turns):
                    inflight = {
                        "kind": "message",
                        "source": "script",
                        "body": {
                            "request_id": str(
                                uuid5(UUID(str(execution_uuid)), f"turn:{timing['cursor']}")
                            ),
                            "expected_revision": output["revision"],
                            "text": turns[timing["cursor"]]["user"],
                        },
                    }
                else:
                    break
                timing["inflight"] = inflight
                e.timing_json = timing
            recorder.emit(
                "input.received",
                event_id=str(
                    uuid5(
                        UUID(str(execution_uuid)),
                        f"{claim['attempt']}:input:{inflight['body']['request_id']}",
                    )
                ),
                request_id=inflight["body"]["request_id"],
                source=inflight["source"],
                text=inflight["body"].get("text"),
                expected_revision=inflight["body"]["expected_revision"],
            )
            output = runtime.act(owner, sid, inflight["body"], kind=inflight["kind"])
            with control.transaction() as (_, _, e):
                timing = dict(e.timing_json)
                if inflight["source"] == "script":
                    timing["cursor"] += 1
                elif inflight["source"] == "input":
                    timing.pop("pending_input", None)
                elif inflight["source"] == "recovery":
                    source = timing.pop("recovery_source", None)
                    if source == "script":
                        timing["cursor"] += 1
                    elif source == "input":
                        timing.pop("pending_input", None)
                timing.pop("inflight", None)
                timing["revision"] += 1
                e.timing_json, e.final_output_json = timing, output
        waiting = output["status"] == "waiting_confirmation"
        service.finish(
            claim["id"],
            claim["token"],
            "waiting_input" if waiting else "inconclusive",
            output,
            None if waiting else {"category": "evaluation", "code": "evaluation_not_implemented"},
        )
        return {"claimed": True, "status": "waiting_input" if waiting else "inconclusive"}
    except AgentError as exc:
        service.finish(
            claim["id"],
            claim["token"],
            "error",
            output,
            {"category": category(exc.code), "code": exc.code, "message": exc.message},
        )
        return {"claimed": True, "status": "error", "code": exc.code}
    except Exception as exc:
        # Provider/driver exception text can contain credentials: store only a type.
        service.finish(
            claim["id"],
            claim["token"],
            "error",
            output,
            {
                "category": "platform",
                "code": "executor_error",
                "exception_type": type(exc).__name__,
            },
        )
        return {
            "claimed": True,
            "status": "error",
            "code": "executor_error",
            "exception_type": type(exc).__name__,
        }
    finally:
        stop.set()
        thread.join(timeout=2)


def redis_connection(settings):
    return Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=15)

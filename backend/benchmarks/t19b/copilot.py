"""Server-bound trial port. Production assistant, prompt and daily settings stay intact."""

from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.versions import canonical_hash
from app.copilot.service import CopilotService
from app.copilot.sources import clean
from app.guidance.service import catalog
from app.schemas.copilot import CopilotAsk, CopilotCreate

from .budget import AUTHORIZATION, TrialBudget, approval
from .preparation import ROOT, check_current


class _PaidTrialService(CopilotService):
    def run(self, owner, binding, record, body, question, tools, result, check):
        # A gateway factory here selects a real separately capped provider port,
        # unlike production's factory used for offline controls.
        result["mode"] = "ai"
        return super().run(owner, binding, record, body, question, tools, result, check)


class TrialAssistant:
    def __init__(self, participant, task_id, package, engine, redis, settings):
        frozen = approval(package)
        check_current(package, engine)
        order = frozen["protocol"]["allocation"].get(participant, [])
        if [task_id, "ai"] not in order:
            raise AgentError("t19b_condition_forbidden", "本项为无AI条件或身份不在试用名单。", 403)
        self.task = next(t for t in frozen["protocol"]["tasks"] if t["id"] == task_id)
        self.package, self.engine = package, engine
        self.owner = frozen["protocol"]["owner"]
        self.binding = f"t19b:{frozen['package_hash']}:{participant}:{task_id}"
        # This copy never changes .env or the live daily service's Settings instance.
        self.settings = settings.model_copy(
            update={
                "copilot_budget_authorization_id": AUTHORIZATION,
                "copilot_budget_usd": 1,
                "copilot_allow_paid_model_calls": True,
                "copilot_local_unlimited_calls": False,
                "copilot_model": "deepseek-flash",
                "copilot_thinking": "disabled",
                "copilot_max_output_tokens": 1536,
                "model_timeout_seconds": 45,
                "executor_namespace": "ac:t19b:" + frozen["package_hash"],
            }
        )

        def gateway(inner):
            session_id = inner.session_key.rsplit(":", 1)[-1]
            budget = TrialBudget(redis, self.settings, session_id, package, engine)
            return ModelGateway(
                self.settings,
                budget,
                role="copilot",
                audit_directory=ROOT / "model-calls" / session_id,
            )

        self.service = _PaidTrialService(engine, redis, self.settings, gateway_factory=gateway)
        self.service.prefix = "ac:t19b:copilot:" + frozen["package_hash"][:16]
        self.creation_id = uuid5(NAMESPACE_URL, self.binding)
        self.admissions = self.service.prefix + ":questions:" + canonical_hash(self.binding)

    def check(self):
        approval(self.package)
        check_current(self.package, self.engine)

    def begin(self):
        self.check()
        return self.service.create(
            self.owner,
            self.binding,
            CopilotCreate(
                request_id=self.creation_id,
                page="library",
                catalog_hash=catalog()[1],
                object_kind="case",
                object_id=str(self.task["case_id"]),
            ),
        )

    def ask(self, question, request_id):
        self.check()
        rid = UUID(str(request_id))
        question = clean(question.strip(), self.settings)
        if not question:
            raise AgentError("t19b_question_empty", "请输入问题。", 400)
        created = self.begin()
        body = CopilotAsk(request_id=rid, context_hash=created["context_hash"], question=question)
        redis, sid = self.service.redis, created["session_id"]
        lock = redis.lock(self.admissions + ":lock", timeout=150, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            raise AgentError("t19b_busy", "原问题仍在处理，请查原回执。", 409)
        try:
            result = redis.eval(
                "local old=redis.call('HGET',KEYS[1],ARGV[1]); "
                "if old then if old==ARGV[2] then return 2 else return -1 end end; "
                "if tonumber(redis.call('HGET',KEYS[1],'count') or '0')>=4 then return 0 end; "
                "redis.call('HSET',KEYS[1],ARGV[1],ARGV[2]); "
                "redis.call('HINCRBY',KEYS[1],'count',1); return 1",
                1,
                self.admissions,
                str(rid),
                canonical_hash(question),
            )
            if result == -1:
                raise AgentError("t19b_request_conflict", "原请求标识不能更换问题。", 409)
            if not result:
                raise AgentError("t19b_question_limit", "本题四个问题已用完，请保留现有回答。", 429)
            if result == 2 and not redis.exists(self.service.turn_key(sid, rid)):
                raise AgentError(
                    "t19b_receipt_unknown", "原问题回执不可得，保留未知且不自动重发。", 409
                )
            return self.service.ask(self.owner, self.binding, sid, body, check=self.check)
        finally:
            if lock.owned():
                lock.release()

    def receipt(self, request_id):
        self.check()
        created = self.begin()
        return self.service.read_turn(
            self.owner, self.binding, created["session_id"], UUID(str(request_id))
        )

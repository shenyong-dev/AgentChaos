"""Reuse current assistant with this pilot's separate identity, budget and read-only action."""

from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.gateway import ModelGateway
from app.copilot.service import CopilotService
from app.copilot.sources import SourceTools, clean
from app.guidance.service import catalog
from app.schemas.copilot import CopilotAsk, CopilotCreate

from .budget import PilotBudget
from .package import AUTHORIZATION, ROOT, approval, check


class PilotSources(SourceTools):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_actions = [key for key in self.allowed_actions if key == "case.read"]


class PaidService(CopilotService):
    def run(self, owner, binding, record, body, question, tools, result, check):
        result["mode"] = "ai"
        return super().run(owner, binding, record, body, question, tools, result, check)


class Assistant:
    def __init__(self, participant, package, engine, redis, settings):
        if participant not in {"P01", "P02", "P03"}:
            raise ValueError("Invalid participant slot")
        frozen = approval(package)
        self.package, self.engine = package, engine
        self.settings = settings.model_copy(
            update={
                "app_env": "development",
                "cloud_deployment_id": "",
                "cloud_approval_sha256": "",
                "cloud_open_model_budget": False,
                "copilot_budget_authorization_id": AUTHORIZATION,
                "copilot_allow_paid_model_calls": True,
                "copilot_local_unlimited_calls": True,
                "copilot_budget_usd": 0,
                "copilot_model": "deepseek-flash",
                "copilot_thinking": "disabled",
                "copilot_max_output_tokens": 1536,
                "model_timeout_seconds": 45,
                "executor_namespace": "ac:p04:" + frozen["package_hash"],
            }
        )
        self.task = frozen["cases"][int(participant[-1]) - 1]
        self.binding = f"p04:{frozen['package_hash']}:{participant}"

        def gateway(inner):
            session_id = inner.session_key.rsplit(":", 1)[-1]
            budget = PilotBudget(redis, self.settings, session_id, package, engine)
            return ModelGateway(
                self.settings,
                budget,
                role="copilot",
                audit_directory=ROOT / "model-calls" / session_id,
            )

        self.service = PaidService(
            engine,
            redis,
            self.settings,
            tools_factory=lambda owner, context: PilotSources(
                engine, self.settings, owner, context
            ),
            gateway_factory=gateway,
        )
        self.service.prefix = "ac:p04:copilot:" + frozen["package_hash"][:16]
        self.creation_id = uuid5(NAMESPACE_URL, self.binding)

    def check(self):
        approval(self.package)
        check(self.package, self.engine, self.settings)

    def begin(self):
        self.check()
        return self.service.create(
            1,
            self.binding,
            CopilotCreate(
                request_id=self.creation_id,
                page="library",
                catalog_hash=catalog()[1],
                object_kind="case",
                object_id=str(self.task["case_id"]),
            ),
        )

    def question(self, value):
        return clean(value.strip(), self.settings)

    def ask(self, question, request_id):
        self.check()
        created = self.begin()
        body = CopilotAsk(
            request_id=UUID(request_id),
            context_hash=created["context_hash"],
            question=self.question(question),
        )
        return self.service.ask(1, self.binding, created["session_id"], body, check=self.check)

    def receipt(self, request_id):
        self.check()
        # Never recreate an expired session as a way to obtain a fresh request.
        session_id = str(
            uuid5(NAMESPACE_URL, f"{self.service.prefix}:1:{self.binding}:{self.creation_id}")
        )
        return self.service.read_turn(1, self.binding, session_id, UUID(request_id))

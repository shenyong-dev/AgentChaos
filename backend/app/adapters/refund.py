"""Local reference adapter. Shared Windows/API ↔ managed Linux/Worker volume only."""

import hashlib
import os
from contextlib import nullcontext
from importlib.metadata import version

from sqlalchemy import text

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.db.fixtures import validate_test_scope
from reference_agents.refund.store import RefundStore
from reference_agents.refund.tools import schemas


class RefundAdapter:
    kind = "reference_refund"

    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def store(self, scope, owner):
        validate_test_scope(scope)
        with (
            self.engine.connect() if hasattr(self.engine, "connect") else nullcontext(self.engine)
        ) as c:
            server, database = c.execute(text("SELECT @@server_uuid,DATABASE()")).one()
        binding = canonical_hash([server, database, owner])
        # The managed worker mounts this host directory as its .local directory.
        root = PROJECT_ROOT / (".local/workers/main/data" if os.name == "nt" else ".local")
        path = root / "refund-ledgers" / binding / (canonical_hash(scope) + ".sqlite3")
        return RefundStore(path, owner)

    def prepare(self, scope, owner):
        self.store(scope, owner).prepare()

    def snapshot(self, scope, owner):
        return self.store(scope, owner).snapshot()

    def manifest(self):
        from app.adapters.refund_rules import evaluator_manifest
        from app.agents.registry import ToolRegistry
        from app.agents.retrieval import KnowledgeRetriever
        from app.agents.versions import build_manifest
        from app.services.booking import BookingService

        s = self.settings
        # Common provider/dependency/source inventory, then replace domain-specific parts.
        retriever = KnowledgeRetriever(self.engine)
        value = build_manifest(s, retriever, ToolRegistry(BookingService(self.engine), retriever))
        value.pop("config_hash")
        for p in sorted((PROJECT_ROOT / "backend/reference_agents").rglob("*")):
            if p.is_file() and p.suffix in {".py", ".txt"}:
                value["sources"][p.relative_to(PROJECT_ROOT).as_posix()] = hashlib.sha256(
                    p.read_text(encoding="utf-8").encode()
                ).hexdigest()
        value.update(
            adapter_type=self.kind,
            baseline="reference",
            execution_policy="single_tool_call",
            prompt_hash=value["sources"]["backend/reference_agents/refund/prompt.txt"],
            tools=schemas(),
            retrieval={"mode": "not_applicable", "documents": []},
            provenance={
                "author_relation": "same_project_developer",
                "independent_external_validation": False,
                "implementation": "project_reference",
                "license": "project reference; no external license claim",
            },
            capabilities={
                "tools": True,
                "business_facts": "isolated_sqlite_simulated_refund",
                "retrieval": False,
                "nodes": "observed_model_and_tools_only",
                "crash_auto_replay": False,
                "payment_network": False,
            },
            context={
                "mode": "full_history_within_execution",
                "compaction": False,
                "cross_execution_memory": False,
                "input_bytes_per_request_max": 65536,
                "overflow": "reject before provider call; no booking context summarization",
            },
            evaluator=evaluator_manifest(),
            sdk_versions={**value["sdk_versions"], "pydantic": version("pydantic")},
        )
        value["config_hash"] = canonical_hash(value)
        return value

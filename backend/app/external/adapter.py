"""Manifest and non-business snapshots for a registered external dialogue target."""

import hashlib

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.external.registry import entry_for
from app.external.schema import PROTOCOL


class ExternalAdapter:
    def __init__(self, engine, settings, kind, *, entry=None):
        self.engine, self.settings, self.kind = engine, settings, kind
        self.entry = entry if entry is not None else entry_for(settings, kind)

    def manifest(self):
        from app.external.rules import evaluator_manifest

        paths = sorted((PROJECT_ROOT / "backend/app").rglob("*.py"))
        paths += sorted((PROJECT_ROOT / "backend/app").rglob("*.json"))
        paths += sorted((PROJECT_ROOT / "backend/app").rglob("*.txt"))
        paths += [PROJECT_ROOT / "backend/uv.lock"]
        sources = {
            p.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(
                p.read_text(encoding="utf-8").encode()
            ).hexdigest()
            for p in paths
        }
        card = self.entry.card.model_dump()
        value = {
            "adapter_type": self.kind,
            "baseline": "external-dialogue-v1",
            "execution_policy": "one_remote_message_no_automatic_retry",
            "provider": "external_service",
            "model_requested": card["name"],
            "prompt_hash": canonical_hash(card),
            "sources": sources,
            "retrieval": {"mode": "not_applicable", "documents": []},
            "tools": [],
            "capabilities": {
                "retrieval": False,
                "business_facts": False,
                "domain_coverage": False,
                "ai_judge": False,
                "dialogue": True,
                "observability": "external_reported",
            },
            "provenance": {
                "author_relation": self.entry.author_relation,
                "independent_external_validation": False,
            },
            "external": {
                "id": self.entry.id,
                "protocol": PROTOCOL,
                "card": card,
                "entry_hash": canonical_hash(self.entry.model_dump()),
                "billing": card["billing"],
                "cost_upper_microusd": self.entry.cost_upper_microusd,
                "token_upper": self.entry.token_upper,
            },
            "context": {"mode": "remote_session", "cross_execution_memory": False},
            "evaluator": evaluator_manifest(),
            "validation_boundary": "reply literal checks only; no business/semantic accuracy claim",
        }
        value["config_hash"] = canonical_hash(value)
        return value

    def prepare(self, scope, owner):
        # Remote preparation belongs in the leased runner, never in a DB claim transaction.
        return None

    def snapshot(self, scope, owner):
        return {
            "status": "not_applicable",
            "origin": "platform_observed",
            "reason": "external_dialogue_has_no_business_fact_adapter",
        }

"""Manifest and non-business snapshots for a registered external dialogue target."""

import hashlib

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.external.registry import entry_for
from app.external.schema import HttpEntry


class ExternalAdapter:
    def __init__(self, engine, settings, kind, *, entry=None, owner=None):
        self.engine, self.settings, self.kind = engine, settings, kind
        self.entry = entry if entry is not None else entry_for(settings, kind, owner=owner)

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
        mapped = isinstance(self.entry, HttpEntry)
        value = {
            "adapter_type": self.kind,
            "baseline": "external-reliability-v1" if mapped else "external-dialogue-v1",
            "execution_policy": "one_remote_message_no_automatic_retry",
            "provider": "external_service",
            "model_requested": card["name"],
            "prompt_hash": canonical_hash(card),
            "sources": sources,
            "retrieval": {"mode": "external_evidence" if mapped else "not_applicable",
                          "documents": []},
            "tools": [],
            "capabilities": {
                "retrieval": mapped,
                "tools": mapped,
                "business_facts": mapped and bool(self.entry.http.readback_path),
                "http_faults": mapped,
                "tool_faults": mapped and self.entry.http.sandbox_tool_fault,
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
                "protocol": self.entry.card.protocol,
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
        if mapped:
            value["context"]["mode"] = "http_field_mapping"
            value["validation_boundary"] = (
                "Observed reply, retrieval IDs, OTLP trace and request-bound read-only facts"
            )
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

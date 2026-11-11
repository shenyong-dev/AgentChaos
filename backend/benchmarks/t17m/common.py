import hashlib

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.evaluation.engine import evaluator_manifest

AUTHORIZATION = "T17M-20261107-agent-1"
LEDGER = f"ac:t17m:budget:{AUTHORIZATION}:agent"
PROPOSAL = PROJECT_ROOT / "docs/fixtures/T17M/semantic-proposal.json"


def binding():
    sources = list((PROJECT_ROOT / "backend/benchmarks/t17m").glob("*.py"))
    sources += list((PROJECT_ROOT / "backend/benchmarks/t17l").glob("*.py"))
    sources += list((PROJECT_ROOT / "backend/app").rglob("*.py"))
    sources += list((PROJECT_ROOT / "backend/app").rglob("*.json"))
    sources += list((PROJECT_ROOT / "backend/alembic/versions").glob("*.py"))
    sources += [PROJECT_ROOT / "backend/app/agents/prompt.txt", PROJECT_ROOT / "backend/uv.lock"]
    hashes = {
        str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(sources)
    }
    return {
        "schema": "t17m-harness-v1",
        "source_hash": canonical_hash(hashes),
        "proposal_sha256": hashlib.sha256(PROPOSAL.read_bytes()).hexdigest(),
        "evaluator": evaluator_manifest(),
        "sources": hashes,
    }

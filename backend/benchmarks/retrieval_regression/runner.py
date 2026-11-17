"""Exercise unchanged KnowledgeRetriever using a single SQLite in-memory fixture table.

No platform DB, Redis, Agent runtime, gateway or model calls. Gold is declared in the
fixture before the first scored run, not derived from the observed ranking.
"""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine

from app.agents.retrieval import KnowledgeRetriever
from app.core.config import PROJECT_ROOT

PROTOCOL = PROJECT_ROOT / "docs/fixtures/P05/protocol-r1.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_protocol(path=PROTOCOL):
    protocol = json.loads(path.read_text(encoding="utf-8"))
    for name, expected in protocol["sources"].items():
        if sha(PROJECT_ROOT / name) != expected:
            raise ValueError(f"Frozen source changed: {name}")
    fixture = PROJECT_ROOT / protocol["fixture"]
    if sha(fixture) != protocol["fixture_sha256"]:
        raise ValueError("Frozen fixture changed")
    return protocol, json.loads(fixture.read_text(encoding="utf-8"))


def fixture_engine(documents):
    # Physical SQLite types only for this table; production ORM reading, chunking,
    # validity filtering, jieba and BM25 are all the unchanged application code.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    table = Table(
        "ac_knowledge_documents",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("created_at", DateTime),
        Column("document_key", String),
        Column("revision", Integer),
        Column("title", String),
        Column("source_type", String),
        Column("source_ref", String),
        Column("valid_from", DateTime),
        Column("valid_to", DateTime),
        Column("applicability_json", JSON),
        Column("content", Text),
        Column("content_hash", String),
    )
    metadata.create_all(engine)
    rows = []
    for document in documents:
        content = f"## {document['heading']}\n- {document['clause']}\n"
        rows.append(
            {
                "id": document["id"],
                "created_at": datetime(2026, 11, 15),
                "document_key": document["key"],
                "revision": document["revision"],
                "title": document["heading"],
                "source_type": "offline_mechanical_fixture",
                "source_ref": f"P05-fixture:document:{document['id']}",
                "valid_from": datetime.fromisoformat(document["from"]).replace(tzinfo=None),
                "valid_to": datetime.fromisoformat(document["to"]).replace(tzinfo=None)
                if document["to"]
                else None,
                "applicability_json": {"scenario": document["scenario"]},
                "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    with engine.begin() as connection:
        connection.execute(table.insert(), rows)
    return engine


def gold_chunks(case, documents):
    # The declared expectation identifies whole one-clause documents. Chunk IDs
    # follow the documented heading-plus-bullet format, independently of scores.
    chunks = []
    for identifier in case["gold"]:
        document = documents[identifier]
        text = document["heading"] + "\n" + document["clause"]
        chunks.append(
            f"{document['key']}:r{document['revision']}:"
            + hashlib.sha256(text.encode()).hexdigest()[:16]
        )
    return chunks


def assess(case, observed, expected):
    returned = [chunk["chunk_id"] for chunk in observed["chunks"]]
    document_ids = {chunk["document_id"] for chunk in observed["chunks"]}
    gold = set(expected)
    hits = len(gold & set(returned))
    issues = []
    if observed["status"] not in case["statuses"]:
        issues.append("unexpected_status")
    if gold and hits != len(gold):
        issues.append("expected_chunk_missing")
    if not gold and returned:
        issues.append("unexpected_retrieval")
    if case.get("top1_required") and (not returned or returned[0] not in gold):
        issues.append("top1_mismatch")
    if document_ids & set(case.get("forbidden", [])):
        issues.append("inapplicable_document_returned")
    return {
        "passed": not issues,
        "issues": issues,
        "gold_chunks": expected,
        "gold_hits": hits,
        "gold_count": len(gold),
        "recall_at_3": hits / len(gold) if gold else None,
        "extra_chunks": len(set(returned) - gold),
        "top1_matches_gold": returned[0] in gold if gold and returned else None,
    }


def execute(fixture):
    engine = fixture_engine(fixture["documents"])
    documents = {document["id"]: document for document in fixture["documents"]}
    retrievers, rows = {}, []
    try:
        for case in fixture["cases"]:
            ids = tuple(sorted(case["documents"]))
            if ids not in retrievers:
                retrievers[ids] = KnowledgeRetriever(engine, list(ids))
            retriever = retrievers[ids]
            clock = datetime.fromisoformat(case.get("clock", fixture["reference_clock"]))
            observed = retriever.retrieve(case["query"], clock)
            assessment = assess(case, observed, gold_chunks(case, documents))
            rows.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "query": case["query"],
                    "reference_clock": clock.isoformat(),
                    "expected_statuses": case["statuses"],
                    "observed": observed,
                    "assessment": assessment,
                }
            )
        manifests = [retriever.manifest for retriever in retrievers.values()]
    finally:
        engine.dispose()
    positive = [row for row in rows if row["assessment"]["gold_count"]]
    no_answer = [row for row in rows if row["category"] == "无答案"]
    categories = {}
    for row in rows:
        group = categories.setdefault(row["category"], {"passed": 0, "total": 0})
        group["total"] += 1
        group["passed"] += row["assessment"]["passed"]
    return {
        "schema": "offline-retrieval-result-v1",
        "boundary": fixture["boundary"],
        "storage": "SQLite in-memory fixture table; not MySQL integration",
        "model_calls": 0,
        "answer_quality": "not_evaluated",
        "metrics": {
            "passed": sum(row["assessment"]["passed"] for row in rows),
            "total": len(rows),
            "positive_queries": len(positive),
            "macro_recall_at_3": sum(row["assessment"]["recall_at_3"] for row in positive)
            / len(positive)
            if positive
            else None,
            "no_answer_wrong_retrieval": sum(bool(row["observed"]["chunks"]) for row in no_answer),
            "no_answer_queries": len(no_answer),
        },
        "categories": categories,
        "rows": rows,
        "index_manifests": manifests,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    args = parser.parse_args()
    protocol, fixture = load_protocol(args.protocol)
    args.output.mkdir(parents=True, exist_ok=False)
    # Preserve protocol before any scored retrieval; outputs are never overwritten.
    (args.output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = execute(fixture)
    result["protocol_sha256"] = sha(args.protocol)
    result["runner_sha256"] = sha(Path(__file__))
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["metrics"]))


if __name__ == "__main__":
    main()

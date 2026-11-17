"""Free harness controls and unchanged real retriever; not human/model effect tests."""

import json
from datetime import datetime

import pytest

from app.agents.retrieval import KnowledgeRetriever
from benchmarks.retrieval_regression.runner import (
    PROTOCOL,
    assess,
    fixture_engine,
    gold_chunks,
    load_protocol,
)


def test_frozen_input_or_source_drift_is_rejected_before_scoring(tmp_path):
    protocol, fixture = load_protocol()
    assert len(fixture["cases"]) == 18
    assert len({case["id"] for case in fixture["cases"]}) == 18
    for field in ("fixture_sha256", "sources"):
        changed = json.loads(json.dumps(protocol))
        if field == "sources":
            changed[field]["backend/app/agents/retrieval.py"] = "0" * 64
        else:
            changed[field] = "0" * 64
        path = tmp_path / f"{field}.json"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="Frozen"):
            load_protocol(path)
    assert PROTOCOL.is_file()


def test_real_retriever_respects_validity_boundaries_in_an_in_memory_fixture():
    _, fixture = load_protocol()
    documents = [item for item in fixture["documents"] if item["id"] in {1, 5, 6}]
    engine = fixture_engine(documents)
    try:
        retriever = KnowledgeRetriever(engine, [1, 5, 6])
        for clock, expected in [
            ("2026-06-30T23:59:59+00:00", 5),
            ("2026-07-01T00:00:00+00:00", 1),
            ("2027-01-01T00:00:00+00:00", 6),
        ]:
            result = retriever.retrieve("Z201容量", datetime.fromisoformat(clock))
            assert result["status"] == "supported"
            assert {chunk["document_id"] for chunk in result["chunks"]} == {expected}
    finally:
        engine.dispose()


def test_conflicting_applicable_revisions_return_no_selected_answer():
    _, fixture = load_protocol()
    engine = fixture_engine([doc for doc in fixture["documents"] if doc["id"] in {1, 7}])
    try:
        result = KnowledgeRetriever(engine, [1, 7]).retrieve(
            "容量", datetime.fromisoformat(fixture["reference_clock"])
        )
        assert result["status"] == "evidence_conflict"
        assert result["chunks"] == []
    finally:
        engine.dispose()


def test_assessment_rejects_wrong_status_missing_gold_and_forbidden_evidence():
    case = {"gold": [1], "statuses": ["supported"], "forbidden": [2], "top1_required": True}
    observed = {"status": "no_lexical_match", "chunks": [{"chunk_id": "wrong", "document_id": 2}]}
    value = assess(case, observed, ["gold"])
    assert not value["passed"]
    assert set(value["issues"]) == {
        "unexpected_status",
        "expected_chunk_missing",
        "top1_mismatch",
        "inapplicable_document_returned",
    }
    assert value["recall_at_3"] == 0


def test_no_answer_wrong_retrieval_cannot_pass_and_extras_are_not_hidden():
    observed = {
        "status": "supported",
        "chunks": [{"chunk_id": "gold", "document_id": 1}, {"chunk_id": "extra", "document_id": 2}],
    }
    positive = assess({"statuses": ["supported"]}, observed, ["gold"])
    assert positive["recall_at_3"] == 1
    assert positive["extra_chunks"] == 1
    negative = assess({"statuses": ["no_lexical_match"]}, observed, [])
    assert not negative["passed"]
    assert "unexpected_retrieval" in negative["issues"]


def test_gold_is_declared_before_scores_and_never_selected_from_returned_chunks():
    _, fixture = load_protocol()
    documents = {doc["id"]: doc for doc in fixture["documents"]}
    expected = gold_chunks(fixture["cases"][0], documents)
    assert expected[0].startswith("p05-z201-capacity:r2:")
    result = assess(fixture["cases"][0], {"status": "supported", "chunks": []}, expected)
    assert result["gold_count"] == 1
    assert result["gold_hits"] == 0
    assert not result["passed"]

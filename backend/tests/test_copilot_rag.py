"""Mechanical vector/provider boundaries; synthetic vectors are not semantic scores."""

import copy
import hashlib
import json
import time
from uuid import uuid4

import pytest

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.copilot.embedding import PROFILE, EmbeddingClient, normalized
from app.copilot.rag import RECIPE, RagIndex
from app.guidance.service import catalog


def vector(x=1, y=0):
    return [x, y] + [0] * (PROFILE["dimension"] - 2)


def test_embedding_has_one_durable_attempt_and_validated_replay(tmp_path):
    calls = []

    def transport(payload, timeout):
        calls.append(payload)
        assert payload["parameters"]["text_type"] == "query"
        return {
            "request_id": "synthetic",
            "usage": {"total_tokens": 4},
            "output": {"embeddings": [{"text_index": 0, "embedding": vector()}]},
        }

    client = EmbeddingClient("unused", tmp_path, transport=transport)
    identifier = uuid4()
    first = client.embed(["机械请求"], "query", identifier, time.monotonic() + 10)
    second = client.embed(["机械请求"], "query", identifier, time.monotonic() + 10)
    assert first == second and len(calls) == 1
    with pytest.raises(AgentError):
        client.embed(["换一个问题"], "query", identifier, time.monotonic() + 10)
    assert len(calls) == 1
    assert "机械请求" not in (tmp_path / str(identifier) / "intent.json").read_text()


def test_embedding_unknown_never_reissues_original_identifier(tmp_path):
    calls = []

    def timeout(*args):
        calls.append(1)
        raise TimeoutError()

    client = EmbeddingClient("unused", tmp_path, transport=timeout)
    identifier = uuid4()
    for _ in range(2):
        with pytest.raises(AgentError):
            client.embed(["机械请求"], "query", identifier, time.monotonic() + 10)
    assert len(calls) == 1
    assert (
        json.loads((tmp_path / str(identifier) / "error.json").read_text())["status"] == "unknown"
    )


@pytest.mark.parametrize("value", [[0] * 1024, [1], [True] * 1024, [float("nan")] * 1024])
def test_invalid_vectors_rejected(value):
    with pytest.raises(ValueError):
        normalized(value, 1024)


def make_bundle():
    content = {"title": "机械资料", "text": "仅供管线验证。"}
    return {
        "profile": PROFILE,
        "catalog_hash": catalog()[1],
        "recipe_hash": hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
        "revision": "test",
        "rows": [
            {
                "id": "private",
                "kind": "knowledge",
                "roles": ["reviewer"],
                "content": content,
                "content_hash": canonical_hash(content),
                "vector": vector(),
            },
            {
                "id": "public",
                "kind": "knowledge",
                "roles": ["operator"],
                "content": content,
                "content_hash": canonical_hash(content),
                "vector": vector(0.8, 0.2),
            },
        ],
    }


def test_role_filters_before_vector_scoring_and_no_match_remains_empty():
    index = RagIndex(make_bundle())
    docs, _ = index.search(vector(), "operator")
    assert [r["id"] for _, r in docs] == ["public"]
    assert index.search(vector(-1), "operator") == ([], [])
    assert index.search(vector(), "nonexistent") == ([], [])


@pytest.mark.parametrize("field", ["recipe_hash", "catalog_hash", "profile"])
def test_incompatible_index_rejected(field):
    bundle = make_bundle()
    bundle[field] = "changed"
    with pytest.raises(ValueError):
        RagIndex(bundle)


def test_content_tampering_rejected():
    bundle = copy.deepcopy(make_bundle())
    bundle["rows"][0]["content"]["text"] = "changed"
    with pytest.raises(ValueError):
        RagIndex(bundle)

"""Explicit build of the shipped help recipe; never scan private docs or live records."""

import argparse
import hashlib
import json
import time
from uuid import uuid4

from app.agents.versions import canonical_hash
from app.copilot.embedding import PROFILE, EmbeddingClient, private_path, write_json
from app.copilot.rag import RECIPE, RagIndex
from app.guidance.service import catalog


def build(destination, credentials_file):
    destination = private_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    recipe = json.loads(RECIPE.read_bytes())
    if recipe["catalog_hash"] != catalog()[1]:
        raise ValueError("Review help recipe against current catalog first")
    client = EmbeddingClient(credentials_file, destination / "calls")
    rows = recipe["rows"]
    vectors = []
    for start in range(0, len(rows), 10):
        inputs = [
            r["content"]["title"] + "\n" + r["content"]["text"] for r in rows[start : start + 10]
        ]
        batch, _ = client.embed(inputs, "document", uuid4(), time.monotonic() + 30)
        vectors.extend(batch)
    bundle = {
        "profile": PROFILE,
        "catalog_hash": catalog()[1],
        "recipe_hash": hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
        "revision": recipe["revision"],
        "rows": [
            {**row, "content_hash": canonical_hash(row["content"]), "vector": vector}
            for row, vector in zip(rows, vectors, strict=True)
        ],
    }
    RagIndex(bundle)
    write_json(destination / "bundle.json", bundle, exclusive=True)
    raw = (destination / "bundle.json").read_bytes()
    config = {
        "authorization": "COPILOT-RAG-20261116",
        "profile": PROFILE,
        "bundle_path": str(
            (destination / "bundle.json").relative_to(private_path(".local").parent)
        ),
        "bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "credentials_file": credentials_file,
        "audit_root": ".local/copilot-rag/query-calls",
    }
    write_json(destination / "runtime-candidate.json", config, exclusive=True)
    return {
        "rows": len(rows),
        "bundle_sha256": config["bundle_sha256"],
        "candidate": str(destination / "runtime-candidate.json"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True)
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--allow-provider", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.destination, args.credentials_file)))

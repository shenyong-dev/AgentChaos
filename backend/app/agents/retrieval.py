"""Pinned experimental rules: deterministic chunks/jieba/BM25, no embeddings."""

import hashlib
import json
import logging
import math
import re
from datetime import datetime
from importlib.metadata import version

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.core.config import PROJECT_ROOT
from app.models import KnowledgeDocument


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class KnowledgeRetriever:
    def __init__(self, engine, document_ids: list[int] | None = None):
        self.engine = engine
        terms_path = PROJECT_ROOT / "knowledge/retrieval-terms.json"
        dictionary_path = PROJECT_ROOT / "knowledge/booking-userdict.txt"
        self.terms = json.loads(terms_path.read_text(encoding="utf-8"))
        self.stop = set(self.terms["stopwords"])
        jieba.setLogLevel(logging.ERROR)
        self.tokenizer = jieba.Tokenizer()
        cache = PROJECT_ROOT / ".cache/jieba"
        cache.mkdir(parents=True, exist_ok=True)
        self.tokenizer.tmp_dir = str(cache)
        self.tokenizer.cache_file = "booking-jieba-0421.cache"
        self.tokenizer.initialize()
        self.tokenizer.load_userdict(str(dictionary_path))
        with Session(engine) as session:
            query = select(KnowledgeDocument).order_by(
                KnowledgeDocument.document_key, KnowledgeDocument.revision
            )
            if document_ids is None:
                query = query.where(
                    KnowledgeDocument.document_key == "booking-rules",
                    KnowledgeDocument.revision == 1,
                )
            else:
                query = query.where(KnowledgeDocument.id.in_(document_ids))
            self.documents = [
                {
                    "id": row.id,
                    "document_key": row.document_key,
                    "revision": row.revision,
                    "title": row.title,
                    "source_type": row.source_type,
                    "source_ref": row.source_ref,
                    "valid_from": row.valid_from.isoformat() if row.valid_from else None,
                    "valid_to": row.valid_to.isoformat() if row.valid_to else None,
                    "applicability": row.applicability_json,
                    "content": row.content,
                    "content_hash": row.content_hash,
                }
                for row in session.scalars(query)
            ]
        if document_ids is not None and {d["id"] for d in self.documents} != set(document_ids):
            raise AgentError("knowledge_not_ready", "冻结规则修订不完整，拒绝替换为其他版本。", 503)
        if not self.documents:
            raise AgentError("knowledge_not_ready", "未找到冻结的实验规则修订。", 503)
        self.chunks = []
        for document in self.documents:
            if digest(document["content"]) != document["content_hash"]:
                raise AgentError("knowledge_hash_mismatch", "规则内容与修订哈希不符。", 503)
            heading = ""
            for line in document["content"].splitlines():
                if line.startswith("## "):
                    heading = line[3:]
                elif line.startswith("- "):
                    text = heading + "\n" + line[2:]
                    self.chunks.append(
                        {
                            "chunk_id": f"{document['document_key']}:r{document['revision']}:"
                            + digest(text)[:16],
                            "text": text,
                            "document_id": document["id"],
                            "source_ref": document["source_ref"],
                            "document_revision": document["revision"],
                            "document_hash": document["content_hash"],
                            "chunk_hash": digest(text),
                        }
                    )
        if not self.chunks:
            raise AgentError("knowledge_not_ready", "规则修订没有可检索条款。", 503)
        corpus = [self.tokens(chunk["text"]) for chunk in self.chunks]
        self.corpus = corpus
        self.bm25 = BM25Okapi(corpus, k1=1.5, b=0.75, epsilon=0.25)
        # Okapi's epsilon * average_idf can remain negative in a tiny frozen KB.
        # Common terms must still retrieve a sole applicable clause.
        for token, value in self.bm25.idf.items():
            if value <= 0:
                frequency = sum(token in words for words in corpus)
                self.bm25.idf[token] = math.log1p(
                    (len(corpus) - frequency + 0.5) / (frequency + 0.5)
                )
        base_dictionary = self.tokenizer.get_dict_file()
        try:
            base_hash = hashlib.sha256(base_dictionary.read()).hexdigest()
        finally:
            base_dictionary.close()
        self.manifest = {
            "version": "rules-bm25-v2",
            "nonpositive_idf": "log1p((N-df+0.5)/(df+0.5)); positive IDF unchanged",
            "jieba": version("jieba"),
            "rank_bm25": version("rank-bm25"),
            "k1": 1.5,
            "b": 0.75,
            "epsilon": 0.25,
            "top_k": 3,
            "minimum_score": 0.01,
            "chunking": "heading-plus-bullet-v1",
            "dictionary_sha256": base_hash,
            "user_dictionary_sha256": digest(dictionary_path.read_text(encoding="utf-8")),
            "terms_sha256": digest(terms_path.read_text(encoding="utf-8")),
            "documents": [
                {key: value for key, value in document.items() if key != "content"}
                for document in self.documents
            ],
            "chunks": [
                {key: value for key, value in chunk.items() if key != "text"}
                for chunk in self.chunks
            ],
        }
        self.manifest["index_hash"] = digest(
            json.dumps(self.manifest, sort_keys=True, ensure_ascii=False)
        )

    def tokens(self, text):
        text = re.sub(r"(\d)\s+(天|小时|分钟)", r"\1\2", text)
        tokens = [
            token.strip().lower()
            for token in self.tokenizer.cut(text, HMM=False)
            if token.strip() and re.search(r"[\w\u4e00-\u9fff]", token) and token not in self.stop
        ]
        aliases = self.terms["aliases"]
        return tokens + [aliases[token] for token in tokens if token in aliases]

    def retrieve(self, query: str, reference_clock: datetime):
        tokens = self.tokens(query)
        applicable = []
        for doc in self.documents:
            if doc["applicability"].get("scenario") != "experimental_booking":
                continue
            if doc["valid_from"] and reference_clock < datetime.fromisoformat(doc["valid_from"]):
                continue
            if doc["valid_to"] and reference_clock >= datetime.fromisoformat(doc["valid_to"]):
                continue
            applicable.append(doc)
        by_key = {}
        for doc in applicable:
            by_key.setdefault(doc["document_key"], set()).add(doc["content_hash"])
        if any(len(hashes) > 1 for hashes in by_key.values()):
            return {
                "status": "evidence_conflict",
                "chunks": [],
                "query": query,
                "index_hash": self.manifest["index_hash"],
            }
        ids = {doc["id"] for doc in applicable}
        if not ids:
            return {
                "status": "no_applicable_evidence",
                "query": query,
                "query_tokens": tokens,
                "chunks": [],
                "index_hash": self.manifest["index_hash"],
            }
        scores = self.bm25.get_scores(tokens) if tokens else [0] * len(self.chunks)
        matches = [
            (float(scores[i]), chunk)
            for i, chunk in enumerate(self.chunks)
            if chunk["document_id"] in ids
            and set(tokens) & set(self.corpus[i])
            and scores[i] >= 0.01
        ]
        matches.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        # Generic task words alone cannot support a specific answer such as a fee.
        meaningful = {
            token
            for token in tokens
            if token not in {"实验", "相关", "问题", "需要"}
            and not re.fullmatch(r"[a-z]\d{3}|\d+", token)
        }
        weak_match = bool(matches)
        if not meaningful or not any(
            meaningful & set(words)
            for chunk, words in zip(self.chunks, self.corpus, strict=True)
            if chunk["document_id"] in ids
        ):
            matches = []
        return {
            "status": "supported"
            if matches
            else ("low_relevance" if weak_match else "no_lexical_match"),
            "query": query,
            "query_tokens": tokens,
            "index_hash": self.manifest["index_hash"],
            "chunks": [{**chunk, "score": round(score, 6)} for score, chunk in matches[:3]],
        }

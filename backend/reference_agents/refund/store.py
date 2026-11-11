"""Integer-fen transactions and request-key idempotency, independent of booking tables."""

import hashlib
import json
import sqlite3
from contextlib import closing
from uuid import uuid4


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class RefundStore:
    def __init__(self, path, owner):
        self.path, self.owner = path, owner

    def connect(self):
        if not self.path.is_file():
            raise RuntimeError("refund_ledger_missing")
        c = sqlite3.connect(f"file:{self.path.as_posix()}?mode=rw", uri=True, timeout=15)
        c.row_factory = sqlite3.Row
        if c.execute("SELECT owner FROM binding").fetchone()[0] != self.owner:
            c.close()
            raise RuntimeError("refund_ledger_owner_mismatch")
        return c

    def prepare(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with closing(self.connect()):
                return
        # Exclusive creation; never replace an existing ledger/checkpoint on recovery.
        self.path.touch(exist_ok=False)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript("""
                CREATE TABLE binding(owner INTEGER NOT NULL);
                CREATE TABLE orders(id TEXT PRIMARY KEY, owner INTEGER NOT NULL,
                    amount_fen INTEGER NOT NULL CHECK(amount_fen>0), refundable INTEGER NOT NULL);
                CREATE TABLE drafts(id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                    amount_fen INTEGER NOT NULL CHECK(amount_fen>0), hash TEXT NOT NULL,
                    request_key TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL,
                    active INTEGER NOT NULL);
                CREATE TABLE refunds(id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE,
                    amount_fen INTEGER NOT NULL CHECK(amount_fen>0),
                    request_key TEXT NOT NULL UNIQUE,
                    owner INTEGER NOT NULL, status TEXT NOT NULL CHECK(status='posted'));
            """)
            c.execute("INSERT INTO binding VALUES(?)", (self.owner,))
            c.executemany(
                "INSERT INTO orders VALUES(?,?,?,?)",
                [
                    ("O-1001", self.owner, 12000, 1),
                    ("O-1002", self.owner, 8000, 0),
                    ("O-2001", self.owner + 1000000, 45000, 1),
                ],
            )

    def snapshot(self):
        if not self.path.exists():
            return None
        with closing(self.connect()) as c:
            return [dict(r) for r in c.execute("SELECT * FROM refunds ORDER BY id")]

    def order(self, order_id):
        with closing(self.connect()) as c:
            row = c.execute(
                "SELECT * FROM orders WHERE id=? AND owner=?", (order_id, self.owner)
            ).fetchone()
            return dict(row) if row else None

    def preview(self, order_id, revision):
        with closing(self.connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM orders WHERE id=? AND owner=?", (order_id, self.owner)
            ).fetchone()
            c.execute("UPDATE drafts SET active=0")
            if not row or not row["refundable"]:
                return None
            identifier, key = str(uuid4()), str(uuid4())
            body = {
                "draft_id": identifier,
                "order_id": order_id,
                "amount_fen": row["amount_fen"],
                "revision": revision,
                "request_key": key,
                "operation": "simulated_refund",
            }
            body["draft_hash"] = digest(body)
            c.execute(
                "INSERT INTO drafts VALUES(?,?,?,?,?,?,1)",
                (identifier, order_id, row["amount_fen"], body["draft_hash"], key, revision),
            )
            return body

    def invalidate(self):
        with closing(self.connect()) as c, c:
            c.execute("UPDATE drafts SET active=0")

    def confirm(self, draft_id, draft_hash, request_key, grant):
        with closing(self.connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT * FROM refunds WHERE request_key=? AND owner=?", (request_key, self.owner)
            ).fetchone()
            if previous:
                # An idempotent receipt must still be bound to its original draft.
                d = c.execute(
                    "SELECT * FROM drafts WHERE id=? AND hash=? AND request_key=?",
                    (draft_id, draft_hash, request_key),
                ).fetchone()
                if not d or d["order_id"] != previous["order_id"]:
                    return None
                return dict(previous)
            d = c.execute(
                "SELECT * FROM drafts WHERE id=? AND hash=? AND request_key=? AND active=1",
                (draft_id, draft_hash, request_key),
            ).fetchone()
            if (
                not d
                or not grant
                or grant
                != {"draft_id": draft_id, "draft_hash": draft_hash, "revision": d["revision"]}
            ):
                return None
            o = c.execute(
                "SELECT * FROM orders WHERE id=? AND owner=?", (d["order_id"], self.owner)
            ).fetchone()
            if not o or not o["refundable"] or o["amount_fen"] != d["amount_fen"]:
                return None
            existing = c.execute(
                "SELECT * FROM refunds WHERE order_id=?", (d["order_id"],)
            ).fetchone()
            if existing:
                return None
            result = {
                "id": str(uuid4()),
                "order_id": d["order_id"],
                "amount_fen": d["amount_fen"],
                "request_key": request_key,
                "owner": self.owner,
                "status": "posted",
            }
            c.execute(
                "INSERT INTO refunds VALUES(:id,:order_id,:amount_fen,:request_key,:owner,:status)",
                result,
            )
            c.execute("UPDATE drafts SET active=0 WHERE id=?", (draft_id,))
            return result

    def lookup(self, key):
        with closing(self.connect()) as c:
            row = c.execute(
                "SELECT * FROM refunds WHERE request_key=? AND owner=?", (key, self.owner)
            ).fetchone()
            return dict(row) if row else None

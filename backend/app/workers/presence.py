"""Short-lived dispatcher observations; no durable work or budget state here."""

import json
import threading
from datetime import UTC, datetime
from uuid import uuid4

from redis import Redis

from app.core.deployment import database_binding, execution_fingerprint, source_fingerprint


class DispatcherPresence:
    def __init__(self, settings):
        self.client = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        self.key = settings.executor_namespace + ":dispatcher:presence:" + uuid4().hex
        self.record = {
            "protocol": "T17H-1",
            "source_hash": source_fingerprint(),
            "database_binding": database_binding(settings),
            "execution_hash": execution_fingerprint(settings),
            "queue": settings.executor_queue,
            "last_scan": None,
            "scan_ok": False,
        }
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="dispatcher-presence")

    def loop(self):
        while not self.stop.is_set():
            try:
                value = {**self.record, "observed_at": datetime.now(UTC).isoformat()}
                self.client.set(self.key, json.dumps(value), ex=12)
            except Exception:
                pass  # A failed heartbeat must expire, never manufacture readiness.
            self.stop.wait(3)

    def scanned(self, success):
        self.record = {
            **self.record,
            "last_scan": datetime.now(UTC).isoformat(),
            "scan_ok": success,
        }

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=4)
        try:
            self.client.delete(self.key)
        finally:
            self.client.close()

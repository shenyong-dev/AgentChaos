"""Read-only Celery liveness, scoped to this container and cloud queue namespace."""

import os
import sys
from urllib.parse import quote

from celery import Celery


def main():
    password = quote(os.environ["REDIS_PASSWORD"], safe="")
    host = os.environ["REDIS_HOST"]
    port = int(os.environ.get("REDIS_PORT", "6379"))
    database = int(os.environ.get("REDIS_DB", "0"))
    namespace = os.environ["EXECUTOR_NAMESPACE"]
    node = "celery@" + os.environ["HOSTNAME"]
    app = Celery("cloud_worker_health", broker=f"redis://:{password}@{host}:{port}/{database}")
    app.conf.update(
        broker_transport_options={
            "global_keyprefix": namespace + ":queue:",
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
        },
        broker_connection_timeout=2,
        broker_connection_retry=False,
    )
    try:
        replies = app.control.ping(destination=[node], timeout=5)
        if not any(reply.get(node, {}).get("ok") == "pong" for reply in replies):
            raise RuntimeError("No matching worker reply")
        print("This worker replied; application readiness still checks source and database.")
    finally:
        app.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- health logs must redact every broker exception
        # Broker credentials must never be included in Docker health logs.
        print(f"Worker heartbeat unavailable ({type(exc).__name__}).")
        sys.exit(1)

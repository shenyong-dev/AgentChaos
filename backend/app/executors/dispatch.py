"""MySQL outbox scanner. No message is the sole durable record of pending work."""

import argparse
import time
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.login_events import PRUNE_LIMIT, prune_login_events
from app.core.config import get_settings
from app.db.engine import create_database_engine
from app.executors.control import database_now, locked_pair
from app.executors.service import RunService
from app.models import TestExecution


def dispatch_pending(engine, settings, *, publisher=None, force=False, only_run=None):
    if publisher is None:
        from app.workers.celery_app import celery_app

        def publisher(identifier):
            celery_app.send_task(
                "agentchaos.execute", args=[identifier], queue=settings.executor_queue, retry=False
            )

    service = RunService(engine, settings)
    counts = {
        "published": 0,
        "failed": 0,
        "deferred": 0,
        "expired": service.expire_deadlines(compatible_only=True),
    }
    counts["incompatible"] = 0
    compatibility = {}
    for identifier in service.candidates():
        # Reserve one dispatch slot, commit, then publish without holding SQL locks.
        dispatch_token = str(uuid4())
        with Session(engine) as session, session.begin():
            eid = session.scalar(
                select(TestExecution.id).where(TestExecution.execution_uuid == identifier)
            )
            run, e = locked_pair(session, eid)
            now = database_now(session)
            timing = dict(e.timing_json)
            if (only_run and run.run_uuid != str(only_run)) or run.status not in (
                "queued",
                "running",
            ):
                continue
            if not service.configuration_matches(
                run.manifest_json, cache=compatibility, owner=run.created_by
            ):
                counts["incompatible"] += 1
                continue
            if e.status != "queued" and not (
                e.status == "running" and e.lease_until and e.lease_until < now
            ):
                continue
            last = timing.get("dispatch_at")
            if not force and last and now < datetime.fromisoformat(last) + timedelta(seconds=15):
                counts["deferred"] += 1
                continue
            timing.update(
                dispatch_token=dispatch_token,
                dispatch_at=now.isoformat(),
                dispatch_count=timing.get("dispatch_count", 0) + 1,
            )
            e.timing_json = timing
        error = None
        try:
            publisher(identifier)
            counts["published"] += 1
        except Exception as exc:
            counts["failed"] += 1
            error = {"code": "dispatch_error", "exception_type": type(exc).__name__}
        with Session(engine) as session, session.begin():
            _, e = locked_pair(session, eid)
            if e.timing_json.get("dispatch_token") == dispatch_token:
                e.timing_json = {
                    **e.timing_json,
                    "dispatch_error": error,
                    "dispatch_result": "failed" if error else "published",
                }
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    engine = create_database_engine(settings)
    from app.workers.presence import DispatcherPresence

    next_auth_cleanup = 0.0
    with DispatcherPresence(settings) as presence:
        while True:
            try:
                result = dispatch_pending(engine, settings)
                presence.scanned(True)
                print(result, flush=True)
            except Exception as exc:
                presence.scanned(False)
                print({"dispatch_scan_error": type(exc).__name__}, flush=True)
            if args.once:
                break
            if time.monotonic() >= next_auth_cleanup:
                delay = 3600
                try:
                    if prune_login_events(engine) == PRUNE_LIMIT:
                        delay = 10
                except Exception as exc:
                    print({"login_audit_cleanup_error": type(exc).__name__}, flush=True)
                next_auth_cleanup = time.monotonic() + delay
            time.sleep(3)


if __name__ == "__main__":
    main()

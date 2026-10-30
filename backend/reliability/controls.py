"""Real process/transport interruptions over narrowly owned T17 resources."""

import json
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy.exc import OperationalError

from app.evidence.reader import cursor
from reliability.lab import BOOK, stop_process


def one(view):
    assert len(view["executions"]) == 1
    return view["executions"][0]


def signal(lab, run, phase, extension):
    return lab.signals / f"{one(lab.service.read(1, run))['session_id']}-{phase}.{extension}"


def wait_signal(lab, run, phase):
    view, _ = lab.wait(run, lambda v: signal(lab, run, phase, "entered").exists())
    return view


def expired(lab, run):
    # Observe the actual lease/time, never fast-forward SQL timestamps.
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        view = lab.service.read(1, run)
        row = one(view)
        if row["status"] != "running":
            return view
        with lab.engine.connect() as connection:
            from sqlalchemy import text

            stale = connection.scalar(
                text(
                    "SELECT lease_until < UTC_TIMESTAMP(6) FROM ac_test_executions "
                    "WHERE execution_uuid=:id"
                ),
                {"id": row["execution_id"]},
            )
        if stale:
            return view
        time.sleep(0.2)
    raise RuntimeError("Actual SQL lease did not expire")


def brief(lab, view):
    row = one(view)
    return {
        "run_id": view["run_id"],
        "run_status": view["status"],
        "execution_id": row["execution_id"],
        "status": row["status"],
        "attempt": row["attempt"],
        "repeat_no": row["repeat_no"],
        "scope_id": row["scope_id"],
        "session_id": row["session_id"],
        "error": row["error"],
        "bookings": lab.bookings(view),
    }


def health(lab, status, component):
    response = httpx.get("http://127.0.0.1:18017/api/v1/health", timeout=12)
    assert response.status_code == status, response.status_code
    value = response.json()["data"]
    check = next(item for item in value["checks"] if item["name"] == component)
    assert check["status"] == ("ok" if status == 200 else "error")
    return {"http_status": status, "checked_at": value["checked_at"], "component": check}


def run_matrix(lab, worker):
    results = {}

    def record(name, view, expected, **extra):
        value = brief(lab, view)
        assert value["bookings"] == expected, value
        results[name] = {**value, **extra}
        lab.save(name + ".json", results[name])

    # Queued cancellation and duplicate delivery must never reach a tool.
    case = lab.case("CANCEL-QUEUED", BOOK)
    run = lab.create([case])
    lab.service.cancel(1, run)
    lab.dispatch(run, duplicate=True)
    view = lab.service.read(1, run)
    assert view["status"] == "cancelled" and one(view)["attempt"] == 1
    record("cancel-queued", view, 0)

    # Persisted confirmation + native Windows Redis SAVE/restart + one original-key write.
    case = lab.case("REDIS-CHECKPOINT", BOOK, expected=1)
    run = lab.create([case])
    lab.dispatch(run)
    before, _ = lab.wait(run, lambda v: one(v)["status"] == "waiting_input")
    sid = one(before)["session_id"]
    key = lab.settings.executor_namespace + ":session:" + sid
    original = lab.redis.get(key)
    assert original and lab.bookings(before) == 0
    lab.stop_redis()
    unavailable = health(lab, 503, "redis")
    lab.start_redis()
    assert lab.redis.get(key) == original
    healthy = health(lab, 200, "redis")
    lab.supply(run)
    view, _ = lab.wait(run)
    assert one(view)["status"] == "passed" and one(view)["session_id"] == sid
    record("native-redis-checkpoint-restart", view, 1, unavailable=unavailable, recovered=healthy)

    # Redis fails during an actual in-flight graph operation; SQL records an error.
    case = lab.case("REDIS-INFLIGHT", "T17-HOLD-BEFORE 明确离线中断控制，不预约。")
    run = lab.create([case])
    lab.dispatch(run)
    wait_signal(lab, run, "before")
    lab.stop_redis()
    unavailable = health(lab, 503, "redis")
    signal(lab, run, "before", "release").touch()
    view, _ = lab.wait(run)
    assert one(view)["status"] == "error"
    record("redis-unavailable-inflight", view, 0, health=unavailable)
    lab.start_redis()
    health(lab, 200, "redis")

    # Stop one whole Linux Worker, wait for a real lease, and reject the old return.
    lab.remove_worker(worker)
    worker_a = lab.start_worker("stale-a", 1)
    case = lab.case("STALE-WORKER", "T17-HOLD-BEFORE 明确离线旧租约控制，不预约。")
    run = lab.create([case])
    lab.dispatch(run)
    before = wait_signal(lab, run, "before")
    lab.docker("pause", worker_a)
    worker_b = lab.start_worker("recovery-b", 1)
    expired(lab, run)
    paused_lock_timeout = False
    resumed_for_lock_release = False
    try:
        lab.dispatch(run)
    except OperationalError as exc:
        if getattr(exc.orig, "args", (None,))[0] != 1205:
            raise
        # An OS-frozen Worker may retain an already-open SQL transaction.
        # A lease does not release that lock. The normal dispatcher reports a
        # scan error and rescans; this lab resumes the owned process to unwind.
        paused_lock_timeout = True
        lab.docker("unpause", worker_a)
        resumed_for_lock_release = True
        signal(lab, run, "before", "release").touch()
        time.sleep(2)
        lab.dispatch(run)
    view, _ = lab.wait(run)
    assert one(view)["attempt"] == 2 and one(view)["error"]["code"] == "recovery_uncertain"
    assert one(view)["scope_id"] == one(before)["scope_id"]
    if not resumed_for_lock_release:
        lab.docker("unpause", worker_a)
    signal(lab, run, "before", "release").touch()
    time.sleep(1)
    after = lab.service.read(1, run)
    assert one(after)["status"] == "error" and one(after)["attempt"] == 2
    record(
        "expired-worker-late-return",
        after,
        0,
        old_return_refused=True,
        paused_SQL_lock_timeout=paused_lock_timeout,
        owned_worker_resumed_to_release_SQL=resumed_for_lock_release,
    )
    lab.remove_worker(worker_a)
    lab.remove_worker(worker_b)
    worker = lab.start_worker("controls", 5)

    # Cancellation in-flight is fenced; after an actual commit the fact remains.
    case = lab.case("CANCEL-INFLIGHT", "T17-HOLD-BEFORE 明确离线取消控制，不预约。")
    run = lab.create([case])
    lab.dispatch(run)
    wait_signal(lab, run, "before")
    lab.service.cancel(1, run)
    signal(lab, run, "before", "release").touch()
    time.sleep(0.3)
    view = lab.service.read(1, run)
    assert one(view)["status"] == "cancelled"
    record("cancel-inflight", view, 0)

    case = lab.case("CANCEL-COMMITTED", BOOK + " T17-HOLD-AFTER-COMMIT", confirm=True, expected=1)
    run = lab.create([case])
    lab.dispatch(run)
    committed = wait_signal(lab, run, "after")
    assert lab.bookings(committed) == 1
    lab.service.cancel(1, run)
    signal(lab, run, "after", "release").touch()
    time.sleep(0.5)
    view = lab.service.read(1, run)
    assert one(view)["status"] == "cancelled"
    record("cancel-after-sql-commit", view, 1, observed_commit_before_cancel=True)

    # Deliberate loss of only this owned session's metadata cannot cause resubmission.
    case = lab.case("MISSING-METADATA", BOOK, expected=1)
    run = lab.create([case])
    lab.dispatch(run)
    before, _ = lab.wait(run, lambda v: one(v)["status"] == "waiting_input")
    key = lab.settings.executor_namespace + ":session:" + one(before)["session_id"]
    assert lab.redis.delete(key) == 1
    lab.supply(run)
    view, _ = lab.wait(run)
    assert one(view)["error"]["code"] == "recovery_uncertain"
    record("missing-session-metadata", view, 0, deliberate_own_key_loss=True)

    # Cut the owned MySQL proxy; the shared server/main schema is never stopped.
    case = lab.case("MYSQL-OUTAGE", "T17-HOLD-BEFORE 明确离线SQL中断控制，不预约。")
    run = lab.create([case])
    lab.dispatch(run)
    wait_signal(lab, run, "before")
    lab.proxy.pause()
    unavailable = health(lab, 503, "mysql")
    time.sleep(2)
    lab.proxy.resume()
    health(lab, 200, "mysql")
    expired(lab, run)
    lab.dispatch(run)
    view, _ = lab.wait(run)
    assert one(view)["status"] == "error"
    signal(lab, run, "before", "release").touch()
    record("owned-mysql-connection-outage", view, 0, health=unavailable)

    # Broker publication fails durably, then the production scanner republishes.
    case = lab.case("BROKER-OUTAGE", "明确离线队列不可用控制，不预约。")
    run = lab.create([case])
    lab.stop_redis()
    dispatch = lab.dispatch(run)
    before = lab.service.read(1, run)
    assert dispatch["failed"] == 1 and one(before)["status"] == "queued"
    lab.start_redis()
    lab.dispatch(run)
    view, _ = lab.wait(run)
    assert one(view)["status"] == "passed" and one(view)["attempt"] == 1
    record("broker-publish-rescan", view, 0, original_publish=dispatch)

    results["sse"] = sse_checks(lab)
    return results, worker


def sse_checks(lab):
    ids = [lab.case(f"SSE-{i}", "明确离线SSE控制，不预约。") for i in range(3)]
    run = lab.create(ids, concurrency=3)
    auth = ("demo_operator", lab.base.demo_seed_password.get_secret_value())
    current, observed, stages = cursor(run), [], []

    def consume(maximum=None, *, slow=False):
        nonlocal current
        deadline = time.monotonic() + 60
        ended = False
        while time.monotonic() < deadline and not ended:
            with (
                httpx.Client(auth=auth, timeout=12) as client,
                client.stream(
                    "GET",
                    f"http://127.0.0.1:18017/api/v1/runs/{run}/events/stream",
                    headers={"Last-Event-ID": current},
                ) as response,
            ):
                assert response.status_code == 200
                frame = {}
                for line in response.iter_lines():
                    if line:
                        key, _, value = line.partition(":")
                        frame[key] = value.lstrip()
                        continue
                    kind = frame.get("event")
                    if kind == "trace":
                        value = json.loads(frame["data"])
                        assert value["cursor"] == frame["id"]
                        observed.append(
                            {"cursor": value["cursor"], "event_id": value["event"]["event_id"]}
                        )
                        current = value["cursor"]
                        if maximum and len(observed) >= maximum:
                            return
                        if slow:
                            time.sleep(0.03)
                    elif kind == "stream.end":
                        ended = True
                        break
                    elif kind:
                        assert kind not in ("stream.error", "stream.reset"), frame
                        stages.append(kind)
                    frame = {}
        assert ended

    errors = []

    def first():
        try:
            consume(5)
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=first)
    thread.start()
    lab.dispatch(run)
    thread.join(timeout=40)
    assert not thread.is_alive() and not errors and len(observed) == 5, errors
    stop_process(lab.api)
    lab.start_api()
    consume(slow=True)
    view = lab.service.read(1, run)
    assert view["status"] == "completed"
    expected = []
    position = None
    while True:
        page = lab.reader.page(1, run, after=position)
        expected.extend(item["event"]["event_id"] for item in page["items"])
        position = page["next_cursor"]
        if not page["has_more"]:
            break
    assert [item["event_id"] for item in observed] == expected
    base = f"http://127.0.0.1:18017/api/v1/runs/{run}/events/stream"
    with httpx.Client(auth=auth, timeout=12) as client:
        assert client.get(base, headers={"Last-Event-ID": "invalid"}).status_code == 400
        assert client.get(base, headers={"Last-Event-ID": cursor(str(uuid4()))}).status_code == 409
        assert (
            client.get(base, headers={"Last-Event-ID": cursor(run, 1, str(uuid4()))}).status_code
            == 410
        )
        other = client.get(
            f"http://127.0.0.1:18017/api/v1/runs/{run}/snapshot",
            auth=("t17_other", auth[1]),
        )
        assert other.status_code == 404
    result = {
        "run_id": run,
        "api_restarted": True,
        "disconnected_after": 5,
        "observed_frames_equal_SQL": len(observed),
        "duplicates": 0,
        "invalid_cursor": 400,
        "cross_run_cursor": 409,
        "unavailable_cursor": 410,
        "foreign_operator": 404,
        "rotation_events": stages,
        "client_delay_per_trace_seconds": 0.03,
        "boundary": "Actual slow read/reconnect; send timeout is a separate ASGI control",
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    lab.save("sse-api-restart.json", result)
    return result

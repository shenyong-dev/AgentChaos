"""T17F: real MySQL users and atomic native Redis session/revocation controls."""

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.api.auth import get_session_service
from app.api.booking import get_booking_service
from app.api.cases import get_case_service
from app.api.evidence import stream
from app.api.judging import get_judge_service
from app.api.workbench import get_workbench_service
from app.auth.service import SessionService
from app.cases.service import CaseService
from app.core.config import Settings
from app.core.passwords import hash_password
from app.db.seeds import seed_database
from app.judging.service import JudgeService
from app.main import create_app
from app.models import User
from app.services.booking import BookingService
from app.workbench.service import WorkbenchService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Uses isolated MySQL and native Redis"
    ),
]
ORIGIN = "http://testserver"
HEADERS = {"Origin": ORIGIN, "X-AC-Origin": ORIGIN}


class SessionLab(SimpleNamespace):
    def __repr__(self):
        return "SessionLab(real SQL/Redis, credentials omitted)"


@pytest.fixture(scope="module")
def engine():
    with disposable_database("T17F") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        with Session(engine) as db, db.begin():
            db.add(
                User(
                    username="second_operator",
                    role="operator",
                    enabled=True,
                    password_hash=hash_password(TEST_PASSWORD),
                )
            )
        yield engine


@pytest.fixture
def lab(engine, monkeypatch):
    settings = Settings(
        mysql_database=engine.url.database, executor_namespace="test:sessions:" + uuid4().hex
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=3)
    clock = SimpleNamespace(now=time.time())
    service = SessionService(engine, redis, settings, clock=lambda: clock.now)
    monkeypatch.setattr("app.auth.service.get_session_service", lambda: service)
    app = create_app()
    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[get_booking_service] = lambda: BookingService(engine)
    app.dependency_overrides[get_case_service] = lambda: CaseService(engine, redis, settings)
    app.dependency_overrides[get_judge_service] = lambda: JudgeService(engine, redis, settings)
    app.dependency_overrides[get_workbench_service] = lambda: WorkbenchService(engine, settings)
    with TestClient(app, headers=HEADERS) as client:
        yield SessionLab(client=client, app=app, service=service, redis=redis, clock=clock)
    keys = list(redis.scan_iter(service.prefix + "*"))
    if keys:
        redis.delete(*keys)
    redis.close()


def login(lab, username="demo_operator", password=TEST_PASSWORD):
    response = lab.client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    lab.client.headers["X-CSRF-Token"] = result["csrf_token"]
    return result


def test_cookie_and_storage_contain_no_password_or_raw_session_key(lab):
    user = login(lab)
    token = lab.client.cookies.get(lab.service.cookie_name)
    assert len(token) == 43 and token not in lab.service.key(token)
    record = lab.redis.get(lab.service.key(token)).decode()
    assert TEST_PASSWORD not in record and token not in record
    assert user["role"] == "operator" and user["idle_seconds"] == 1800
    response = lab.client.post(
        "/api/v1/auth/login", json={"username": "demo_operator", "password": TEST_PASSWORD}
    )
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/v1" in cookie
    assert "Domain=" not in cookie and "Max-Age=28800" in cookie
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("username", ["demo_operator", "demo_reviewer"])
def test_same_identity_reads_generator_and_judge_without_relogin(lab, username):
    user = login(lab, username)
    for path in ("/case-generation/access", "/judging/access"):
        response = lab.client.get("/api/v1" + path)
        assert response.status_code == 200 and response.json()["data"]["role"] == user["role"]
    assert lab.client.get("/api/v1/auth/session").json()["data"]["user_id"] == user["user_id"]
    response = lab.client.get("/api/v1/workbench/overview")
    assert response.status_code == (200 if user["role"] == "operator" else 403)


def test_idle_expiry_and_activity_extend_only_until_absolute_deadline(lab):
    user = login(lab)
    for _ in range(16):
        lab.clock.now += 1700
        result = lab.client.get("/api/v1/auth/session", headers={"X-AC-Activity": "1"})
        assert result.status_code == 200
        assert result.json()["data"]["absolute_expires_at"] == user["absolute_expires_at"]
    lab.clock.now = user["absolute_expires_at"]
    assert lab.client.get("/api/v1/auth/session").status_code == 401


@pytest.mark.parametrize("activity", [None, "0"])
def test_inactive_status_queries_do_not_extend_idle_deadline(lab, activity):
    user = login(lab)
    key = lab.service.key(lab.client.cookies.get(lab.service.cookie_name))
    headers = {} if activity is None else {"X-AC-Activity": activity}
    for elapsed in (600, 1200):
        lab.clock.now = user["idle_expires_at"] - 1800 + elapsed
        response = lab.client.get("/api/v1/auth/session", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["idle_expires_at"] == user["idle_expires_at"]
        assert json.loads(lab.redis.get(key))["idle"] == user["idle_expires_at"]
    lab.clock.now = user["idle_expires_at"]
    assert lab.client.get("/api/v1/auth/session", headers=headers).status_code == 401
    assert not lab.redis.exists(key)


def test_active_status_query_renews_idle_without_moving_absolute_deadline(lab):
    user = login(lab)
    lab.clock.now += 600
    response = lab.client.get("/api/v1/auth/session", headers={"X-AC-Activity": "1"})
    assert response.status_code == 200
    current = response.json()["data"]
    assert current["idle_expires_at"] > user["idle_expires_at"]
    assert current["absolute_expires_at"] == user["absolute_expires_at"]


def test_idle_timeout_revokes_and_cannot_be_replayed(lab):
    login(lab)
    key = lab.service.key(lab.client.cookies.get(lab.service.cookie_name))
    lab.clock.now += 1800
    assert lab.client.get("/api/v1/case-generation/access").status_code == 401
    assert not lab.redis.exists(key)
    assert lab.client.get("/api/v1/auth/session").status_code == 401


def test_logout_replay_and_rotation_invalidate_old_token(lab):
    login(lab)
    old = lab.client.cookies.get(lab.service.cookie_name)
    login(lab, "second_operator")
    assert not lab.redis.exists(lab.service.key(old))
    current = lab.client.cookies.get(lab.service.cookie_name)
    assert lab.client.post("/api/v1/auth/logout").status_code == 200
    assert not lab.redis.exists(lab.service.key(current))
    lab.client.cookies.set(lab.service.cookie_name, current)
    assert lab.client.get("/api/v1/workbench/overview").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example", "X-AC-Origin": "http://testserver"},
        {"Origin": "http://testserver:9999", "X-AC-Origin": "http://testserver:9999"},
        {"Origin": "null", "X-AC-Origin": "http://testserver"},
        {
            "Origin": "http://testserver",
            "X-AC-Origin": "http://testserver",
            "Sec-Fetch-Site": "same-site",
        },
    ],
)
def test_login_csrf_and_other_port_rejected(lab, headers):
    response = lab.client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"username": "demo_operator", "password": TEST_PASSWORD},
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_missing_origin_and_missing_or_wrong_csrf_are_rejected(lab):
    login(lab)
    csrf = lab.client.headers.pop("X-CSRF-Token")
    assert lab.client.post("/api/v1/auth/logout").status_code == 403
    assert (
        lab.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )
    lab.client.headers["X-CSRF-Token"] = csrf
    origin = lab.client.headers.pop("Origin")
    assert lab.client.post("/api/v1/auth/logout").status_code == 403
    lab.client.headers["Origin"] = origin
    assert lab.client.post("/api/v1/auth/logout").status_code == 200


def test_cross_port_get_and_other_instance_do_not_share_identity(lab):
    login(lab)
    assert (
        lab.client.get(
            "/api/v1/auth/session", headers={"X-AC-Origin": "http://testserver:9999"}
        ).status_code
        == 403
    )
    other = SessionService(
        lab.service.engine,
        lab.redis,
        lab.service.settings.model_copy(update={"executor_namespace": "other-instance"}),
    )
    assert other.cookie_name != lab.service.cookie_name
    assert other.prefix != lab.service.prefix


def test_wrong_password_and_disabled_account_do_not_create_session(lab):
    response = lab.client.post(
        "/api/v1/auth/login", json={"username": "demo_operator", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_credentials_invalid"
    assert "set-cookie" not in response.headers
    assert not list(lab.redis.scan_iter(lab.service.prefix + "?" * 64))


@pytest.mark.parametrize("change", ["enabled", "role", "password_hash"])
def test_account_disable_role_or_password_change_revokes_session(lab, change):
    login(lab)
    with Session(lab.service.engine) as db, db.begin():
        user = db.scalar(select(User).where(User.username == "demo_operator"))
        old = getattr(user, change)
        setattr(
            user,
            change,
            {"enabled": False, "role": "reviewer", "password_hash": hash_password("changed")}[
                change
            ],
        )
    try:
        assert lab.client.get("/api/v1/auth/session").status_code == 401
    finally:
        with Session(lab.service.engine) as db, db.begin():
            user = db.scalar(select(User).where(User.username == "demo_operator"))
            setattr(user, change, old)


def test_legacy_basic_keeps_original_roles_and_does_not_depend_on_cookie(lab):
    assert (
        lab.client.get(
            "/api/v1/workbench/overview", auth=("demo_operator", TEST_PASSWORD)
        ).status_code
        == 200
    )
    login(lab, "demo_reviewer")
    assert lab.client.get("/api/v1/workbench/overview").status_code == 403
    # An explicit Basic client intentionally chooses its credential over any browser cookie.
    assert (
        lab.client.get(
            "/api/v1/workbench/overview", auth=("demo_operator", TEST_PASSWORD)
        ).status_code
        == 200
    )
    assert (
        lab.client.post(
            "/api/v1/booking/availability", json={}, auth=("demo_reviewer", TEST_PASSWORD)
        ).status_code
        == 403
    )
    assert (
        lab.client.get("/api/v1/judging/access", auth=("demo_operator", "wrong")).status_code == 401
    )


def test_browser_write_guard_applies_to_all_three_authentication_paths(lab):
    login(lab)
    lab.client.headers.pop("X-CSRF-Token")
    for path in ("/booking/availability", "/case-generation", "/judging/calibrations"):
        response = lab.client.post("/api/v1" + path, json={})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "session_csrf_forbidden"


def test_rate_limit_and_service_failure_fail_closed(lab, monkeypatch):
    for _ in range(12):
        assert (
            lab.client.post(
                "/api/v1/auth/login", json={"username": "missing", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        lab.client.post(
            "/api/v1/auth/login", json={"username": "missing", "password": "wrong"}
        ).status_code
        == 429
    )
    from redis.exceptions import ConnectionError

    def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic outage, never include connection credentials")

    monkeypatch.setattr(lab.redis, "eval", unavailable)
    response = lab.client.post(
        "/api/v1/auth/login", json={"username": "demo_operator", "password": TEST_PASSWORD}
    )
    assert response.status_code == 503 and "synthetic" not in response.text
    assert (
        lab.client.get(
            "/api/v1/workbench/overview", auth=("demo_operator", TEST_PASSWORD)
        ).status_code
        == 200
    )


def test_production_requires_https_and_sets_secure_cookie(lab):
    lab.service.settings = lab.service.settings.model_copy(update={"app_env": "production"})
    assert (
        lab.client.post(
            "/api/v1/auth/login", json={"username": "demo_operator", "password": TEST_PASSWORD}
        ).status_code
        == 403
    )
    with TestClient(
        lab.app,
        base_url="https://testserver",
        headers={"Origin": "https://testserver", "X-AC-Origin": "https://testserver"},
    ) as client:
        response = client.post(
            "/api/v1/auth/login", json={"username": "demo_operator", "password": TEST_PASSWORD}
        )
        assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]


def test_concurrent_touch_cannot_resurrect_revoked_session_and_stream_does_not_extend_idle(lab):
    login(lab)
    token = lab.client.cookies.get(lab.service.cookie_name)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "headers": [
                (b"host", b"testserver"),
                (b"x-ac-origin", b"http://testserver"),
                (b"cookie", f"{lab.service.cookie_name}={token}".encode()),
            ],
        }
    )
    key = lab.service.key(token)
    idle = json.loads(lab.redis.get(key))["idle"]
    lab.clock.now += 100
    lab.service.resolve(request, touch=False)
    assert json.loads(lab.redis.get(key))["idle"] == idle

    def read():
        try:
            return lab.service.resolve(request)
        except AgentError as exc:
            assert exc.status_code == 401

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(read) for _ in range(20)]
        lab.redis.delete(key)
        for future in futures:
            future.result()
    assert not lab.redis.exists(key)


def test_open_sse_checks_revocation_before_delivering_more_facts(lab):
    login(lab)
    token = lab.client.cookies.get(lab.service.cookie_name)

    async def connected():
        await asyncio.sleep(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "headers": [
                (b"host", b"testserver"),
                (b"x-ac-origin", b"http://testserver"),
                (b"cookie", f"{lab.service.cookie_name}={token}".encode()),
            ],
        },
        receive=connected,
    )
    request.state.browser_session = True

    def forbidden_read(*args, **kwargs):
        raise AssertionError("Revoked SSE must stop before reading another SQL page")

    reader = SimpleNamespace(settings=SimpleNamespace(sse_max_seconds=30), page=forbidden_read)

    async def check():
        gen = stream(
            request, reader, {"cursor": "v1:synthetic:0:0", "run_id": "synthetic", "user_id": 1}
        )
        assert (await anext(gen)).event == "stream.ready"
        lab.redis.delete(lab.service.key(token))
        assert (await anext(gen)).event == "session.expired"
        with pytest.raises(StopAsyncIteration):
            await anext(gen)

    asyncio.run(check())

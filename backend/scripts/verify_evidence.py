"""Opt-in T06 Linux/API/provider/SSE acceptance, isolated fixtures and audited cleanup."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader, cursor
from app.evidence.recorder import redact
from app.executors.budget import OPEN_AUTHORIZATION, authorization_limit
from app.executors.service import case_content
from app.models import Artifact, TestCase, TestRun, User

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from test_database import disposable_database, migrate  # noqa: E402


def collect_sse(base, auth, run_id, deadline):
    """Drop after five committed frames, then resume only by Last-Event-ID."""
    observed, connections = [], []
    current = cursor(run_id)
    dropped = ended = False
    while time.monotonic() < deadline and not ended:
        record = {"last_event_id": current, "events": []}
        connections.append(record)
        with httpx.Client(base_url=base, auth=auth, timeout=40) as client:
            with client.stream(
                "GET", f"/api/v1/runs/{run_id}/events/stream", headers={"Last-Event-ID": current}
            ) as response:
                response.raise_for_status()
                record["headers"] = dict(response.headers)
                frame = []
                for line in response.iter_lines():
                    if line:
                        frame.append(line)
                        continue
                    fields = {}
                    for part in frame:
                        key, _, value = part.partition(":")
                        if key in ("id", "event", "data"):
                            fields[key] = value.lstrip()
                    frame = []
                    if not fields:
                        continue
                    kind = fields.get("event")
                    record["events"].append(kind)
                    if kind == "trace":
                        fact = json.loads(fields["data"])
                        assert fields["id"] == fact["cursor"]
                        assert fact["event"]["run_seq"] == len(observed) + 1
                        observed.append(fact)
                        current = fact["cursor"]
                        if not dropped and len(observed) == 5:
                            dropped = True
                            record["deliberate_disconnect"] = True
                            break
                    if kind == "stream.end":
                        ended = True
                        break
                    assert kind not in ("stream.error", "stream.reset"), fields
    assert ended and dropped, "SSE did not completely drain after resume"
    return {"events": observed, "connections": connections, "disconnected_after": 5}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true", required=True)
    parser.add_argument("--image", default="agentchaos-t06:dev")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--ui-port", type=int, default=5174)
    args = parser.parse_args()
    settings = Settings()
    assert settings.executor_allow_paid_model_calls and authorization_limit(settings) == -1
    assert settings.executor_budget_authorization_id == OPEN_AUTHORIZATION
    assert settings.casegen_budget_usd == settings.judge_budget_usd == 0
    identifier = uuid4().hex[:12]
    local = PROJECT_ROOT / ".local/t06" / identifier
    evidence = PROJECT_ROOT / "docs/evidence/T06" / ("linux-" + identifier)
    local.mkdir(parents=True)
    evidence.mkdir(parents=True)
    redis = Redis.from_url(settings.redis_url)
    key = f"ac:t05:budget:{OPEN_AUTHORIZATION}:agent"
    assert redis.hget(key, "limit") == b"-1", "Approved persistent ledger must exist"
    containers, sessions = [], []
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "notice": "Real provider; mechanical dev fixtures, no human approval or T08 assertions.",
    }
    base = f"http://127.0.0.1:{args.port}"
    docker_base = [
        r"D:\DockerDesktop\resources\bin\docker.exe",
        "--config",
        str(PROJECT_ROOT / ".local/t05/docker-config"),
        "--host",
        "npipe:////./pipe/dockerDesktopLinuxEngine",
    ]

    def save(name, value):
        (evidence / name).write_text(
            json.dumps(redact(value, settings), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    def ledger():
        return {k.decode(): int(v) for k, v in redis.hgetall(key).items()}

    report["ledger_before"] = ledger()

    def docker(*command, check=True):
        result = subprocess.run(
            [*docker_base, *command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        if check and result.returncode:
            (local / "docker-error.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
            raise RuntimeError("Docker command failed; diagnostic retained locally")
        return result

    with disposable_database("T06") as engine:
        migrate(engine)
        password, sql_password = secrets.token_hex(16), secrets.token_hex(24)
        seed_database(engine, password)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        sql_user = "ac_t06_" + identifier
        admin = create_database_engine(settings, database="mysql")
        envfile, vite, pool, future = local / "worker.env", None, None, None
        reader = TraceReader(engine, settings)
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(
                    text(f"CREATE USER '{sql_user}'@:host IDENTIFIED BY :password"),
                    {"host": "%", "password": sql_password},
                )
                c.exec_driver_sql(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{engine.url.database}`.* "
                    f"TO '{sql_user}'@'%%'"
                )
            case_ids = []
            with Session(engine) as session, session.begin():
                reviewer = session.scalar(select(User).where(User.role == "reviewer"))
                for name, utterance in [
                    ("rules", "实验预约的时间范围、人数和确认要求是什么？请给依据。"),
                    ("booking", "明天A101，14:00到15:00，10人，请生成完整待确认草稿，等我确认。"),
                ]:
                    case = TestCase(
                        case_key=f"T06-{name}-{identifier}".upper(),
                        revision=1,
                        family_key="development-" + name,
                        category="normal",
                        dataset_split="dev",
                        source="T06-mechanical-development-fixture-not-human-approval",
                        turns_json=[{"user": utterance}],
                        fixture_json={
                            "fixture": "booking-default-v1",
                            "reference_clock": "2026-10-26T09:00:00+08:00",
                        },
                        expected_json={
                            "assertions": [
                                {"type": "booking_count", "equals": int(name == "booking")}
                            ]
                        },
                        review_status="approved",
                        reviewer_id=reviewer.id,
                        reviewed_at=datetime.now(UTC),
                        content_hash="0" * 64,
                    )
                    case.content_hash = canonical_hash(case_content(case))
                    session.add(case)
                    session.flush()
                    case_ids.append(case.id)
            worker_env = {
                "APP_ENV": "test",
                "MYSQL_HOST": "host.docker.internal",
                "MYSQL_PORT": str(settings.mysql_port),
                "MYSQL_DATABASE": engine.url.database,
                "MYSQL_USER": sql_user,
                "MYSQL_PASSWORD": sql_password,
                "REDIS_HOST": "host.docker.internal",
                "REDIS_PORT": str(settings.redis_port),
                "REDIS_PASSWORD": settings.redis_password.get_secret_value(),
                "REDIS_DB": str(settings.redis_db),
                "DEEPSEEK_API_KEY": settings.deepseek_api_key.get_secret_value(),
                "AGENT_MODEL": settings.agent_model,
                "AGENT_THINKING": "disabled",
                "AGENT_MAX_OUTPUT_TOKENS": str(settings.agent_max_output_tokens),
                "MODEL_MAX_RETRIES": "0",
                "MODEL_TIMEOUT_SECONDS": str(settings.model_timeout_seconds),
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "true",
                "EXECUTOR_BUDGET_AUTHORIZATION_ID": OPEN_AUTHORIZATION,
                "EXECUTOR_AGENT_BUDGET_UNLIMITED": "true",
                "EXECUTOR_AGENT_BUDGET_USD": "0",
                "EXECUTOR_NAMESPACE": "ac:t06:linux:" + identifier,
                "EXECUTOR_QUEUE": "t06-" + identifier,
                "RUN_CONCURRENCY": "2",
                "EXECUTOR_LEASE_SECONDS": "30",
            }
            envfile.write_text(
                "\n".join(f"{k}={v}" for k, v in worker_env.items()) + "\n", encoding="utf-8"
            )
            mounts = []
            for source, target, readonly in [
                ("backend/app", "backend/app", True),
                ("backend/alembic", "backend/alembic", True),
                (".local", ".local", False),
                (".cache/jieba", ".cache/jieba", False),
            ]:
                mounts += [
                    "--mount",
                    f"type=bind,source={PROJECT_ROOT / source},"
                    f"target=/workspace/AgentChaos/{target}" + (",readonly" if readonly else ""),
                ]

            def start(suffix, command, ports=None):
                name = "ac-t06-" + identifier + "-" + suffix
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--env-file",
                    str(envfile),
                    *mounts,
                    *(ports or []),
                    args.image,
                    *command,
                )
                containers.append(name)
                return name

            start(
                "api",
                ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
                ["-p", f"127.0.0.1:{args.port}:8000"],
            )
            client = httpx.Client(base_url=base, auth=("demo_operator", password), timeout=15)
            for _ in range(80):
                try:
                    if client.get("/api/v1/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("Linux API not ready")

            def request(method, path, body=None):
                response = client.request(method, path, json=body)
                if response.is_error:
                    save("http-error.json", response.json())
                    response.raise_for_status()
                return response.json()["data"]

            created = request(
                "POST",
                "/api/v1/runs",
                {
                    "request_id": str(uuid4()),
                    "target_version_id": runtime.version_id,
                    "case_revision_ids": case_ids,
                    "seed": 26,
                    "limits": {
                        "concurrency": 2,
                        "timeout_seconds": 600,
                        "max_steps": 160,
                        "max_model_calls": 30,
                        "max_tokens": 600000,
                    },
                    "budgets": {"agent_microusd": 250000},
                },
            )
            run_id = created["run_id"]
            sessions = [e["session_id"] for e in created["executions"]]
            save("created.json", created)
            pool = ThreadPoolExecutor(1)
            future = pool.submit(
                collect_sse, base, ("demo_operator", password), run_id, time.monotonic() + 600
            )
            for n in (1, 2):
                start(
                    f"worker{n}",
                    [
                        "celery",
                        "-A",
                        "app.workers.celery_app:celery_app",
                        "worker",
                        "--loglevel=INFO",
                        "--concurrency=1",
                        f"--hostname=t06-worker{n}@%h",
                    ],
                )
            start("dispatcher", ["python", "-m", "app.executors.dispatch"])
            wait_deadline, ui_checked = time.monotonic() + 500, False
            while time.monotonic() < wait_deadline:
                view = request("GET", f"/api/v1/runs/{run_id}")
                save("latest-run.json", view)
                waiting = [e for e in view["executions"] if e["status"] == "waiting_input"]
                if waiting:
                    save("waiting-input.json", view)
                    if not ui_checked:
                        ui_env = {
                            **os.environ,
                            "API_PROXY_TARGET": base,
                            "AC_E2E_BASE_URL": f"http://127.0.0.1:{args.ui_port}",
                            "AC_T06_RUN_ID": run_id,
                            "AC_T06_DEMO_PASSWORD": password,
                            "AC_E2E_EVIDENCE_DIR": str(evidence),
                            "PLAYWRIGHT_BROWSERS_PATH": str(PROJECT_ROOT / ".cache/playwright"),
                        }
                        with (local / "vite.log").open("w", encoding="utf-8") as log:
                            vite = subprocess.Popen(
                                [
                                    "node",
                                    "node_modules/vite/bin/vite.js",
                                    "--host",
                                    "127.0.0.1",
                                    "--port",
                                    str(args.ui_port),
                                    "--strictPort",
                                ],
                                cwd=PROJECT_ROOT / "frontend",
                                env=ui_env,
                                stdout=log,
                                stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            )
                        for _ in range(40):
                            try:
                                if httpx.get(f"http://127.0.0.1:{args.ui_port}").status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            time.sleep(0.25)
                        ui = subprocess.run(
                            [
                                "node",
                                "node_modules/@playwright/test/cli.js",
                                "test",
                                "tests/evidence.spec.ts",
                            ],
                            cwd=PROJECT_ROOT / "frontend",
                            env=ui_env,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            timeout=120,
                        )
                        (evidence / "frontend-playwright.txt").write_text(
                            ui.stdout + ui.stderr, encoding="utf-8"
                        )
                        ui_checked = True
                        assert ui.returncode == 0, "Real frontend acceptance failed"
                    for e in waiting:
                        request(
                            "POST",
                            f"/api/v1/runs/{run_id}/executions/{e['execution_id']}/input",
                            {
                                "request_id": str(uuid4()),
                                "expected_revision": e["timing"]["revision"],
                                "text": "确认",
                            },
                        )
                if view["status"] in ("completed", "cancelled", "failed"):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Real T06 run did not terminate")
            stream = future.result(timeout=45)
            save("sse-connections.json", stream)
            observed = []
            after = None
            while True:
                page = request(
                    "GET", f"/api/v1/runs/{run_id}/events" + (f"?cursor={after}" if after else "")
                )
                observed += page["items"]
                after = page["next_cursor"]
                if not page["has_more"]:
                    break
            assert stream["events"] == observed, (
                "SSE must exactly equal committed replay, no duplicate/gap"
            )
            snap = request("GET", f"/api/v1/runs/{run_id}/snapshot")
            assert snap["event_count"] == len(observed) and snap["pending_model_observations"] == 0
            kinds = {f["event"]["type"] for f in observed}
            assert {
                "retrieval.completed",
                "model.completed",
                "tool.completed",
                "confirmation.requested",
                "confirmation.resumed",
                "checkpoint.restored",
                "run.completed",
            } <= kinds
            assert all(
                e["status"] in ("passed", "failed", "inconclusive") for e in view["executions"]
            ), view
            assert len(view["executions"][1]["after"]) == 1, view
            before_replay = ledger()
            for _ in range(3):
                request("GET", f"/api/v1/runs/{run_id}/snapshot")
                request("GET", f"/api/v1/runs/{run_id}/events")
            assert ledger() == before_replay, "Reading evidence must not invoke provider"
            save("events.json", observed)
            save("snapshot.json", snap)
            with Session(engine) as session:
                for artifact in session.scalars(select(Artifact)):
                    path, digest = reader.artifact(1, run_id, artifact.id)
                    out = evidence / "artifacts" / path.name
                    out.parent.mkdir(exist_ok=True)
                    shutil.copy2(path, out)
            report.update(
                run=view,
                checks={
                    "sse_equals_mysql_replay": True,
                    "resume_after_five": True,
                    "readonly_did_not_call_model": True,
                    "real_ui": ui_checked,
                    "booking_count": 1,
                    "event_count": len(observed),
                    "no_agent_pass_claimed": True,
                },
                image=docker("image", "inspect", args.image, "--format", "{{.Id}}").stdout.strip(),
            )
            save("real-chain.json", report)
            client.close()
        finally:
            if vite:
                vite.terminate()
                vite.wait(timeout=10)
            for name in reversed(containers):
                logs = docker("logs", name, check=False)
                raw = logs.stdout + logs.stderr
                for secret in (
                    password,
                    sql_password,
                    settings.redis_password.get_secret_value(),
                    settings.deepseek_api_key.get_secret_value(),
                ):
                    if secret:
                        raw = raw.replace(secret, "[REDACTED]")
                (evidence / (name + ".log.txt")).write_text(raw, encoding="utf-8")
                docker("rm", "-f", name, check=False)
            if future and not future.done():
                # API removal closes the stream and leaves the failed run evidence intact.
                try:
                    future.result(timeout=45)
                except Exception:
                    pass
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
            with Session(engine) as session:
                for run_id in session.scalars(select(TestRun.run_uuid)):
                    partial, after = [], None
                    while True:
                        page = reader.page(1, run_id, after=after)
                        partial += page["items"]
                        after = page["next_cursor"]
                        if not page["has_more"]:
                            break
                    save(f"retained-events-{run_id}.json", partial)
                    save(f"retained-snapshot-{run_id}.json", reader.snapshot(1, run_id))
                    source = PROJECT_ROOT / ".local/t05/model-calls" / run_id
                    if source.exists():
                        shutil.copytree(
                            source, evidence / "model-calls" / run_id, dirs_exist_ok=True
                        )
            report["ledger_after"] = ledger()
            report["finished_at"] = datetime.now(UTC).isoformat()
            save("final-report.json", report)
            for sid in sessions:
                runtime.saver.delete_thread(sid)
            keys = list(redis.scan_iter(match="ac:t06:linux:" + identifier + "*"))
            if keys:
                redis.delete(*keys)
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.exec_driver_sql(f"DROP USER IF EXISTS '{sql_user}'@'%%'")
            admin.dispose()
            envfile.unlink(missing_ok=True)
            save(
                "cleanup.json",
                {
                    "containers_removed": containers,
                    "sessions_removed": sessions,
                    "temporary_user_removed": True,
                    "global_budget_ledger_preserved": True,
                },
            )
    redis.close()
    print(
        json.dumps(
            {
                "evidence": str(evidence),
                "checks": report.get("checks"),
                "ledger": report["ledger_after"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

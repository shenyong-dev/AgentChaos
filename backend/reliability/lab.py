"""Owned T17 Linux/HTTP/native Redis lab. All models in this entrypoint are offline."""

import hashlib
import json
import math
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from celery import Celery
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader
from app.executors.dispatch import dispatch_pending
from app.executors.service import RunService, case_content
from app.models import (
    Artifact,
    AssertionResult,
    Booking,
    BookingDraft,
    BookingSlot,
    TargetVersion,
    TestCase,
    TestExecution,
    TestRun,
    TraceEvent,
    User,
)
from app.schemas.runs import CreateRun, ExecutionInput
from reliability.proxy import FaultProxy

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from agent_test_support import REFERENCE  # noqa: E402
from test_database import disposable_database, migrate  # noqa: E402

DOCKER = [
    "D:/DockerDesktop/resources/bin/docker.exe",
    "--config",
    str(PROJECT_ROOT / ".local/t05/docker-config"),
    "--host",
    "npipe:////./pipe/dockerDesktopLinuxEngine",
]
BOOK = "T17-BOOK 明天A101，14:00到15:00，10人，请给完整草稿等待确认。"


def stop_process(process):
    if process is not None and process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15
            )
        else:
            process.terminate()
        process.wait(timeout=15)


class Lab:
    api_module = "app.main:app"
    worker_module = "reliability.worker:celery_app"
    case_source = "explicit_offline_control_not_human_formal_dataset"

    def __init__(self, *, image="agentchaos-t07:dev"):
        self.base = Settings()
        assert not any(
            getattr(self.base, name)
            for name in (
                "allow_paid_model_calls",
                "executor_allow_paid_model_calls",
                "casegen_allow_paid_model_calls",
                "judge_allow_paid_model_calls",
            )
        )
        assert shutil.disk_usage("C:/").free > 1_500_000_000
        for port in (16381, 13317, 18017, 5184):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        self.marker = uuid4().hex[:12]
        self.image = image
        self.local = PROJECT_ROOT / ".local/t17" / self.marker
        self.output = PROJECT_ROOT / "docs/evidence/T17" / ("offline-" + self.marker)
        self.local.mkdir(parents=True)
        self.output.mkdir(parents=True)
        self.signals = self.local / "signals"
        self.signals.mkdir()
        self.processes, self.containers, self.runs = [], [], []
        self.api = self.native = self.proxy = self.engine = self.redis = None
        self.stack = ExitStack()
        self.sql_user = "ac_t17_" + self.marker
        self.sql_password = secrets.token_urlsafe(28)
        self.admin = create_database_engine(self.base, database="mysql")
        self.sql_user_created = False
        self.api_generation = 0
        self.measurements = []

    def save(self, name, value):
        path = self.output / name
        if path.exists():
            raise RuntimeError("Do not overwrite original evidence: " + name)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )

    def docker(self, *args):
        result = subprocess.run(DOCKER + list(args), capture_output=True, timeout=40)
        if result.returncode:
            # Detailed raw logs remain local; environment files contain passwords.
            with (self.local / "docker-errors.log").open("ab") as file:
                file.write(result.stdout + result.stderr)
            raise RuntimeError("Owned Docker operation failed: " + args[0])
        payload = result.stdout + (result.stderr if args[0] == "logs" else b"")
        return payload.decode("utf-8").strip()

    def spawn(self, command, env, cwd, name):
        out = (self.local / (name + ".stdout.log")).open("wb")
        err = (self.local / (name + ".stderr.log")).open("wb")
        try:
            process = subprocess.Popen(
                command,
                env={**os.environ, **env, "TEMP": str(PROJECT_ROOT / ".cache/tmp")},
                cwd=cwd,
                stdout=out,
                stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        finally:
            out.close()
            err.close()
        self.processes.append(process)
        return process

    def start_redis(self):
        directory = self.local / "redis"
        directory.mkdir(exist_ok=True)
        config = self.local / "redis.conf"
        modules = [
            line
            for line in Path("D:/RedisSearch/memurai.conf").read_text(encoding="utf-8").splitlines()
            if line.startswith("loadmodule ")
        ]
        assert len(modules) == 2
        config.write_text(
            "\n".join(
                [
                    "bind 0.0.0.0",
                    "protected-mode yes",
                    "port 16381",
                    "dir " + directory.as_posix(),
                    "appendonly yes",
                    "appendfsync always",
                    "save 60 1",
                    "requirepass " + self.base.redis_password.get_secret_value(),
                    *modules,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        generation = sum(1 for p in self.local.glob("redis-*.stdout.log")) + 1
        self.native = self.spawn(
            ["D:/RedisSearch/memurai.exe", str(config)], {}, "D:/RedisSearch", f"redis-{generation}"
        )
        self.redis = Redis(
            host="127.0.0.1",
            port=16381,
            password=self.base.redis_password.get_secret_value(),
            socket_timeout=5,
        )
        for _ in range(100):
            if self.native.poll() is not None:
                raise RuntimeError("Owned native Redis exited")
            try:
                if self.redis.ping() and self.redis.execute_command("FT._LIST") is not None:
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise RuntimeError("Owned native Redis startup timeout")

    def stop_redis(self):
        self.redis.shutdown(save=True)
        self.native.wait(timeout=15)
        self.redis.close()

    def __enter__(self):
        try:
            self.start_redis()
            self.engine = self.stack.enter_context(disposable_database("T17"))
            migrate(self.engine)
            seed_database(self.engine, self.base.demo_seed_password.get_secret_value())
            with Session(self.engine) as session, session.begin():
                owner = session.get(User, 1)
                session.add(
                    User(username="t17_other", role="operator", password_hash=owner.password_hash)
                )
            with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(
                    text(f"CREATE USER '{self.sql_user}'@'%' IDENTIFIED BY :password"),
                    {"password": self.sql_password},
                )
                self.sql_user_created = True
                c.execute(
                    text(
                        f"GRANT SELECT,INSERT,UPDATE,DELETE ON `{self.engine.url.database}`.* "
                        f"TO '{self.sql_user}'@'%'"
                    )
                )
            self.proxy = FaultProxy(13317, (self.base.mysql_host, self.base.mysql_port))
            self.settings = self.base.model_copy(
                update={
                    "mysql_database": self.engine.url.database,
                    "redis_port": 16381,
                    "redis_db": 0,
                    "run_concurrency": 5,
                    "executor_lease_seconds": 10,
                    "executor_agent_budget_usd": 0,
                    "executor_agent_budget_unlimited": False,
                    "executor_namespace": "ac:t17:" + self.marker,
                    "executor_queue": "t17-" + self.marker,
                    "deepseek_api_key": SecretStr(""),
                }
            )
            runtime = AgentRuntime(self.engine, self.redis, self.settings, auto_register=True)
            self.version = runtime.version_id
            self.service = RunService(self.engine, self.settings)
            self.reader = TraceReader(self.engine, self.settings)
            self.env = {
                "APP_ENV": "test",
                "MYSQL_HOST": "127.0.0.1",
                "MYSQL_PORT": "13317",
                "MYSQL_DATABASE": self.engine.url.database,
                "MYSQL_USER": self.sql_user,
                "MYSQL_PASSWORD": self.sql_password,
                "DEMO_SEED_PASSWORD": self.base.demo_seed_password.get_secret_value(),
                "REDIS_HOST": "127.0.0.1",
                "REDIS_PORT": "16381",
                "REDIS_DB": "0",
                "REDIS_PASSWORD": self.base.redis_password.get_secret_value(),
                "DEEPSEEK_API_KEY": "",
                "ALLOW_PAID_MODEL_CALLS": "false",
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
                "EXECUTOR_AGENT_BUDGET_USD": "0",
                "EXECUTOR_AGENT_BUDGET_UNLIMITED": "false",
                "EXECUTOR_NAMESPACE": self.settings.executor_namespace,
                "EXECUTOR_QUEUE": self.settings.executor_queue,
                "EXECUTOR_LEASE_SECONDS": "10",
                "RUN_CONCURRENCY": "5",
                "SSE_MAX_SECONDS": "5",
                "AC_T17_MODE": "offline",
            }
            # Every role is part of the frozen execution contract, even when the
            # offline task invokes none of the paid providers.
            self.env["MODEL_TIMEOUT_SECONDS"] = str(self.base.model_timeout_seconds)
            self.env["MODEL_MAX_RETRIES"] = "0"
            for prefix, field in (("AGENT", "agent"), ("CASEGEN", "casegen"), ("JUDGE", "judge")):
                self.env[prefix + "_MODEL"] = getattr(self.base, field + "_model")
                self.env[prefix + "_THINKING"] = getattr(self.base, field + "_thinking")
                self.env[prefix + "_MAX_OUTPUT_TOKENS"] = str(
                    getattr(self.base, field + "_max_output_tokens")
                )
            self.worker_env = {
                **self.env,
                "MYSQL_HOST": "host.docker.internal",
                "REDIS_HOST": "host.docker.internal",
                "AC_T17_SIGNALS": f"/workspace/AgentChaos/.local/t17/{self.marker}/signals",
            }
            env_file = self.local / "worker.env"
            env_file.write_text(
                "\n".join(f"{k}={v}" for k, v in self.worker_env.items()) + "\n", encoding="utf-8"
            )
            self.env_file = env_file
            self.celery = Celery("T17-producer", broker=self.settings.redis_url)
            self.celery.conf.broker_transport_options = {
                "global_keyprefix": self.settings.executor_namespace + ":queue:"
            }
            self.start_api()
            private = {
                "marker": self.marker,
                "database": self.engine.url.database,
                "sql_user": self.sql_user,
                "sql_password": self.sql_password,
                "settings": self.env,
                "version": self.version,
            }
            (self.local / "session-private.json").write_text(json.dumps(private), encoding="utf-8")
            self.save(
                "session.json",
                {
                    "marker": self.marker,
                    "database": self.engine.url.database,
                    "mode": "explicit_offline_model_with_real_Linux_SQL_Redis_HTTP",
                    "image": self.image,
                    "native_redis_port": 16381,
                    "mysql_fault_proxy_port": 13317,
                    "api": "http://127.0.0.1:18017",
                    "paid_calls": 0,
                    "version": self.version,
                    "config_hash": runtime.manifest["config_hash"],
                },
            )
            return self
        except BaseException:
            self.close()
            raise

    def start_api(self):
        self.api_generation += 1
        self.api = self.spawn(
            [
                sys.executable,
                "-m",
                "uvicorn",
                self.api_module,
                "--host",
                "127.0.0.1",
                "--port",
                "18017",
            ],
            self.env,
            PROJECT_ROOT / "backend",
            f"api-{self.api_generation}",
        )
        for _ in range(100):
            try:
                if (
                    httpx.get("http://127.0.0.1:18017/api/v1/health/live", timeout=2).status_code
                    == 200
                ):
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.15)
        raise RuntimeError("Owned API startup timeout")

    def start_worker(self, name, concurrency=5):
        container = "ac-t17-" + self.marker + "-" + name
        mounts = []
        for source, target, readonly in (
            ("backend/app", "backend/app", True),
            ("backend/reliability", "backend/reliability", True),
            ("backend/alembic", "backend/alembic", True),
            ("knowledge", "knowledge", True),
            (f".local/t17/{self.marker}", f".local/t17/{self.marker}", False),
            (".local/evidence", ".local/evidence", False),
            (".cache/jieba", ".cache/jieba", False),
        ):
            (PROJECT_ROOT / source).mkdir(exist_ok=True, parents=True)
            mounts += [
                "--mount",
                f"type=bind,source={PROJECT_ROOT / source},target=/workspace/AgentChaos/{target}"
                + (",readonly" if readonly else ""),
            ]
        probe = self.docker(
            "run",
            "--rm",
            "--env-file",
            str(self.env_file),
            *mounts,
            self.image,
            "python",
            "-c",
            "import json,importlib;"
            f"getattr(importlib.import_module('{self.worker_module.split(':')[0]}'),'celery_app');"
            "from app.core.config import get_settings;"
            "from app.db.engine import create_database_engine;"
            "from app.executors.service import RunService;"
            "print(json.dumps(RunService(create_database_engine(),get_settings()).current_manifest()))",
        )
        actual = json.loads(probe)
        expected = self.service.current_manifest()
        differences = []

        def compare(left, right, path=""):
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(left.keys() | right.keys()):
                    compare(left.get(key), right.get(key), path + "/" + key)
            elif left != right:
                differences.append({"field": path, "host": left, "linux": right})

        compare(expected, actual)
        self.save(
            "worker-binding-" + name + ".json",
            {
                "host_config_hash": expected["config_hash"],
                "linux_config_hash": actual["config_hash"],
                "differences": differences,
                "no_model_calls": True,
            },
        )
        assert not differences, "Linux source/knowledge/role preflight mismatch"
        self.docker(
            "run",
            "-d",
            "--name",
            container,
            "--hostname",
            name,
            "--label",
            "agentchaos.lab=" + self.marker,
            "--env-file",
            str(self.env_file),
            *mounts,
            self.image,
            "celery",
            "-A",
            self.worker_module,
            "worker",
            "--loglevel=WARNING",
            "--concurrency",
            str(concurrency),
            "--hostname",
            name + "@%h",
        )
        self.containers.append(container)
        return container

    def remove_worker(self, container):
        assert container in self.containers and self.marker in container
        label = self.docker(
            "inspect", container, "--format", '{{index .Config.Labels "agentchaos.lab"}}'
        )
        assert label == self.marker
        logs = self.docker("logs", container)
        (self.local / (container + ".log")).write_text(logs, encoding="utf-8")
        self.docker("rm", "-f", container)
        self.containers.remove(container)

    def case(self, key, text_value, *, confirm=False, expected=0):
        with Session(self.engine) as session, session.begin():
            row = TestCase(
                case_key="T17-" + key,
                revision=1,
                family_key="explicit-t17-infrastructure-control-" + key.lower(),
                category="normal",
                dataset_split="dev",
                source=self.case_source,
                turns_json=[{"user": text_value}] + ([{"user": "确认"}] if confirm else []),
                fixture_json={
                    "fixture": "booking-default-v1",
                    "reference_clock": REFERENCE.isoformat(),
                },
                expected_json={
                    "schema_version": "1.1",
                    "assertions": [{"type": "booking_count", "equals": expected}],
                },
                review_status="approved",
                reviewer_id=2,
                reviewed_at=REFERENCE,
                content_hash="pending",
            )
            row.content_hash = canonical_hash(case_content(row))
            session.add(row)
            session.flush()
            return row.id

    def create(self, cases, *, concurrency=1, agent_microusd=0):
        values = CreateRun(
            request_id=uuid4(),
            target_version_id=self.version,
            case_revision_ids=cases,
            budgets={"agent_microusd": agent_microusd},
            limits={"concurrency": concurrency, "timeout_seconds": 300},
        )
        run = self.service.create(1, values)
        assert self.service.create(1, values) == run
        self.runs.append(run)
        return run

    def publish(self, identifier):
        self.celery.send_task(
            "agentchaos.execute", args=[identifier], queue=self.settings.executor_queue, retry=False
        )

    def dispatch(self, run, *, duplicate=False):
        result = dispatch_pending(
            self.engine, self.settings, publisher=self.publish, force=True, only_run=run
        )
        if duplicate:
            for execution in self.service.read(1, run)["executions"]:
                self.publish(execution["execution_id"])
        return result

    def wait(self, run, predicate=None, *, seconds=90, cap=None):
        end = time.monotonic() + seconds
        next_scan = time.monotonic() + 2
        peak = 0
        while time.monotonic() < end:
            view = self.service.read(1, run)
            active = sum(item["status"] == "running" for item in view["executions"])
            peak = max(peak, active)
            if cap is not None:
                assert active <= cap, (active, cap)
            if (predicate and predicate(view)) or (
                predicate is None and view["status"] == "completed"
            ):
                return view, peak
            if time.monotonic() >= next_scan:
                dispatch_pending(self.engine, self.settings, publisher=self.publish, only_run=run)
                next_scan = time.monotonic() + 2
            time.sleep(0.15)
        self.save("timeout-" + run + ".json", view)
        raise RuntimeError("Owned T17 run timed out: " + run)

    def bookings(self, view):
        scopes = [item["scope_id"] for item in view["executions"]]
        with Session(self.engine) as session:
            return session.scalar(
                select(func.count()).select_from(Booking).where(Booking.scope_id.in_(scopes))
            )

    def supply(self, run, text_value="确认"):
        row = self.service.read(1, run)["executions"][0]
        request = ExecutionInput(
            request_id=uuid4(), expected_revision=row["timing"]["revision"], text=text_value
        )
        self.service.supply_input(1, run, row["execution_id"], request)
        self.service.supply_input(1, run, row["execution_id"], request)
        self.dispatch(run, duplicate=True)

    def loads(self):
        cases = [
            self.case(f"LOAD-{i:02}", "明确T17离线负载控制，只回复一句话，不预约。")
            for i in range(15)
        ]
        rows = []
        for cap in (1, 3, 5):
            began = time.monotonic()
            run = self.create(cases, concurrency=cap)
            self.dispatch(run, duplicate=True)
            view, peak = self.wait(run, cap=cap, seconds=150)
            assert len(view["executions"]) == 15 and self.bookings(view) == 0
            assert all(
                item["status"] == "passed" and item["attempt"] == 1 for item in view["executions"]
            ), view["summary"]
            durations = sorted(
                (
                    datetime.fromisoformat(item["timing"]["finished_at"])
                    - datetime.fromisoformat(item["timing"]["claimed_at"])
                ).total_seconds()
                for item in view["executions"]
            )
            rows.append(
                {
                    "concurrency_cap": cap,
                    "run_id": run,
                    "samples": 15,
                    "observed_peak": peak,
                    "claim_to_finish_p50_seconds": durations[7],
                    "claim_to_finish_p95_seconds": durations[math.ceil(0.95 * len(durations)) - 1],
                    "create_to_observe_batch_seconds": round(time.monotonic() - began, 3),
                    "passed": 15,
                    "bookings": 0,
                    "duplicate_messages_added": 15,
                    "boundary": (
                        "Same host; explicit offline gateway; 15 related controls, "
                        "not provider capacity or statistical independence"
                    ),
                }
            )
            self.save(f"load-{cap}.json", rows[-1])
        self.measurements = rows
        return rows

    def close(self):
        if self.proxy:
            self.proxy.resume()
        for container in list(self.containers):
            self.remove_worker(container)
        if self.redis and self.native and self.native.poll() is None:
            try:
                self.stop_redis()
            except Exception:
                pass
        for process in reversed(self.processes):
            stop_process(process)
        if self.redis:
            self.redis.close()
        if self.proxy:
            self.proxy.close()
        if self.sql_user_created:
            with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(text(f"DROP USER '{self.sql_user}'@'%'"))
        self.stack.close()
        self.admin.dispose()

    def export(self, *, failed=None):
        """Store each raw row/artifact once; summaries reference this one archive."""
        path = self.output / "raw-evidence.zip"
        assert not path.exists()
        entries = {}
        with zipfile.ZipFile(
            path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:

            def put(name, body):
                assert name not in entries
                archive.writestr(name, body)
                entries[name] = {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}

            with Session(self.engine) as session:
                for model in (
                    TestRun,
                    TestExecution,
                    TraceEvent,
                    AssertionResult,
                    Booking,
                    BookingDraft,
                    BookingSlot,
                    TestCase,
                    TargetVersion,
                    Artifact,
                ):
                    data = bytearray()
                    for row in session.scalars(select(model).order_by(model.id)):
                        value = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                        if "lease_token" in value:
                            value["lease_token"] = "[REDACTED]" if value["lease_token"] else None
                        data.extend(
                            (json.dumps(value, ensure_ascii=False, default=str) + "\n").encode()
                        )
                    put(model.__tablename__ + ".jsonl", bytes(data))
                for row in session.scalars(select(Artifact).where(Artifact.run_id.is_not(None))):
                    source = PROJECT_ROOT / row.relative_path
                    raw = source.read_bytes()
                    assert (
                        len(raw) == row.size_bytes and hashlib.sha256(raw).hexdigest() == row.sha256
                    )
                    put(f"artifacts/{row.id}.json", raw)
            patch = subprocess.check_output(["git", "diff", "--", "backend/app"], cwd=PROJECT_ROOT)
            put("application-source.patch", patch)
            sources = {
                p.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (PROJECT_ROOT / "backend/reliability").glob("*.py")
            }
            put("harness-source-hashes.json", json.dumps(sources, sort_keys=True).encode())
        self.save(
            "raw-evidence-manifest.json",
            {
                "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "archive_bytes": path.stat().st_size,
                "entries": entries,
                "source_base_commit": "0f26016372593a6fdcb2300bd907bc1be0c40184",
                "failed": failed,
                "boundary": (
                    "Explicit offline controls; one copy of each raw row/artifact. "
                    "User password hashes excluded; lease tokens redacted."
                ),
            },
        )

    def __exit__(self, kind, value, traceback):
        self.close()

"""Owned Docker Worker/dispatcher lifecycle, with bounded background supervision.

The CLI never installs an engine or dependencies and never initializes budgets.
Only explicit start creates containers. Status and logs never expose Docker env.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.core.deployment import database_binding, execution_fingerprint, source_fingerprint

LABEL = "io.agentchaos.workers"
PACKAGES = (
    "celery",
    "langgraph",
    "langchain-core",
    "langchain-deepseek",
    "langgraph-checkpoint-redis",
)
DOCKER = Path("D:/DockerDesktop/resources/bin/docker.exe")
ENDPOINT = "npipe:////./pipe/dockerDesktopLinuxEngine"
RETRY_DELAYS = (2, 4, 8)


class OperationError(Exception):
    pass


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".writing")
    temporary.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
    os.replace(temporary, path)


@contextlib.contextmanager
def lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "operation.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def load(folder):
    path = folder / "state.json"
    return json.loads(path.read_bytes()) if path.exists() else {}


class Docker:
    def __init__(self):
        self.config = PROJECT_ROOT / ".local/t05/docker-config"

    def call(self, args, *, timeout=20, optional=False):
        if not DOCKER.is_file():
            raise OperationError("docker_not_installed_use_existing_D_drive_environment")
        try:
            result = subprocess.run(
                [str(DOCKER), "--config", str(self.config), "--host", ENDPOINT, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise OperationError("docker_unavailable_or_timeout") from None
        if result.returncode and not optional:
            # Docker errors may contain environment/config details; do not echo stderr.
            raise OperationError("docker_command_failed_check_engine_or_owned_container")
        return result.stdout.strip() if result.returncode == 0 else None

    def inspect(self, identifier):
        raw = self.call(
            ["inspect", "--format", "{{json .State}}|{{json .Config.Labels}}|{{.Id}}", identifier],
            optional=True,
        )
        if not raw:
            return None
        state, labels, identity = raw.split("|")
        return {"state": json.loads(state), "labels": json.loads(labels), "id": identity}

    def ready(self):
        if self.call(["info", "--format", "{{.OSType}}"], optional=True) != "linux":
            raise OperationError("docker_linux_engine_unavailable_no_containers_changed")


def owned(docker, identifier, record):
    actual = docker.inspect(identifier)
    if actual and (
        actual["labels"].get(LABEL) != record["owner"]
        or actual["labels"].get(LABEL + ".instance") != record["instance"]
    ):
        raise OperationError("container_ownership_changed_no_action")
    return actual


def binding(settings):
    return {
        "source": source_fingerprint(),
        "database": database_binding(settings),
        "execution": execution_fingerprint(settings),
        "queue": settings.executor_queue,
    }


def snapshot(folder, settings):
    operations = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (PROJECT_ROOT / "backend/operations").glob("*.py")
    }
    release = (
        folder
        / "releases"
        / canonical_hash({"source": source_fingerprint(), "operations": operations})
    )
    if release.exists() and not (release / "ready.json").exists():
        raise OperationError("incomplete_source_snapshot_requires_review_no_files_deleted")
    if not release.exists():
        for name in (
            "backend/app",
            "backend/alembic",
            "backend/operations",
            "backend/reliability",
            "knowledge",
        ):
            shutil.copytree(
                PROJECT_ROOT / name,
                release / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for name in (
            "backend/uv.lock",
            "backend/alembic.ini",
            "docs/evidence/T04/model-probe.json",
        ):
            target = release / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT_ROOT / name, target)
        write_json(release / "ready.json", {"source": source_fingerprint()})
    values = settings.model_dump(mode="json")
    values.pop("demo_seed_password", None)
    for key in ("mysql_password", "redis_password", "deepseek_api_key"):
        values[key] = getattr(settings, key).get_secret_value()
    for key in ("mysql_host", "redis_host"):
        if values[key] in ("localhost", "127.0.0.1"):
            values[key] = "host.docker.internal"
    values["app_env"] = "test" if settings.app_env == "test" else "development"
    values["casegen_allow_paid_model_calls"] = False
    values["judge_allow_paid_model_calls"] = False
    path = folder / "runtime.env"
    lines = []
    for key, value in values.items():
        value = str(value).lower() if isinstance(value, bool) else str(value)
        if "\n" in value or "\r" in value:
            raise OperationError("multiline_environment_value_not_supported")
        lines.append(f"{key.upper()}={value}")
    path.write_bytes(("\n".join(lines) + "\n").encode())
    path.chmod(0o600)
    return release, path


def check_image(docker, image):
    code = (
        "import json,hashlib;from pathlib import Path;from importlib.metadata import version;"
        "print(json.dumps({'versions':{p:version(p) for p in "
        + repr(PACKAGES)
        + "},'lock':hashlib.sha256(Path('uv.lock').read_text().encode()).hexdigest()}))"
    )
    raw = docker.call(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--label",
            LABEL + "=runtime-probe",
            image,
            "python",
            "-c",
            code,
        ],
        timeout=30,
    )
    expected = {
        "versions": {p: version(p) for p in PACKAGES},
        "lock": hashlib.sha256(
            (PROJECT_ROOT / "backend/uv.lock").read_text(encoding="utf-8").encode()
        ).hexdigest(),
    }
    if json.loads(raw) != expected:
        raise OperationError("installed_runtime_versions_do_not_match_no_install_attempted")


def create_container(docker, role, record, release, env_file, image):
    name = f"ac-daily-{record['owner'][:10]}-{role}"
    if docker.inspect(name):
        raise OperationError("container_name_in_use_no_existing_container_changed")
    args = [
        "create",
        "--name",
        name,
        "--label",
        LABEL + "=" + record["owner"],
        "--label",
        LABEL + ".instance=" + record["instance"],
        "--restart",
        "no",
        "--env-file",
        str(env_file),
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m",
        "--log-opt",
        "max-size=5m",
        "--log-opt",
        "max-file=2",
    ]
    for source, target in (
        (release / "backend/app", "backend/app"),
        (release / "backend/alembic", "backend/alembic"),
        (release / "backend/operations", "backend/operations"),
        (release / "backend/reliability", "backend/reliability"),
        (release / "knowledge", "knowledge"),
        (release / "backend/uv.lock", "backend/uv.lock"),
        (release / "backend/alembic.ini", "backend/alembic.ini"),
        (release / "docs/evidence/T04/model-probe.json", "docs/evidence/T04/model-probe.json"),
    ):
        args += [
            "--mount",
            f"type=bind,source={source},target=/workspace/AgentChaos/{target},readonly",
        ]
    for source, target in (
        (folder_path(record) / "data", ".local"),
        (PROJECT_ROOT / ".cache/jieba", ".cache/jieba"),
    ):
        source.mkdir(parents=True, exist_ok=True)
        args += ["--mount", f"type=bind,source={source},target=/workspace/AgentChaos/{target}"]
    entrypoint = (
        "operations.control_worker:celery_app"
        if record.get("control")
        else "app.workers.celery_app:celery_app"
    )
    if record.get("control"):
        args += ["--env", "AC_T17_SIGNALS=/workspace/AgentChaos/.local/signals"]
    command = (
        [
            "celery",
            "-A",
            entrypoint,
            "worker",
            "--loglevel=WARNING",
            "--concurrency=1",
            "--hostname",
            "ac-daily@%h",
        ]
        if role == "worker"
        else ["python", "-m", "app.executors.dispatch"]
    )
    return docker.call([*args, image, *command])


def folder_path(record):
    return PROJECT_ROOT / ".local/workers" / record["profile"]


def start(folder, profile, config, image, docker, *, control=False):
    with lock(folder):
        docker.ready()
        current = load(folder)
        settings = Settings(_env_file=config)
        observed = binding(settings)
        if current.get("desired") == "running" and time.time() - current.get("heartbeat", 0) < 15:
            if current.get("binding") != observed or bool(current.get("control")) != control:
                raise OperationError("running_configuration_changed_stop_then_start_explicitly")
            return status(folder, docker)
        if current.get("containers"):
            for identifier in current["containers"].values():
                if owned(docker, identifier, current):
                    raise OperationError("previous_owned_containers_exist_use_stop_then_start")
        if control and not (
            settings.mysql_database.startswith("agentchaos_t17h_verify_")
            and not settings.deepseek_api_key.get_secret_value()
            and not any(
                (
                    settings.allow_paid_model_calls,
                    settings.executor_allow_paid_model_calls,
                    settings.casegen_allow_paid_model_calls,
                    settings.judge_allow_paid_model_calls,
                )
            )
        ):
            raise OperationError(
                "offline_control_requires_owned_T17H_database_no_key_no_paid_switches"
            )
        check_image(docker, image)
        release, env_file = snapshot(folder, settings)
        owner = hashlib.sha256((str(PROJECT_ROOT.resolve()) + profile).encode()).hexdigest()
        record = {
            "schema": "T17H-1",
            "owner": owner,
            "instance": uuid4().hex,
            "profile": profile,
            "desired": "running",
            "phase": "starting",
            "binding": observed,
            "config": str(config),
            "heartbeat": time.time(),
            "containers": {},
            "restarts": {},
            "image": image,
            "last_error": None,
            "control": control,
        }
        try:
            for role in ("worker", "dispatcher"):
                identifier = create_container(docker, role, record, release, env_file, image)
                record["containers"][role] = identifier
                write_json(folder / "state.json", record)
            for identifier in record["containers"].values():
                docker.call(["start", identifier])
            record["phase"] = "running"
            write_json(folder / "state.json", record)
            with (folder / "supervisor.log").open("ab") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "operations.workers",
                        "serve",
                        "--profile",
                        profile,
                        "--instance",
                        record["instance"],
                    ],
                    cwd=PROJECT_ROOT / "backend",
                    stdout=log,
                    stderr=log,
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
                    if os.name == "nt"
                    else 0,
                    start_new_session=os.name != "nt",
                )
            record["supervisor_pid"] = process.pid
            write_json(folder / "state.json", record)
        except Exception:
            record["desired"], record["phase"] = "stopped", "startup_failed"
            write_json(folder / "state.json", record)
            for identifier in record["containers"].values():
                if owned(docker, identifier, record):
                    docker.call(["rm", "--force", identifier], optional=True)
            raise
    return status(folder, docker)


def stop(folder, docker):
    with lock(folder):
        record = load(folder)
        if not record:
            return {"desired": "stopped", "phase": "not_started"}
        record["desired"], record["phase"] = "stopped", "stopping"
        write_json(folder / "state.json", record)
        if record["containers"]:
            docker.ready()
        for identifier in record["containers"].values():
            if owned(docker, identifier, record):
                docker.call(["stop", "--time", "15", identifier], timeout=25)
                docker.call(["rm", identifier])
        record["phase"], record["containers"] = "stopped", {}
        write_json(folder / "state.json", record)
    return {"desired": "stopped", "phase": "stopped"}


def status(folder, docker):
    record = load(folder)
    if not record:
        return {"desired": "stopped", "phase": "not_started", "containers": {}}
    containers = {}
    if record.get("containers"):
        docker.ready()
    for role, identifier in record.get("containers", {}).items():
        actual = owned(docker, identifier, record)
        containers[role] = {
            "state": actual["state"]["Status"] if actual else "missing",
            "exit_code": actual["state"].get("ExitCode") if actual else None,
            "restarts": record.get("restarts", {}).get(role, 0),
        }
    return {
        "desired": record["desired"],
        "phase": record["phase"],
        "supervisor_observed": record["desired"] == "running"
        and time.time() - record.get("heartbeat", 0) < 15,
        "containers": containers,
        "last_error": record.get("last_error"),
        "boundary": "Container running is not execution readiness. "
        "Verify source/database/queue replies in environment page; "
        "paid calls require their original authorization.",
    }


def serve(folder, instance, docker):
    while True:
        with lock(folder):
            record = load(folder)
            if record.get("instance") != instance or record.get("desired") != "running":
                return
            record["heartbeat"] = time.time()
            for role, identifier in record["containers"].items():
                try:
                    docker.ready()
                    actual = owned(docker, identifier, record)
                    if not actual:
                        record["phase"], record["last_error"] = "failed", "owned_container_missing"
                        continue
                    if actual["state"]["Running"]:
                        continue
                    count = record["restarts"].get(role, 0)
                    if count >= len(RETRY_DELAYS):
                        record["phase"], record["last_error"] = (
                            "failed",
                            role + "_restart_limit_reached",
                        )
                        continue
                    retry_at = record.setdefault("retry_at", {}).setdefault(
                        role, time.time() + RETRY_DELAYS[count]
                    )
                    record["phase"] = "restarting"
                    if time.time() >= retry_at:
                        docker.call(["start", identifier])
                        record["restarts"][role] = count + 1
                        record["retry_at"].pop(role)
                        record["phase"] = "running"
                except OperationError as error:
                    record["phase"], record["last_error"] = "unknown", str(error)
            write_json(folder / "state.json", record)
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "logs", "serve"))
    parser.add_argument("--profile", default="main")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--image", default="agentchaos-t07:dev")
    parser.add_argument("--instance", default="")
    parser.add_argument("--offline-control", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        parser.error("Invalid local profile")
    folder = PROJECT_ROOT / ".local/workers" / args.profile
    docker = Docker()
    try:
        if args.action == "serve":
            serve(folder, args.instance, docker)
            return
        if args.action == "start":
            if args.config is None and args.profile != "main":
                previous = load(folder)
                config = Path(previous.get("config", "")).resolve()
                if (
                    not config.is_relative_to((PROJECT_ROOT / ".local").resolve())
                    or not config.is_file()
                ):
                    raise OperationError(
                        "non_main_profile_requires_existing_owned_local_configuration"
                    )
                observed = binding(Settings(_env_file=config))
                if any(
                    observed[k] != previous.get("binding", {}).get(k)
                    for k in ("database", "queue", "execution")
                ):
                    raise OperationError(
                        "cached_profile_binding_changed_supply_explicit_configuration"
                    )
                args.config = config
                args.offline_control = bool(previous.get("control"))
            config = args.config or PROJECT_ROOT / ".env"
            result = start(
                folder,
                args.profile,
                config.resolve(),
                args.image,
                docker,
                control=args.offline_control,
            )
        elif args.action == "stop":
            result = stop(folder, docker)
        elif args.action == "logs":
            # Never echo arbitrary provider/tool logs or Docker environment. Local
            # operators can inspect raw logs explicitly using their Docker access.
            result = status(folder, docker)
            result["log_commands"] = [
                f"& '{DOCKER}' --config '{docker.config}' --host '{ENDPOINT}' logs --tail 50 {i}"
                for i in load(folder).get("containers", {}).values()
            ]
        else:
            result = status(folder, docker)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "code": str(error)
                    if isinstance(error, OperationError)
                    else type(error).__name__,
                    "no_install_or_budget_reset": True,
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

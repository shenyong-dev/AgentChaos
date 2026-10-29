"""Bounded owned API/UI processes; keep formal SQL/evidence/ledgers when view closes."""

import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

from experiments.common import EVIDENCE, LOCAL, ROOT, read, write_new


def main():
    for port in (18016, 5183):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    marker = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    local = LOCAL / ("review-" + marker)
    local.mkdir()
    stop = local / "stop"
    expires = datetime.now(UTC) + timedelta(hours=6)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "backend"),
        "PYTHONIOENCODING": "utf-8",
        "AC_API_PROXY_TARGET": "http://127.0.0.1:18016",
        "TEMP": str(ROOT / ".cache/tmp"),
        "TMP": str(ROOT / ".cache/tmp"),
        "DEEPSEEK_API_KEY": "",
        "ALLOW_PAID_MODEL_CALLS": "false",
        "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
        "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
        "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
    }
    processes = []

    def start(command, directory, name):
        with (local / (name + ".log")).open("x", encoding="utf-8") as file:
            process = subprocess.Popen(
                command,
                cwd=directory,
                env=env,
                stdout=file,
                stderr=file,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        processes.append(process)
        return process.pid

    try:
        api = start(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "experiments.review_api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "18016",
            ],
            ROOT,
            "api",
        )
        ui = start(
            [
                "D:/node.js/node.exe",
                "node_modules/vite/bin/vite.js",
                "--host",
                "127.0.0.1",
                "--port",
                "5183",
                "--strictPort",
            ],
            ROOT / "frontend",
            "ui",
        )
        for _ in range(100):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("Owned review process exited during startup; inspect local logs")
            try:
                if (
                    httpx.get("http://127.0.0.1:18016/api/v1/judging/access", timeout=2).status_code
                    == 401
                    and httpx.get("http://127.0.0.1:5183", timeout=2).status_code == 200
                ):
                    break
            except httpx.RequestError:
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("Review startup timeout")
        batch = read(EVIDENCE / "human-batches/C.json")["batch_id"]
        record = {
            "ui": "http://127.0.0.1:5183/judging/" + batch,
            "api": "http://127.0.0.1:18016",
            "api_pid": api,
            "ui_pid": ui,
            "stop_file": str(stop),
            "expires_at": expires.isoformat(),
            "label_only": True,
            "paid_calls_enabled": False,
            "database_preserved_when_view_closes": True,
        }
        (LOCAL / "latest-review.json").write_text(
            __import__("json").dumps(record, indent=2), encoding="utf-8"
        )
        write_new(local / "session.json", record)
        print(__import__("json").dumps(record), flush=True)
        while not stop.exists() and datetime.now(UTC) < expires:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("Review process stopped; preserve data and report interruption")
            time.sleep(1)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        write_new(
            local / "view-cleanup.json",
            {
                "stopped_at": datetime.now(UTC).isoformat(),
                "owned_processes_stopped": True,
                "SQL_evidence_ledgers_preserved": True,
            },
        )


if __name__ == "__main__":
    main()

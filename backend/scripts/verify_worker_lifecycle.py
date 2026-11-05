"""Crash only the owned T17H free-control Worker; never operate the main queue."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime

from app.core.config import PROJECT_ROOT, Settings
from app.services.readiness import worker_check
from operations.workers import Docker, load, owned
from scripts.prepare_sessions import observe


def main():
    root = PROJECT_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="worker-lifecycle.json")
    args = parser.parse_args()
    assert args.output.endswith(".json") and "/" not in args.output and "\\" not in args.output
    output = root / "docs/evidence/T17H" / args.output
    assert not output.exists(), "Preserve earlier lifecycle evidence"
    info = json.loads((root / ".local/t17h/latest-review.json").read_bytes())
    profile = info["profile"]
    assert profile.startswith("t17h-") and info["database"].startswith("agentchaos_t17h_verify_")
    folder = root / ".local/workers" / profile
    settings = Settings(_env_file=info["config_file"])
    assert settings.mysql_database == info["database"]
    assert (
        not settings.deepseek_api_key.get_secret_value()
        and not settings.executor_allow_paid_model_calls
    )
    baseline = observe()
    docker = Docker()
    results = {}

    def cli(action):
        command = [sys.executable, "-m", "operations.workers", action, "--profile", profile]
        if action == "start":
            command += ["--config", info["config_file"], "--offline-control"]
        result = subprocess.run(
            command,
            cwd=root / "backend",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stdout
        return json.loads(result.stdout)

    def wait(predicate, seconds=45):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(0.4)
        raise AssertionError("Owned lifecycle condition not reached")

    before = load(folder)
    cli("start")
    after = load(folder)
    assert before["instance"] == after["instance"] and before["containers"] == after["containers"]
    results["idempotent_start"] = True
    results["cli_launcher_exited_background_continues"] = all(
        owned(docker, identifier, after)["state"]["Running"]
        for identifier in after["containers"].values()
    )
    wait(lambda: worker_check(settings).status == "ready")
    results["actual_wrong_queue_blocked"] = (
        worker_check(
            settings.model_copy(update={"executor_queue": settings.executor_queue + "-wrong"})
        ).status
        != "ready"
    )
    results["actual_wrong_execution_configuration_blocked"] = (
        worker_check(
            settings.model_copy(
                update={"agent_max_output_tokens": settings.agent_max_output_tokens + 1}
            )
        ).status
        != "ready"
    )
    results["crashes"] = []
    try:
        for number in range(1, 5):
            current = load(folder)
            identifier = current["containers"]["worker"]
            assert owned(docker, identifier, current)["state"]["Running"]
            docker.call(["kill", identifier])
            if number <= 3:
                wait(
                    lambda expected=number: (
                        load(folder).get("restarts", {}).get("worker", 0) == expected
                    )
                )
                wait(lambda: worker_check(settings).status == "ready")
                results["crashes"].append(
                    {"injection": number, "restarted": True, "matching_live_reply": True}
                )
            else:
                wait(lambda: load(folder).get("phase") == "failed")
                assert load(folder)["restarts"]["worker"] == 3
                results["crashes"].append(
                    {"injection": number, "restarted": False, "limit_reported": True}
                )
        cli("stop")
        stopped = load(folder)
        assert stopped["desired"] == "stopped" and not stopped["containers"]
        old_instance = stopped["instance"]
        time.sleep(4)
        assert load(folder)["instance"] == old_instance and load(folder)["desired"] == "stopped"
        results["manual_stop_stays_stopped"] = True
        assert cli("stop")["phase"] == "stopped"
        results["repeated_stop_safe"] = True
    finally:
        if load(folder).get("desired") != "running":
            cli("start")
        elif load(folder).get("phase") == "failed":
            cli("stop")
            cli("start")
    wait(lambda: worker_check(settings).status == "ready")
    final = observe()
    assert final["tables"] == baseline["tables"] and final["ledgers"] == baseline["ledgers"]
    results.update(
        checked_at=datetime.now(UTC).isoformat(),
        new_model_calls=0,
        main_tables_and_14_ledgers_unchanged=True,
        boundary="Actual isolated Docker process kills and bounded restart; "
        "launcher exit verified, Codex app itself was not closed.",
    )
    output.write_bytes((json.dumps(results, ensure_ascii=False, indent=2) + "\n").encode())
    print(
        json.dumps(
            {"verified": True, "crash_injections": 4, "automatic_restarts": 3, "new_model_calls": 0}
        )
    )


if __name__ == "__main__":
    main()

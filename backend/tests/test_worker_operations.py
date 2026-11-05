"""Local supervisor safety: bounded restart, stale ownership and unavailable engine."""

import json

import pytest

from operations import workers as w


class FakeDocker:
    def __init__(self, record, *, available=True):
        self.record, self.available = record, available
        self.commands, self.present, self.running = [], True, False

    def ready(self):
        if not self.available:
            raise w.OperationError("controlled_engine_unavailable")

    def inspect(self, identifier):
        if not self.present:
            return None
        return {
            "id": identifier,
            "labels": {
                w.LABEL: self.record["owner"],
                w.LABEL + ".instance": self.record["instance"],
            },
            "state": {
                "Status": "running" if self.running else "exited",
                "Running": self.running,
                "ExitCode": 1,
            },
        }

    def call(self, args, **kwargs):
        self.commands.append(args)
        if args[0] == "rm":
            self.present = False
        return "controlled"


def state():
    return {
        "owner": "owned",
        "instance": "current",
        "desired": "running",
        "phase": "running",
        "heartbeat": 0,
        "containers": {"worker": "owned-container"},
        "restarts": {},
    }


def test_engine_unavailable_stop_preserves_container_identity_for_a_later_stop(tmp_path):
    record = state()
    w.write_json(tmp_path / "state.json", record)
    docker = FakeDocker(record, available=False)
    with pytest.raises(w.OperationError):
        w.stop(tmp_path, docker)
    saved = w.load(tmp_path)
    assert saved["desired"] == "stopped" and saved["containers"] == record["containers"]
    assert docker.commands == []
    docker.available = True
    assert w.stop(tmp_path, docker)["phase"] == "stopped"
    assert docker.commands == [
        ["stop", "--time", "15", "owned-container"],
        ["rm", "owned-container"],
    ]
    assert w.load(tmp_path)["containers"] == {}


def test_changed_container_owner_is_never_stopped_or_removed(tmp_path):
    record = state()
    w.write_json(tmp_path / "state.json", record)
    docker = FakeDocker({**record, "owner": "another-project"})
    with pytest.raises(w.OperationError, match="ownership"):
        w.stop(tmp_path, docker)
    assert docker.commands == []
    assert w.load(tmp_path)["containers"] == record["containers"]


def test_repeated_crash_has_three_delayed_retries_then_requires_intervention(tmp_path, monkeypatch):
    record = state()
    w.write_json(tmp_path / "state.json", record)
    docker = FakeDocker(record)
    clock = [0]

    class Done(Exception):
        pass

    def sleep(_):
        clock[0] += 2
        if clock[0] > 30:
            raise Done

    monkeypatch.setattr(w.time, "time", lambda: clock[0])
    monkeypatch.setattr(w.time, "sleep", sleep)
    with pytest.raises(Done):
        w.serve(tmp_path, record["instance"], docker)
    saved = w.load(tmp_path)
    assert docker.commands == [["start", "owned-container"]] * 3
    assert saved["phase"] == "failed" and saved["last_error"] == "worker_restart_limit_reached"
    assert saved["restarts"]["worker"] == 3


def test_old_supervisor_and_explicit_stopped_state_cannot_restart_anything(tmp_path):
    record = state()
    docker = FakeDocker(record)
    w.write_json(tmp_path / "state.json", record)
    w.serve(tmp_path, "old-instance", docker)
    w.write_json(tmp_path / "state.json", {**record, "desired": "stopped"})
    w.serve(tmp_path, record["instance"], docker)
    assert docker.commands == []


def test_public_status_does_not_echo_configuration_secrets(tmp_path):
    record = {**state(), "config": "private-config", "arbitrary_secret": "must-never-appear"}
    w.write_json(tmp_path / "state.json", record)
    result = json.dumps(w.status(tmp_path, FakeDocker(record)))
    assert "must-never-appear" not in result and "private-config" not in result

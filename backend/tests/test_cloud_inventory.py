"""Synthetic /proc controls; never a claim about actual cloud capacity or deployment."""

import importlib.util
import json
from collections import namedtuple
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "deploy/inspect_host.py"
spec = importlib.util.spec_from_file_location("cloud_inventory", MODULE)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture
def host(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "meminfo").write_text(
        "MemTotal: 2097152 kB\nMemAvailable: 131072 kB\nSwapTotal: 0 kB\n", encoding="utf-8"
    )
    release = tmp_path / "os-release"
    release.write_text('ID="alinux"\nVERSION_ID="4"\nSECRET=never-export\n', encoding="utf-8")
    disk = namedtuple("Disk", "total used free")
    monkeypatch.setattr(
        inventory.shutil, "disk_usage", lambda _: disk(40 << 30, 20 << 30, 20 << 30)
    )
    return proc, release, tmp_path


def test_read_only_capacity_reports_missing_observations_and_does_not_approve_deployment(host):
    proc, release, root = host
    report = inventory.collect(proc, release, root)
    assert report["memory"]["total_bytes"] == 2 * 1024**3
    assert report["memory"]["available_bytes"] == 128 * 1024**2
    assert report["memory"]["swap_free_bytes"] is None
    assert report["listeners"]["tables_read"] == 0
    assert report["decision"] == "needs_review_not_deployment_approval"
    assert "never-export" not in json.dumps(report)
    assert any("256 MiB" in x for x in report["findings"])


def test_port_inventory_distinguishes_loopback_ipv4_and_ipv6_without_exporting_addresses(host):
    proc, _, _ = host
    (proc / "net/tcp").write_text(
        "header\n0: 0100007F:0CEA 00000000:0000 0A\n"
        "1: 00000000:1F40 00000000:0000 0A\n"
        "2: 0100007F:0016 00000000:0000 01\n3: broken\n",
        encoding="utf-8",
    )
    (proc / "net/tcp6").write_text(
        "header\n0: 00000000000000000000000001000000:18EB 0:0000 0A\n", encoding="utf-8"
    )
    result = inventory.listeners(proc)
    assert result["tables_read"] == 2
    assert {tuple(r.values()) for r in result["observations"]} == {
        (3306, "tcp", "loopback"),
        (8000, "tcp", "wildcard"),
        (6379, "tcp6", "loopback"),
    }
    assert "0100007F" not in json.dumps(result)


def test_process_partial_pss_is_explicit_and_does_not_read_commandline_or_environment(host):
    proc, _, _ = host
    for pid in ("101", "102"):
        p = proc / pid
        p.mkdir()
        (p / "comm").write_text("nginx\n", encoding="utf-8")
        (p / "status").write_text("VmRSS: 10 kB\n", encoding="utf-8")
        (p / "cmdline").write_text("secret-argument-do-not-export", encoding="utf-8")
        (p / "environ").write_text("SECRET=do-not-export", encoding="utf-8")
    (proc / "101/smaps_rollup").write_text("Pss: 5 kB\n", encoding="utf-8")
    result = inventory.process_inventory(proc)
    assert result["groups"][0]["observed_processes"] == 2
    assert result["groups"][0]["pss_read"] == 1
    assert result["groups"][0]["pss_bytes_observed"] == 5120
    assert "do-not-export" not in json.dumps(result)


def test_non_linux_invocation_fails_closed_without_collecting(monkeypatch, capsys):
    monkeypatch.setattr(inventory.sys, "platform", "win32")
    monkeypatch.setattr(inventory, "collect", lambda: pytest.fail("Must not probe local Windows"))
    assert inventory.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported_host"


def test_missing_memavailable_is_unknown_not_zero_or_healthy(host):
    proc, release, root = host
    (proc / "meminfo").write_text("MemTotal: 2097152 kB\n", encoding="utf-8")
    value = inventory.collect(proc, release, root)
    assert value["memory"]["available_bytes"] is None
    assert any("无法确认可用内存" in line for line in value["findings"])

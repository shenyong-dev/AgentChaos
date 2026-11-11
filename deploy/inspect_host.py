"""Read-only Linux capacity inventory. Standard library only; no network or service actions."""

import hashlib
import ipaddress
import json
import os
import platform
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

VERSION = "t22a-host-inventory-1"
MIB = 1024 * 1024
PORTS = {22, 80, 443, 3306, 6379, 16380, 8000, 5173}
SERVICES = {
    "nginx",
    "caddy",
    "apache2",
    "httpd",
    "mysqld",
    "mariadbd",
    "redis-server",
    "redis-stack-ser",
    "dockerd",
    "containerd",
    "uvicorn",
    "gunicorn",
    "celery",
}


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def memory_fields(text):
    values = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1].isdigit() and parts[2] == "kB":
            values[parts[0].rstrip(":")] = int(parts[1]) * 1024
    return values


def listeners(proc):
    result, read_tables = [], 0
    for name in ("tcp", "tcp6"):
        content = read(proc / "net" / name)
        if content is None:
            continue
        read_tables += 1
        for line in content.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":
                continue
            try:
                address, port = fields[1].split(":")
                port = int(port, 16)
                if port not in PORTS:
                    continue
                raw = bytes.fromhex(address)
                raw = b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))
                ip = ipaddress.ip_address(raw)
                bind = (
                    "loopback"
                    if ip.is_loopback
                    else "wildcard"
                    if ip.is_unspecified
                    else "other"
                )
                item = {"port": port, "family": name, "bind": bind}
                if item not in result:
                    result.append(item)
            except (ValueError, IndexError):
                continue
    return {
        "observations": sorted(
            result, key=lambda x: (x["port"], x["family"], x["bind"])
        ),
        "tables_read": read_tables,
        "boundary": "仅当前网络命名空间的绑定观察；未验证公网可达、安全组或主机防火墙。",
    }


def process_inventory(proc):
    groups = {}
    try:
        entries = list(proc.iterdir())
    except OSError:
        return {"groups": [], "boundary": "无法读取进程目录；不表示没有服务。"}
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        name = (read(entry / "comm") or "").strip()
        if name not in SERVICES:
            continue
        group = groups.setdefault(
            name,
            {
                "name": name,
                "observed_processes": 0,
                "rss_bytes_observed": 0,
                "rss_read": 0,
                "pss_bytes_observed": 0,
                "pss_read": 0,
            },
        )
        group["observed_processes"] += 1
        for filename, key, field in (
            ("status", "rss", "VmRSS"),
            ("smaps_rollup", "pss", "Pss"),
        ):
            value = memory_fields(read(entry / filename)).get(field)
            if value is not None:
                group[key + "_bytes_observed"] += value
                group[key + "_read"] += 1
    return {
        "groups": [groups[k] for k in sorted(groups)],
        "boundary": "仅列常见服务名，不读取命令行/环境/配置。RSS有共享页，不能相加证明容量；"
        "PSS可能因权限不完整。未列出不代表未安装或没有其他业务。",
    }


def collect(
    proc=Path("/proc"), os_release=Path("/etc/os-release"), disk_path=Path("/")
):
    release = {}
    for line in (read(os_release) or "").splitlines():
        key, _, value = line.partition("=")
        if key in {"ID", "VERSION_ID"}:
            release[key] = value.strip("\"'")[:80]
    memory = memory_fields(read(proc / "meminfo"))
    disk = shutil.disk_usage(disk_path)
    observations = listeners(proc)
    available = memory.get("MemAvailable")
    findings = []
    if available is None:
        findings.append("无法确认可用内存，需补充盘点。")
    elif available < 256 * MIB:
        findings.append("可用内存低于规划的256 MiB观察线，先处理现有负载。")
    if any(
        x["port"] in {3306, 6379, 16380, 8000, 5173} and x["bind"] != "loopback"
        for x in observations["observations"]
    ):
        findings.append(
            "观察到内部服务绑定非回环地址；需结合现有业务核对网络规则，不自动更改。"
        )
    return {
        "schema": VERSION,
        "at_utc": datetime.now(UTC).isoformat(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if globals().get("__file__") and Path(__file__).is_file()
        else None,
        "os": {
            "id": release.get("ID"),
            "version": release.get("VERSION_ID"),
            "architecture": platform.machine(),
            "logical_cpus_observed": os.cpu_count(),
        },
        "memory": {
            "total_bytes": memory.get("MemTotal"),
            "available_bytes": available,
            "swap_total_bytes": memory.get("SwapTotal"),
            "swap_free_bytes": memory.get("SwapFree"),
        },
        "root_disk": {"total_bytes": disk.total, "free_bytes": disk.free},
        "listeners": observations,
        "processes": process_inventory(proc),
        "findings": findings,
        "decision": "needs_review_not_deployment_approval",
        "unverified": [
            "已有站点/域名与证书",
            "当前用户和容器资源限制",
            "公网可达与评委网络",
            "60分钟浏览与任务峰值",
            "重启恢复和回滚",
            "文件权限与磁盘类型",
        ],
        "scope": "只读取盘点；不安装、不连接数据库或模型、不输出IP/主机名/密钥/进程参数，"
        "不创建Swap、不启停或修改服务。2 GiB全栈容量仍未证明。",
    }


def main():
    if not sys.platform.startswith("linux"):
        print(
            json.dumps(
                {
                    "schema": VERSION,
                    "status": "unsupported_host",
                    "message": "请在目标Linux服务器运行；本机Windows不是云端盘点。",
                },
                ensure_ascii=False,
            )
        )
        return 2
    try:
        report = collect()
    except OSError:
        print(
            json.dumps(
                {
                    "schema": VERSION,
                    "status": "inventory_unavailable",
                    "message": "部分系统观察无法读取；未执行任何变更。",
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

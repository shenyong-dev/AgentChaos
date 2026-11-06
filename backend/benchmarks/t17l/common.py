"""Immutable inputs and strict ownership checks for this opt-in CLI only."""

import hashlib
import json
import re
import shutil
from pathlib import Path

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT

AUTHORIZATION = "T17L-20261106-agent-1"
LEDGER = f"ac:t17l:budget:{AUTHORIZATION}:agent"
CATALOG_PATH = PROJECT_ROOT / "docs/fixtures/T17L/catalog.json"


def catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def source_fingerprint():
    paths = sorted(Path(__file__).parent.glob("*.py"))
    return canonical_hash({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def guard(engine, settings=None):
    database = engine.url.database or ""
    if not re.fullmatch(r"agentchaos_t17l_verify_[a-f0-9]{12}(?:_restore)?", database):
        raise RuntimeError("T17L refuses any database outside its exact disposable namespace")
    if settings and (
        settings.mysql_database != database
        or not re.fullmatch(r"ac:t17l:[a-f0-9]{12}", settings.executor_namespace)
    ):
        raise RuntimeError("T17L database/Redis namespace mismatch")


def disk_guard():
    if shutil.disk_usage("C:/").free < 2_000_000_000:
        raise RuntimeError("C drive below 2GB: stop additional SQL/model work")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

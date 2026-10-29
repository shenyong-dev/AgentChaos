"""Create an isolated formal database once, preserve IDs, and archive provenance."""

import json
import secrets
import shutil
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

from alembic.config import Config
from redis import Redis
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import Settings
from app.db.engine import create_database_engine
from app.models import (
    Artifact,
    FaultProfile,
    KnowledgeDocument,
    Target,
    TargetVersion,
    TestCase,
    User,
)
from experiments.budget import AGENT_AUTH, AGENT_CAP, JUDGE_AUTH, JUDGE_CAP
from experiments.common import EVIDENCE, LOCAL, ROOT, read, sha, write_new


def main():
    if (LOCAL / "session-private.json").exists():
        raise SystemExit("Already prepared. Reuse this session; do not recreate databases or caps.")
    assert shutil.disk_usage("C:/").free > 2_000_000_000
    s = Settings()
    assert not any(
        (
            s.allow_paid_model_calls,
            s.executor_allow_paid_model_calls,
            s.judge_allow_paid_model_calls,
            s.casegen_allow_paid_model_calls,
        )
    )
    auth = read(ROOT / "docs/fixtures/T16/authorization.json")
    assert auth["role_caps_microusd"] == {
        "agent": AGENT_CAP,
        "judge": JUDGE_CAP,
        "case_generator": 0,
    }
    baseline = read(EVIDENCE / "preparation.json")
    r = Redis.from_url(s.redis_url)
    assert {
        k: {f.decode(): int(v) for f, v in r.hgetall(k).items()} for k in baseline["ledgers"]
    } == baseline["ledgers"]
    candidate = read(EVIDENCE / "v1-candidate-manifest.json")
    assert candidate["archive_sha256"] == auth["candidate_archive_sha256"]
    sources = {"V0": ROOT / candidate["parent"], "V1": ROOT / candidate["archive"]}
    expected = {"V0": candidate["parent_sha256"], "V1": candidate["archive_sha256"]}
    snapshots = {}
    for name, archive in sources.items():
        assert sha(archive) == expected[name]
        dest = LOCAL / ("formal-" + name.lower())
        dest.mkdir(exist_ok=False)
        with zipfile.ZipFile(archive) as file:
            for member in file.namelist():
                p = (dest / member).resolve()
                assert p.is_relative_to(dest.resolve())
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(file.read(member))
        snapshots[name] = str(dest)
    marker = uuid4().hex[:12]
    database, username = "agentchaos_t16_formal_" + marker, "ac_t16_" + marker
    password = secrets.token_urlsafe(32)
    private = {
        "marker": marker,
        "snapshots": snapshots,
        "mysql": {"mysql_database": database, "mysql_user": username, "mysql_password": password},
        "database_created_at": datetime.now(UTC).isoformat(),
    }
    # Preserve precise ownership before DDL so interrupted setup is recoverable.
    write_new(LOCAL / "session-private.json", private)
    admin = create_database_engine(s, database="mysql")
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(
            f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        )
        connection.execute(
            text("CREATE USER :name@'localhost' IDENTIFIED BY :password"),
            {"name": username, "password": password},
        )
        connection.exec_driver_sql(f"GRANT ALL ON `{database}`.* TO '{username}'@'localhost'")
    engine = create_database_engine(s, database=database)
    with engine.connect() as connection:
        config = Config(str(ROOT / "backend/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    source = create_database_engine(s)
    counts = {}
    with Session(source) as session, engine.begin() as connection:
        for model in [
            User,
            Target,
            KnowledgeDocument,
            TargetVersion,
            TestCase,
            FaultProfile,
            Artifact,
        ]:
            rows = [
                {c.name: getattr(row, c.name) for c in model.__table__.columns}
                for row in session.scalars(select(model).order_by(model.id))
            ]
            if rows:
                connection.execute(insert(model.__table__), rows)
            counts[model.__tablename__] = len(rows)
    assert counts["ac_target_versions"] == 40 and counts["ac_test_cases"] == 123
    for role, identifier, cap in [
        ("agent", AGENT_AUTH, AGENT_CAP),
        ("judge", JUDGE_AUTH, JUDGE_CAP),
    ]:
        key = f"ac:t16:budget:{identifier}:{role}"
        marker_path = LOCAL / (identifier + ".initialized.json")
        if marker_path.exists() and not r.exists(key):
            raise SystemExit(
                "Ledger lost after initialization; restore audited ledger, never reset."
            )
        created = r.eval(
            "if redis.call('EXISTS',KEYS[1])==1 then return 0 end "
            "redis.call('HSET',KEYS[1],'limit',ARGV[1]) return 1",
            1,
            key,
            cap,
        )
        ledger = {k.decode(): int(v) for k, v in r.hgetall(key).items()}
        assert ledger["limit"] == cap
        if not marker_path.exists():
            write_new(
                marker_path,
                {
                    "key": key,
                    "initial": ledger,
                    "newly_initialized": bool(created),
                    "authorization_sha256": sha(ROOT / "docs/fixtures/T16/authorization.json"),
                },
            )
    write_new(
        EVIDENCE / "session.json",
        {
            "marker": marker,
            "database": database,
            "cloned_rows": counts,
            "snapshots": snapshots,
            "new_role_caps_microusd": auth["role_caps_microusd"],
            "old_ledgers_unchanged": 8,
            "source_archives": expected,
            "paid_calls": 0,
            "main_database_written": False,
        },
    )
    r.close()
    engine.dispose()
    source.dispose()
    admin.dispose()
    print(
        json.dumps(
            {
                "marker": marker,
                "versions_cloned": 40,
                "cases_cloned": 123,
                "paid_calls": 0,
                "role_ledgers": 2,
            }
        )
    )


if __name__ == "__main__":
    main()

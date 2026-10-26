"""MySQL fencing. All locks use run -> execution order; database time is authoritative."""

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.models import TestExecution, TestRun

TERMINAL = {"passed", "failed", "inconclusive", "error", "cancelled", "skipped"}


def database_now(session):
    return session.scalar(text("SELECT UTC_TIMESTAMP(6)")).replace(tzinfo=UTC)


@contextmanager
def advisory(engine, name):
    key = "ac:t05:" + hashlib.sha256(f"{engine.url.database}:{name}".encode()).hexdigest()[:48]
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT GET_LOCK(:key, 3)"), {"key": key}) == 1
        connection.commit()
        if not acquired:
            raise AgentError("executor_busy", "执行协调锁繁忙，请稍后重试。", 503)
        try:
            yield connection
        finally:
            if connection.in_transaction():
                connection.rollback()
            connection.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})
            connection.commit()


def locked_pair(session, execution_id):
    run_id = session.scalar(select(TestExecution.run_id).where(TestExecution.id == execution_id))
    if run_id is None:
        raise AgentError("execution_not_found", "找不到执行。", 404)
    run = session.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
    execution = session.scalar(
        select(TestExecution).where(TestExecution.id == execution_id).with_for_update()
    )
    return run, execution


def fence(session, execution_id, token, *, scope_id=None, session_id=None):
    run, execution = locked_pair(session, execution_id)
    now = database_now(session)
    if run.status == "cancelling" or execution.status == "cancelled":
        raise AgentError("execution_cancelled", "运行已请求取消。", 409)
    if (
        run.status != "running"
        or execution.status != "running"
        or not token
        or token != execution.lease_token
        or not execution.lease_until
        or execution.lease_until <= now
        or (scope_id is not None and execution.scope_id != scope_id)
        or (session_id is not None and execution.session_id != session_id)
    ):
        raise AgentError("lease_lost", "执行租约已失效，拒绝旧Worker继续写入。", 409)
    deadline = (execution.timing_json or {}).get("deadline")
    if deadline and now >= datetime.fromisoformat(deadline):
        raise AgentError("execution_timeout", "执行达到总时限。", 429)
    return run, execution


@dataclass(frozen=True)
class ExecutionControl:
    engine: object
    execution_id: int
    token: str

    @contextmanager
    def transaction(self):
        with Session(self.engine) as session, session.begin():
            run, execution = fence(session, self.execution_id, self.token)
            yield session, run, execution
            # A blocking tool/storage operation must not outlive its deadline/lease.
            fence(session, self.execution_id, self.token)

    def check(self):
        with self.transaction():
            pass

    def step(self, kind):
        with self.transaction() as (_, run, execution):
            timing = dict(execution.timing_json or {})
            steps = timing.get("steps", 0) + 1
            if steps > run.manifest_json["limits"]["max_steps"]:
                raise AgentError("execution_step_limit", "执行步骤达到上限。", 429)
            timing.update(steps=steps, last_step=kind)
            execution.timing_json = timing

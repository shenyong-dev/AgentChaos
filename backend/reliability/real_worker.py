"""Opt-in real-provider lab; only ledger transport and observable pauses differ.

Production runner, ModelGateway, token fencing and tools are unchanged. The
authorization ledger uses the persistent main Redis; checkpoints/broker remain
on the disposable Redis. A pause follows an actually received provider chunk.
"""

import json
import os
import time
from pathlib import Path

from langchain_deepseek import ChatDeepSeek
from redis import Redis

from app.agents.gateway import ModelGateway
from app.executors import runner
from app.executors.budget import PLATFORM_AUTHORIZATION, ExecutorBudget
from app.workers.celery_app import celery_app as celery_app
from app.workers.celery_app import settings

assert os.environ["AC_T17_MODE"] == "real"
assert settings.executor_budget_authorization_id == PLATFORM_AUTHORIZATION
assert settings.mysql_database.startswith("agentchaos_t17_verify_")
assert not settings.casegen_allow_paid_model_calls and not settings.judge_allow_paid_model_calls

ROOT = Path(os.environ["AC_T17_SIGNALS"]).parent
active = None
original_stream = ChatDeepSeek.stream


def observable_stream(self, *args, **kwargs):
    stream = original_stream(self, *args, **kwargs)
    paused = False
    try:
        for chunk in stream:
            yield chunk
            if active and not paused:
                paused = True
                key = active["key"]
                (ROOT / "signals" / (key + ".provider-chunk.json")).write_text(
                    json.dumps(
                        {
                            "real_provider_chunk": True,
                            "content": chunk.content,
                            "usage": chunk.usage_metadata,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                end = time.monotonic() + 90
                while not (ROOT / "signals" / (key + ".release")).exists():
                    if time.monotonic() > end:
                        raise TimeoutError("T17 owned observation pause expired")
                    time.sleep(0.05)
    finally:
        stream.close()


class PersistentBudget(ExecutorBudget):
    def __init__(self, redis, settings, *args, **kwargs):
        coordinator = Redis(
            host="host.docker.internal",
            port=int(os.environ["AC_T17_LEDGER_PORT"]),
            db=int(os.environ["AC_T17_LEDGER_DB"]),
            password=settings.redis_password.get_secret_value(),
            socket_timeout=10,
        )
        super().__init__(coordinator, settings, *args, **kwargs)


class ObservableGateway(ModelGateway):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.audit_directory = ROOT / "real-calls" / self.budget.execution_key.split(":")[-1]

    def invoke(self, messages, *args, **kwargs):
        global active
        text_value = " ".join(str(item.content) for item in messages)
        key = next(
            (key for key in ("T17-LIVE-CANCEL", "T17-LIVE-PAUSE") if key in text_value), None
        )
        active = {"key": key} if key else None
        try:
            return super().invoke(messages, *args, **kwargs)
        finally:
            active = None


ChatDeepSeek.stream = observable_stream
runner.ExecutorBudget = PersistentBudget
runner.ModelGateway = ObservableGateway

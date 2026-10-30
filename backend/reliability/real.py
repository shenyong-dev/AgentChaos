"""Bounded approved T17 live smoke, concurrency and cancellation/interruption."""

import hashlib
import json
import time
from datetime import UTC, datetime

from redis import Redis

from app.agents.runtime import AgentRuntime
from app.executors.budget import PLATFORM_AUTHORIZATION
from app.executors.service import RunService
from reliability.lab import Lab

AUTH_KEY = f"ac:t05:budget:{PLATFORM_AUTHORIZATION}:agent"


class RealLab(Lab):
    worker_module = "reliability.real_worker:celery_app"
    case_source = "explicit_real_provider_development_not_human_formal_dataset"

    def __init__(self):
        super().__init__()
        target = self.output.with_name("real-" + self.marker)
        self.output.rename(target)
        self.output = target

    def enable(self):
        self.settings = self.settings.model_copy(
            update={
                "deepseek_api_key": self.base.deepseek_api_key,
                "executor_allow_paid_model_calls": True,
                "executor_budget_authorization_id": PLATFORM_AUTHORIZATION,
                "executor_agent_budget_usd": 1.0,
            }
        )
        assert self.settings.deepseek_api_key.get_secret_value()
        # Register after the exact roles/source are bound, before creating runs.
        self.version = AgentRuntime(
            self.engine, self.redis, self.settings, auto_register=True
        ).version_id
        self.service = RunService(self.engine, self.settings)
        self.worker_env.update(
            {
                "AC_T17_MODE": "real",
                "DEEPSEEK_API_KEY": self.base.deepseek_api_key.get_secret_value(),
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "true",
                "EXECUTOR_BUDGET_AUTHORIZATION_ID": PLATFORM_AUTHORIZATION,
                "EXECUTOR_AGENT_BUDGET_USD": "1",
                "AC_T17_LEDGER_PORT": str(self.base.redis_port),
                "AC_T17_LEDGER_DB": str(self.base.redis_db),
            }
        )
        self.env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in self.worker_env.items()) + "\n", encoding="utf-8"
        )
        # API remains free and has no model key; only these owned workers can call.
        self.coordinator = Redis.from_url(self.base.redis_url)
        assert self.coordinator.exists(AUTH_KEY), "Initialize approved ledger once first"
        self.before = self.ledger()
        self.save(
            "live-binding.json",
            {
                "authorization": PLATFORM_AUTHORIZATION,
                "max_usd": 1,
                "max_calls": 60,
                "max_run_usd": 0.10,
                "before": self.before,
                "model": "deepseek-flash",
                "thinking": "disabled",
                "case_generator": 0,
                "judge": 0,
                "mode": "real_provider_production_runner_with_persistent_ledger_transport",
                "price_verified_at": datetime.now(UTC).isoformat(),
                "price_source": "https://api-docs.deepseek.com/quick_start/pricing/",
                "peak_input_miss_usd_per_million": 0.30,
                "peak_output_usd_per_million": 1.20,
                "prices_match_frozen_estimator": True,
                "not_an_invoice": True,
            },
        )

    def ledger(self):
        return {k.decode(): int(v) for k, v in self.coordinator.hgetall(AUTH_KEY).items()}

    def create(self, cases, **kwargs):
        return super().create(cases, agent_microusd=100_000, **kwargs)

    def export(self, **kwargs):
        calls = []
        for path in sorted((self.local / "real-calls").rglob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            calls.append(
                {
                    "call_id": value["call_id"],
                    "status": value["status"],
                    "usage": value.get("usage"),
                    "error_code": value.get("error_code"),
                    "http_status": value.get("http_status"),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        self.save(
            "live-calls.json",
            {"calls": calls, "ledger": self.ledger(), "unknown_is_held_not_refunded": True},
        )
        # Store provider files exactly once in their own small compressed archive.
        import zipfile

        target = self.output / "provider-calls.zip"
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in (self.local / "real-calls").rglob("*.json"):
                archive.write(path, path.relative_to(self.local).as_posix())
            for path in self.signals.glob("*.provider-chunk.json"):
                archive.write(path, path.relative_to(self.local).as_posix())
        super().export(**kwargs)


def signal(lab, name):
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if (lab.signals / (name + ".provider-chunk.json")).exists():
            return
        time.sleep(0.1)
    raise RuntimeError("No real provider chunk: " + name)


def main():
    with RealLab() as lab:
        lab.enable()
        try:
            worker = lab.start_worker("live", 5)
            rows = []
            for cap in (1, 3, 5):
                ids = [
                    lab.case(f"LIVE-{cap}-{i}", "你好，只简短回复一句欢迎，不使用工具，不预约。")
                    for i in range(cap)
                ]
                run = lab.create(ids, concurrency=cap)
                lab.dispatch(run, duplicate=True)
                view, peak = lab.wait(run, cap=cap)
                assert lab.bookings(view) == 0
                assert all(
                    e["status"] == "passed" and e["attempt"] == 1 for e in view["executions"]
                )
                rows.append(
                    {
                        "run_id": run,
                        "cap": cap,
                        "samples": cap,
                        "observed_peak": peak,
                        "summary": view["summary"],
                    }
                )
                lab.save(f"live-concurrency-{cap}.json", rows[-1])
            cancel = lab.create(
                [lab.case("LIVE-CANCEL", "T17-LIVE-CANCEL 你好，请简短欢迎，不使用工具。")]
            )
            lab.dispatch(cancel)
            signal(lab, "T17-LIVE-CANCEL")
            lab.service.cancel(1, cancel)
            (lab.signals / "T17-LIVE-CANCEL.release").touch()
            time.sleep(2)
            cancelled = lab.service.read(1, cancel)
            assert cancelled["status"] == "cancelled" and lab.bookings(cancelled) == 0
            lab.remove_worker(worker)
            worker = lab.start_worker("pause", 1)
            paused = lab.create(
                [lab.case("LIVE-PAUSE", "T17-LIVE-PAUSE 你好，请简短欢迎，不使用工具。")]
            )
            lab.dispatch(paused)
            signal(lab, "T17-LIVE-PAUSE")
            lab.docker("pause", worker)
            recovery = lab.start_worker("recovery", 1)
            time.sleep(12)
            lab.dispatch(paused)
            recovered, _ = lab.wait(paused)
            e = recovered["executions"][0]
            assert e["status"] == "error" and e["error"]["code"] == "recovery_uncertain"
            assert e["attempt"] == 2 and lab.bookings(recovered) == 0
            lab.docker("unpause", worker)
            (lab.signals / "T17-LIVE-PAUSE.release").touch()
            time.sleep(3)
            assert (
                lab.service.read(1, paused)["executions"][0]["error"]["code"]
                == "recovery_uncertain"
            )
            lab.remove_worker(worker)
            lab.remove_worker(recovery)
            lab.save(
                "live-summary.json",
                {
                    "concurrency": rows,
                    "cancel": cancel,
                    "pause": paused,
                    "ledger": lab.ledger(),
                    "real_provider_first_chunk_observed_before_both_interruptions": True,
                    "boundary": (
                        "9 simple greetings plus 2 interrupted development greetings; "
                        "not semantic quality or provider load benchmark"
                    ),
                },
            )
        except BaseException as exc:
            lab.export(failed=type(exc).__name__)
            raise
        else:
            lab.export()
            print(json.dumps({"evidence": str(lab.output), "ledger": lab.ledger()}), flush=True)


if __name__ == "__main__":
    main()

"""Read-only verification of new judge calls, immutable history and original labels."""

import hashlib
import io
import json
import runpy
import shutil
import subprocess
import zipfile
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from redis import Redis
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.agents.budget import peak_estimate_microusd
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.judging.engine import interpret, manifest
from app.judging.protocol import parse_response
from app.models import Artifact, Booking, Defect, TargetVersion, TestCase, TestExecution, TestRun
from judge_checks.lab import KEY


def sha(body):
    return hashlib.sha256(body).hexdigest()


def main():
    root = PROJECT_ROOT
    output = root / "docs/evidence/T17B"
    baseline = json.loads((output / "preparation.json").read_bytes())
    s = Settings()
    assert not any(
        getattr(s, k)
        for k in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    client, engine = Redis.from_url(s.redis_url), create_database_engine(s)
    try:

        def ledger(key):
            return {k.decode(): int(v) for k, v in client.hgetall(key).items()}

        assert {key: ledger(key) for key in baseline["ledgers"]} == baseline["ledgers"]
        tables = {}
        with Session(engine) as session:
            for model in (
                TargetVersion,
                TestCase,
                Artifact,
                Booking,
                Defect,
                TestRun,
                TestExecution,
            ):
                query = select(model).order_by(model.id)
                if model is TargetVersion:
                    query = query.where(model.id <= 42)
                rows = [
                    {c.name: getattr(row, c.name) for c in row.__table__.columns}
                    for row in session.scalars(query)
                ]
                plain = json.loads(json.dumps(rows, default=str))
                tables[model.__tablename__] = {"count": len(plain), "sha256": canonical_hash(plain)}
            assert tables == baseline["tables"]
            new = session.get(TargetVersion, 43)
            new_hash = new.config_hash
            for relative, digest in new.model_config_json["sources"].items():
                assert sha((root / relative).read_text(encoding="utf-8").encode()) == digest, (
                    relative
                )
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version"))
                == baseline["migration"]
            )
            assert (
                session.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=:db"
                    ),
                    {"db": s.mysql_database},
                )
                == baseline["triggers"]
            )
        assert len(inspect(engine).get_table_names()) - 1 == baseline["table_count"]
        history = {}
        for task in ("T16", "T17", "T17A"):
            path = root / f"docs/evidence/{task}/delivery-manifest.json"
            assert sha(path.read_bytes()) == baseline["historical_manifests"][task]
            entries = json.loads(path.read_bytes())["files"]
            checked = 0
            for name, meta in entries.items():
                if name.startswith(
                    ("docs/evidence/", "docs/fixtures/", "datasets/", "docs/acceptance/")
                ):
                    assert sha((root / name).read_bytes()) == (
                        meta["sha256"] if isinstance(meta, dict) else meta
                    ), name
                    checked += 1
            history[task] = {"manifest_unchanged": True, "immutable_files_checked": checked}
        cohort = next(output.glob("calibration-*/final-batch.json")).parent
        batch = json.loads((cohort / "final-batch.json").read_bytes())
        seal = batch["seal"]
        assert (
            canonical_hash(seal["labels"]) == seal["labels_hash"] == canonical_hash(batch["labels"])
        )
        assert seal == json.loads((cohort / "independent-seal.json").read_bytes())
        protocol_settings = s.model_copy(update={"judge_max_output_tokens": 1536})
        assert (
            batch["evaluation_protocol"]
            == batch["frozen"]["manifest"]
            == manifest(protocol_settings)
        )
        expected = json.loads((cohort / "archive-manifest.json").read_bytes())
        providers, sql_rows, values = {}, [], {}
        with zipfile.ZipFile(cohort / "raw-evidence.zip") as archive:
            assert set(archive.namelist()) == set(expected)
            for name, meta in expected.items():
                body = archive.read(name)
                assert sha(body) == meta["sha256"] and len(body) == meta["bytes"]
                if "/provider/" in name:
                    call = json.loads(body)
                    assert call["call_id"] not in providers
                    providers[call["call_id"]] = call
                elif name == "sql-artifacts.json":
                    sql_rows = json.loads(body)
                else:
                    values[name] = json.loads(body)
        samples = {canonical_hash(x["payload"]): x for x in batch["frozen"]["dataset"]["samples"]}
        assert len(providers) == 12
        call_by_sample, tokens, estimate = {}, 0, 0
        for identifier, call in providers.items():
            assert call["role"] == "judge" and call["model_requests"] == 1
            assert call["request"]["response_format"] == {"type": "json_object"}
            assert not call["request"]["tools"] and len(call["request"]["messages"]) == 2
            request = json.loads(call["request"]["messages"][1]["data"]["content"])
            sample = samples[canonical_hash(request["observed"])]
            assert request == {
                "observed": sample["payload"],
                "view": "observations" if sample["payload"]["evidence"] else "answer_only",
            }
            assert datetime.fromisoformat(call["started_at"]) > datetime.fromisoformat(
                seal["sealed_at"]
            )
            assert sample["id"] not in call_by_sample
            call_by_sample[sample["id"]] = identifier
            parsed = interpret(parse_response(call["response"]["content"]), sample["payload"])
            result = batch["results"][sample["id"]]
            assert (
                parsed["assessment"] == result["assessment"]
                and parsed["status"] == result["status"]
            )
            assert result["call_id"] == identifier and result["usage"] == call["usage"]
            assert call["response"]["finish_reason"] == "stop"
            usage = call["usage"]
            tokens += usage["input_tokens"] + usage["output_tokens"]
            charge = peak_estimate_microusd(usage["input_tokens"], usage["output_tokens"])
            assert charge == call["estimated_peak_microusd"]
            estimate += charge
        reservations, settlements = {}, {}
        for row in sql_rows:
            value = values[row["relative_path"]]
            phase = row["metadata_json"]["phase"]
            if phase == "reserved":
                reservations[value["ticket"]["call_id"]] = value
            if phase == "settled":
                settlements[value["ticket"]["call_id"]] = value
        assert set(providers) == set(reservations) == set(settlements)
        for identifier, call in providers.items():
            assert (
                reservations[identifier]["ticket"]["upper_microusd"] == call["reservation_microusd"]
            )
            assert settlements[identifier]["usage"] == call["usage"]
            assert settlements[identifier]["estimate_microusd"] == call["estimated_peak_microusd"]
        current = ledger(KEY)
        assert current == {
            "limit": 500_000,
            "calls_started": 12,
            "calls_completed": 12,
            "held_upper": 0,
            "token_upper": tokens,
            "charged_estimate": estimate,
        }
        contracts = {}

        def capture(path, body, **_):
            contracts[path.name] = body.encode()
            return len(body)

        with patch.object(Path, "write_text", capture), redirect_stdout(io.StringIO()):
            runpy.run_path(str(root / "backend/scripts/export_contracts.py"))
        assert len(contracts) == 17
        for name, body in contracts.items():
            assert (root / "docs/architecture" / name).read_bytes().replace(
                b"\r\n", b"\n"
            ) == body.replace(b"\r\n", b"\n")
        secret = s.deepseek_api_key.get_secret_value().encode()
        assert len(secret) > 12
        for path in output.rglob("*"):
            if path.is_file():
                if path.suffix == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        assert all(secret not in archive.read(name) for name in archive.namelist())
                else:
                    assert secret not in path.read_bytes(), path
        assert not subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=root)
        result = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "old_twelve_ledgers_unchanged": True,
            "old_main_records_unchanged": tables,
            "history": history,
            "new_source_version": 43,
            "new_config_hash": new_hash,
            "provider_requests_verified": 12,
            "each_after_human_seal": True,
            "blind_payload_exact_match": True,
            "sql_reservations_usage_global_ledger_match": True,
            "original_labels_unchanged": True,
            "ledger": current,
            "not_a_bill": True,
            "contracts_verified": 17,
            "tables": baseline["table_count"],
            "triggers": baseline["triggers"],
            "migration": baseline["migration"],
            "daily_paid_flags_false": True,
            "public_evidence_secret_scan_passed": True,
            "index_empty": True,
            "c_free_bytes": shutil.disk_usage("C:/").free,
            "boundary": "No old outcomes rescored; no assistant analysis replaces human labels.",
        }
        (output / "final-audit.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "calls": 12,
                    "tokens": tokens,
                    "estimate_microusd": estimate,
                    "old_ledgers": 12,
                    "historical": history,
                    "contracts": 17,
                }
            )
        )
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()

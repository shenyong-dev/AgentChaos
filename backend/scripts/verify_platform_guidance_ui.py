"""Read-only real-page help verification; credentials remain only in child memory."""

import argparse
import json
import os
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.models import TestCase, TestExecution, TestRun
from app.workbench.service import WorkbenchService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="ui-first")
    parser.add_argument("--grep")
    parser.add_argument("--regressions", action="store_true")
    args = parser.parse_args()
    assert args.output.replace("-", "").isalnum()
    output = PROJECT_ROOT / "docs/evidence/T17I" / args.output
    assert not output.exists(), "Preserve earlier results"
    settings = Settings()
    assert not settings.allow_paid_model_calls and not settings.judge_allow_paid_model_calls
    engine = create_database_engine(settings)
    try:
        first = WorkbenchService(engine, settings).cases(1, limit=1)["items"][0]
        with Session(engine) as session:
            run = session.scalar(
                select(TestRun).where(TestRun.created_by == 1).order_by(TestRun.id)
            )
            execution = session.scalar(
                select(TestExecution.execution_uuid).where(TestExecution.run_id == run.id)
            )
            holdout = session.scalar(select(TestCase.id).where(TestCase.dataset_split == "holdout"))
        objects = {
            "case": first["id"],
            "case_key": first["case_key"],
            "run": run.run_uuid,
            "execution": execution,
            "holdout": holdout,
        }
    finally:
        engine.dispose()
    output.mkdir(parents=True)
    env = {
        **os.environ,
        "AC_UI_PASSWORD": settings.demo_seed_password.get_secret_value(),
        "AC_T17I_UI": "1",
        "AC_T17I_OBJECTS": json.dumps(objects),
        "AC_E2E_EVIDENCE_DIR": str(output),
        "AC_E2E_BASE_URL": "http://127.0.0.1:5173",
    }
    tests = ["platform-guidance.spec.ts"]
    selection = args.grep
    if args.regressions:
        env.update(AC_T17F_UI="1", AC_T17G_UI="1")
        tests = ["case-guidance.spec.ts", "sessions.spec.ts", "environment.spec.ts"]
        selection = "|".join(
            (
                "six real categories",
                "long conversations remain",
                "nonbase knowledge stays",
                "delayed earlier case",
                "one real login visits",
                "real services show",
                "mobile view fits",
                "a simulated network failure",
            )
        )
    command = ["D:/node.js/node.exe", "node_modules/@playwright/test/cli.js", "test", *tests]
    if selection:
        command += ["--grep", selection]
    result = subprocess.run(command, cwd=PROJECT_ROOT / "frontend", env=env, check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

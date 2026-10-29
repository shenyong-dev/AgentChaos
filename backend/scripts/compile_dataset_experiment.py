"""Validate cold T13 V0 request drafts. This CLI cannot create or dispatch a run."""

import argparse
import hashlib
import json

from app.cases.service import CaseService
from app.core.config import PROJECT_ROOT, Settings
from app.datasets.catalog import PATH as CATALOG_PATH
from app.datasets.service import DatasetService
from app.db.engine import create_database_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify published frozen file hashes")
    parser.add_argument("--version", choices=["V0", "V1"], default="V0")
    args = parser.parse_args()
    s = Settings()
    engine = create_database_engine(s)
    try:
        user = CaseService(engine, None, s).authenticate(
            "demo_operator", s.demo_seed_password.get_secret_value()
        )
        service = DatasetService(engine, s)
        if args.version == "V1":
            raise SystemExit("V1 is not registered. No run created; never substitute V0.")
        if args.verify:
            frozen = json.loads((CATALOG_PATH.parent / "frozen-manifest.json").read_bytes())
            for relative, digest in frozen["files"].items():
                if hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest() != digest:
                    raise SystemExit("Frozen file changed: " + relative)
        current = service.summary(user)
        drafts = service.requests(user)
        for job in drafts["requests"]:
            case = next(
                c
                for c in current["experiment_plan"]["E2"]["episodes"]
                if c["version"] == "V0" and c["case_key"] == job["plan_case"]
            )
            job["trusted_creation"] = {
                "factory": "app.executors.service.RunService",
                "document_ids": current["frozen"]["knowledge_profiles"][case["knowledge_profile"]],
                "boundary": (
                    "Use the frozen server-trusted knowledge selection. "
                    "The default-KB API cannot create custom-KB runs."
                ),
            }
        print(
            json.dumps(
                {
                    "dataset_hash": current["package_hash"],
                    "V0_request_drafts": len(drafts["requests"]),
                    "profiles": len(current["bindings"]["V0"]),
                    "V1": "unregistered",
                    "runs_created": 0,
                    "paid_calls": 0,
                    "files_verified": bool(args.verify),
                    "boundary": "Cold requests only; no Worker, dispatch or model invocation.",
                },
                ensure_ascii=False,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

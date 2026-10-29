"""Read-only HTTP and rejected-input checks; never creates a human label or model call."""

import base64
from datetime import UTC, datetime

import httpx

from app.agents.versions import canonical_hash
from app.core.config import Settings
from experiments.common import EVIDENCE, ROOT, read, write_new
from experiments.schemas import ExecutionGoldLabel


def main():
    s = Settings()
    password = s.demo_seed_password.get_secret_value()
    header = {
        "Authorization": "Basic "
        + base64.b64encode(("demo_reviewer:" + password).encode()).decode()
    }
    batch = read(EVIDENCE / "human-batches/C.json")["batch_id"]
    base = read(ROOT / ".local/t16/latest-review.json")["api"]
    path = "/api/v1/judging/calibrations/" + batch
    with httpx.Client(base_url=base, headers=header, timeout=20) as client:
        access = client.get("/api/v1/judging/access")
        assert access.status_code == 200 and not access.json()["data"]["judge_enabled"]
        before = client.get(path)
        assert before.status_code == 200
        blocked = client.get("/api/v1/runs")
        assert blocked.status_code == 403
        invalid = client.post(path + "/samples/C-001/execution-label", json={})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "validation_error"
        assert any(
            x["location"][-1] == "execution_problem" for x in invalid.json()["error"]["details"]
        )
        after = client.get(path)
        assert canonical_hash(before.json()["data"]) == canonical_hash(after.json()["data"])
    write_new(
        EVIDENCE / "label-api-verification.json",
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "authenticated_access": 200,
            "judge_enabled": False,
            "non_label_route_blocked": 403,
            "incomplete_gold_label_rejected": 400,
            "original_C_batch_unchanged": True,
            "new_human_labels": 0,
            "new_model_calls": 0,
            "gold_label_schema": ExecutionGoldLabel.model_json_schema(),
        },
    )
    print("T16 label-only API and rejected gold input verified; no label/model mutation")


if __name__ == "__main__":
    main()

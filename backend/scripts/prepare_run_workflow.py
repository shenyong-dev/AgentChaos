"""T17H baseline: preserve existing main records, ledgers, source and sealed evidence."""

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime

from app.core.config import PROJECT_ROOT
from scripts.prepare_sessions import observe


def main():
    root = PROJECT_ROOT
    out = root / "docs/evidence/T17H"
    out.mkdir(parents=True, exist_ok=True)
    assert not (out / "preparation.json").exists()
    baseline = observe()
    assert baseline["c_free_bytes"] > 2_000_000_000
    paths = []
    for folder in ("backend/app", "frontend/src", "docs/architecture", "scripts"):
        paths.extend(
            p for p in (root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    paths += [root / "任务规划.md", root / "backend/tests/test_database.py", root / "compose.yaml"]
    with zipfile.ZipFile(out / "previous-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path, path.relative_to(root).as_posix())
    historical = {}
    for folder in ("docs/evidence", "docs/fixtures"):
        for path in (root / folder).rglob("*"):
            if path.is_file() and out not in path.parents:
                historical[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    local = root / ".local/t17h"
    local.mkdir(parents=True, exist_ok=True)
    (local / "historical-hashes.json").write_text(json.dumps(historical), encoding="utf-8")
    baseline.update(
        recorded_at=datetime.now(UTC).isoformat(),
        authorization="User next-step accepts T17G and authorizes T17H "
        "including v1.33 Worker lifecycle planning.",
        base_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        historical_files=len(historical),
        source_archive="previous-source.zip",
        new_model_calls=0,
        boundaries="T17H only; no T17I/E, paid model calls, migration, "
        "installation, cleanup or Git release.",
    )
    (out / "preparation.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "tables": len(baseline["tables"]),
                "ledgers": len(baseline["ledgers"]),
                "historical": len(historical),
            }
        )
    )


if __name__ == "__main__":
    main()

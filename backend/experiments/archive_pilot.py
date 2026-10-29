"""Append original diagnostic provider bytes without modifying any pilot export."""

import json
from pathlib import Path

from experiments.common import EVIDENCE, LOCAL, read, settings, sha, write_new


def main():
    snapshots = read(LOCAL / "session-private.json")["snapshots"]
    secret = settings().deepseek_api_key.get_secret_value().encode()
    assert secret
    entries = []
    for outcome in sorted((EVIDENCE / "episodes").glob("*/outcome.json")):
        value = read(outcome)
        version = value["episode"]["version"]
        source = Path(snapshots[version]) / ".local/t05/model-calls" / value["run_id"]
        records = []
        for original in sorted(source.rglob("*.json")):
            data = original.read_bytes()
            assert secret not in data
            target = EVIDENCE / "diagnostic-provider-archive" / outcome.parent.name / original.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                assert target.read_bytes() == data
            else:
                with target.open("xb") as file:
                    file.write(data)
            records.append(
                {"path": target.relative_to(EVIDENCE).as_posix(), "sha256": sha(original)}
            )
        entries.append(
            {"episode": outcome.parent.name, "run_id": value["run_id"], "files": records}
        )
    manifest = EVIDENCE / "diagnostic-provider-archive/manifest.json"
    if manifest.exists():
        assert read(manifest) == entries
    else:
        write_new(manifest, entries)
    print(
        json.dumps(
            {
                "diagnostic_episodes": len(entries),
                "original_files": sum(len(x["files"]) for x in entries),
                "new_model_calls": 0,
                "old_exports_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()

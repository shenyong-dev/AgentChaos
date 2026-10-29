"""Bounded local process pool; no scheduler, silent retry or extra planned episode."""

import argparse
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime

from app.datasets.catalog import experiment_plan, load_catalog
from experiments.common import EVIDENCE, LOCAL, ROOT, read, write_new


def invoke(job):
    source = read(LOCAL / "session-private.json")["snapshots"][job["version"]]
    environment = {
        **os.environ,
        "PYTHONPATH": source + "/backend" + os.pathsep + str(ROOT / "backend"),
        "PYTHONIOENCODING": "utf-8",
        "TEMP": str(ROOT / ".cache/tmp"),
        "TMP": str(ROOT / ".cache/tmp"),
    }
    label = f"{job['version']}-{job['case_key']}-{job['repeat_no']}"
    dest = EVIDENCE / "serial-episodes" / label
    if (dest / "outcome.json").exists():
        return {"episode": label, "already_recorded": True}
    if dest.exists():
        raise RuntimeError(
            "Interrupted episode requires explicit audit; never silently retry: " + label
        )
    logs = EVIDENCE / "serial-process-logs"
    logs.mkdir(exist_ok=True)
    with (logs / (label + ".txt")).open("x", encoding="utf-8") as file:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "experiments.episodes",
                "--version",
                job["version"],
                "--case",
                job["case_key"],
                "--repeat",
                str(job["repeat_no"]),
                "--execute",
                "--cohort",
                "serial",
            ],
            cwd=source,
            env=environment,
            stdout=file,
            stderr=subprocess.STDOUT,
        )
    if result.returncode != 0 or not (dest / "outcome.json").exists():
        raise RuntimeError("Episode failed to export; preserve and audit: " + label)
    return {"episode": label, "outcome": read(dest / "outcome.json")["runner"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["calibration", "remaining"], required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    assert (EVIDENCE / "preflight.json").exists()
    assert not (LOCAL / "stop").exists()
    catalog = load_catalog()
    jobs = experiment_plan(catalog)["E2"]["episodes"]
    calibration = {c["id"] for c in catalog["cases"] if c["dsl"]["dataset_split"] == "calibration"}
    if args.phase == "calibration":
        jobs = [j for j in jobs if j["version"] == "V0" and j["case_key"] in calibration]
        assert len(jobs) == 30
    else:
        # Alternate the two versions inside each planned case/repeat pair.
        jobs = sorted(jobs, key=lambda j: (j["case_key"], j["repeat_no"], j["version"]))
    receipt = EVIDENCE / (args.phase + "-serial-launch.json")
    if args.resume:
        assert receipt.exists(), "No original serial plan to resume"
        assert read(receipt)["jobs"] == jobs, "Resume would change the frozen episode list"
        receipt = EVIDENCE / (
            args.phase + "-serial-resume-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json"
        )
    write_new(
        receipt,
        {
            "phase": args.phase,
            "jobs": jobs,
            "max_concurrency": 1,
            "automatic_infrastructure_retries": 0,
            "resume_skips_existing_outcomes_without_rerunning": args.resume,
            "planned_total": len(jobs),
        },
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending, remaining = set(), iter(jobs)
        exhausted = False
        while pending or not exhausted:
            while len(pending) < 1 and not exhausted:
                if (LOCAL / "stop").exists() or shutil.disk_usage("C:/").free < 1_500_000_000:
                    raise SystemExit("Stopped before next episode: explicit marker or disk floor.")
                try:
                    pending.add(pool.submit(invoke, next(remaining)))
                except StopIteration:
                    exhausted = True
            done, pending = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
            for future in done:
                print(__import__("json").dumps(future.result()), flush=True)
            if not done:
                time.sleep(0.1)


if __name__ == "__main__":
    main()

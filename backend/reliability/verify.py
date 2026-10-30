"""Run the bounded free T17 infrastructure matrix; no model key or paid switch."""

import argparse
import json
import time

from reliability.controls import run_matrix
from reliability.lab import Lab
from reliability.review import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loads-only", action="store_true")
    parser.add_argument("--controls-only", action="store_true")
    parser.add_argument("--review-seconds", type=int, default=0)
    args = parser.parse_args()
    with Lab() as lab:
        try:
            worker = lab.start_worker("pool", 5)
            # A bounded pause allows the actual prefork processes to connect to Redis.
            time.sleep(2)
            loads = [] if args.controls_only else lab.loads()
            controls = {}
            if not args.loads_only:
                controls, worker = run_matrix(lab, worker)
            summary = {
                "loads": loads,
                "controls": controls,
                "paid_calls": 0,
                "checks_complete": not args.loads_only and not args.controls_only,
                "scope": "controls"
                if args.controls_only
                else "loads"
                if args.loads_only
                else "full",
            }
            lab.save("summary.json", summary)
            print(json.dumps({"evidence": str(lab.output), "loads": loads}), flush=True)
            lab.remove_worker(worker)
        except BaseException as exc:
            lab.export(failed=type(exc).__name__)
            raise
        else:
            lab.export()
            if args.review_seconds:
                serve(lab, summary, args.review_seconds)


if __name__ == "__main__":
    main()

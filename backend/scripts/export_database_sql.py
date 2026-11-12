"""Regenerate/check public SQL from migrations and public reference fixtures, without a live DB."""

import argparse
import sys

from database_bundle import DIRECTORY, bundle_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = bundle_files()
    if args.check:
        changed = [
            name
            for name, data in files.items()
            if not (DIRECTORY / name).exists() or (DIRECTORY / name).read_bytes() != data.encode()
        ]
        if changed:
            print("SQL bundle differs: " + ", ".join(changed), file=sys.stderr)
            return 1
        print("Public SQL bundle matches migrations and reference data.")
    else:
        DIRECTORY.mkdir(exist_ok=True)
        for name, data in files.items():
            (DIRECTORY / name).write_bytes(data.encode())
        print(
            "Generated schema.sql, demo_data.sql, verify.sql and manifest.json; no database access."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

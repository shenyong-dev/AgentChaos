"""Read-only Playwright review, with one video file and local credentials only."""

import os
import subprocess

from app.core.config import PROJECT_ROOT, Settings


def main():
    root = PROJECT_ROOT
    review = root / ".local/t17/latest-review.json"
    assert review.exists()
    output = root / "docs/evidence/T17/ui"
    output.mkdir(exist_ok=False)
    env = {
        **os.environ,
        "AC_T17_REVIEW": str(review),
        "AC_T17_PASSWORD": Settings().demo_seed_password.get_secret_value(),
        "AC_E2E_EVIDENCE_DIR": str(output),
    }
    with (output / "playwright.txt").open("wb") as log:
        result = subprocess.run(
            [
                "D:/node.js/node.exe",
                str(root / "frontend/node_modules/@playwright/test/cli.js"),
                "test",
                "tests/reliability.spec.ts",
            ],
            cwd=root / "frontend",
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    print((output / "playwright.txt").read_text(encoding="utf-8"))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

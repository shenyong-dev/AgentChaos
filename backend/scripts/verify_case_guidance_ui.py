"""Use the local credential only in child environment; print no secrets."""

import os
import subprocess
import sys

from app.core.config import PROJECT_ROOT, Settings


def main():
    env = {
        **os.environ,
        "AC_UI_PASSWORD": Settings().demo_seed_password.get_secret_value(),
        "AC_T17G_UI": "1",
        "AC_E2E_EVIDENCE_DIR": str(PROJECT_ROOT / "docs/evidence/T17G/ui"),
        "AC_E2E_BASE_URL": "http://127.0.0.1:5173",
    }
    command = [
        "D:/node.js/node.exe",
        "node_modules/@playwright/test/cli.js",
        "test",
        "case-guidance.spec.ts",
        *sys.argv[1:],
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT / "frontend", env=env, check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

"""Pass the existing local test credential only in child environment, never output it."""

import os
import subprocess

from app.core.config import PROJECT_ROOT, Settings


def main():
    settings = Settings()
    env = {
        **os.environ,
        "AC_UI_PASSWORD": settings.demo_seed_password.get_secret_value(),
        "AC_T17F_UI": "1",
        "AC_E2E_EVIDENCE_DIR": str(PROJECT_ROOT / "docs/evidence/T17F/ui"),
        "AC_E2E_BASE_URL": "http://127.0.0.1:5173",
    }
    command = [
        "D:/node.js/node.exe",
        "node_modules/@playwright/test/cli.js",
        "test",
        "sessions.spec.ts",
        "environment.spec.ts",
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT / "frontend", env=env, check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

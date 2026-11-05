"""Read-only main-page regression; prohibit business writes in existing UI tests."""

import os
import subprocess

from app.core.config import PROJECT_ROOT, Settings


def main():
    output = PROJECT_ROOT / "docs/evidence/T17H/main-ui"
    assert not output.exists(), "Keep previous page evidence"
    output.mkdir()
    env = {
        **os.environ,
        "AC_UI_PASSWORD": Settings().demo_seed_password.get_secret_value(),
        "AC_T17F_UI": "1",
        "AC_T17G_UI": "1",
        "AC_E2E_EVIDENCE_DIR": str(output),
        "AC_E2E_BASE_URL": "http://127.0.0.1:5173",
    }
    selection = "|".join(
        (
            "six real categories",
            "long conversations remain",
            "nonbase knowledge stays",
            "delayed earlier case",
            "one real login visits",
            "real services show",
            "mobile view fits",
            "a simulated network failure",
        )
    )
    result = subprocess.run(
        [
            "D:/node.js/node.exe",
            "node_modules/@playwright/test/cli.js",
            "test",
            "case-guidance.spec.ts",
            "sessions.spec.ts",
            "environment.spec.ts",
            "--grep",
            selection,
        ],
        cwd=PROJECT_ROOT / "frontend",
        env=env,
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

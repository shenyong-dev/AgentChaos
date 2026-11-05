"""Use the private credential only in child environment, against the owned free lab."""

import argparse
import json
import os
import subprocess

from app.core.config import PROJECT_ROOT, Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="ui-v51")
    parser.add_argument("--grep")
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    assert args.output.replace("-", "").isalnum()
    output = PROJECT_ROOT / "docs/evidence/T17H" / args.output
    assert not output.exists(), "Keep previous UI results"
    info = json.loads((PROJECT_ROOT / ".local/t17h/latest-review.json").read_bytes())
    assert info["profile"].startswith("t17h-")
    assert info["mode"] == "offline_test_double"
    settings = Settings(_env_file=info["config_file"])
    assert not settings.deepseek_api_key.get_secret_value()
    env = {
        **os.environ,
        "AC_UI_PASSWORD": settings.demo_seed_password.get_secret_value(),
        "AC_T17H_LAB": json.dumps(info),
        "AC_E2E_EVIDENCE_DIR": str(output),
        "AC_E2E_BASE_URL": "http://127.0.0.1:5189",
    }
    result = subprocess.run(
        [
            "D:/node.js/node.exe",
            "node_modules/@playwright/test/cli.js",
            "test",
            "run-workflow.spec.ts",
            *(["--grep", args.grep] if args.grep else []),
            *args.tests,
        ],
        cwd=PROJECT_ROOT / "frontend",
        env=env,
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

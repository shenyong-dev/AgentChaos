"""Create a project-local .env without overwriting existing configuration."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / ".env"
template = (root / ".env.example").read_text(encoding="utf-8")
for name in (
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "DEMO_SEED_PASSWORD",
):
    template = template.replace(f"{name}=\n", f"{name}={secrets.token_urlsafe(32)}\n")
try:
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        output.write(template)
except FileExistsError:
    print(".env already exists; kept unchanged.")
else:
    print("Created ignored project-local .env. Secrets were not printed.")

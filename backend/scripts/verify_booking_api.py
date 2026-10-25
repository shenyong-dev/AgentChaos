"""Read-only network smoke against the local T03 API, using ignored credentials."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import create_database_engine
from app.models import Booking, BookingDraft, BookingSlot, Room


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    credential = settings.demo_seed_password.get_secret_value()
    if not credential:
        raise ValueError("Set DEMO_SEED_PASSWORD in the local environment")
    engine = create_database_engine(settings)

    def row_counts():
        with Session(engine) as session:
            return {
                model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in (Room, BookingDraft, Booking, BookingSlot)
            }

    try:
        before = row_counts()
        start = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=1)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        body = {
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "attendees": 10,
        }
        auth = httpx.BasicAuth("demo_operator", credential)
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
            health = client.get("/api/v1/health")
            assert health.status_code == 200
            schema = client.get("/openapi.json").json()
            routes = {
                name: sorted(value)
                for name, value in schema["paths"].items()
                if name.startswith("/api/v1/booking/")
            }
            assert len(routes) == 5
            unauthorized = client.post("/api/v1/booking/availability", json=body)
            assert unauthorized.status_code == 401
            available = client.post("/api/v1/booking/availability", json=body, auth=auth)
            assert available.status_code == 200
            unknown = client.get("/api/v1/booking/requests/" + str(uuid4()), auth=auth)
            assert unknown.status_code == 200
            assert unknown.json()["data"]["commit_state"] == "unknown"
            invalid = client.post(
                "/api/v1/booking/availability", auth=auth, json={**body, "scope_id": "untrusted"}
            )
            assert invalid.status_code == 400
            frontend = client.get("http://127.0.0.1:5173/environment")
            assert frontend.status_code == 200
        after = row_counts()
        assert after == before
        result = {
            "checked_at": datetime.now(UTC).isoformat(),
            "transport": "real loopback HTTP",
            "routes": routes,
            "health_http": health.status_code,
            "health": health.json(),
            "unauthenticated_http": unauthorized.status_code,
            "availability_http": available.status_code,
            "availability": available.json(),
            "missing_request": unknown.json(),
            "forged_scope_http": invalid.status_code,
            "frontend_http": frontend.status_code,
            "before": before,
            "after": after,
            "model_requests": 0,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "Live API checks passed: 5 routes, authentication, availability, unknown lookup, "
            "scope rejection and frontend; application booking rows unchanged."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

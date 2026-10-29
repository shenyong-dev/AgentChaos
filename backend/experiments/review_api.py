"""T16 label-only API using the existing frontend, isolated credentials and database."""

from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from redis import Redis

from app.api.booking import get_booking_engine
from app.api.judging import Identity, Service, get_judge_service
from app.core.config import get_settings
from app.db.engine import create_database_engine
from app.main import create_app
from app.schemas.common import Envelope
from experiments.common import settings
from experiments.labels import LabelService
from experiments.schemas import ExecutionGoldLabel

s = settings()
engine = create_database_engine(s)
service = LabelService(engine, Redis.from_url(s.redis_url), s)
app = create_app()
app.dependency_overrides[get_settings] = lambda: s
app.dependency_overrides[get_booking_engine] = lambda: engine
app.dependency_overrides[get_judge_service] = lambda: service


@app.post("/api/v1/judging/calibrations/{batch}/samples/{sample_id}/execution-label")
def execution_label(
    request: Request,
    batch: UUID,
    sample_id: str,
    values: ExecutionGoldLabel,
    user: Identity,
    service: Service,
) -> Envelope[dict]:
    return Envelope(
        data=service.label_execution(user, batch, sample_id, values),
        request_id=request.state.request_id,
    )


@app.middleware("http")
async def label_only(request, call_next):
    path = request.url.path
    if not (path == "/api/v1/judging/access" or path.startswith("/api/v1/judging/calibrations")):
        return JSONResponse(
            {"error": {"code": "formal_labels_only", "message": "此入口仅用于T16独立标注。"}},
            status_code=403,
        )
    return await call_next(request)

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app import __version__
from app.core.config import Settings, get_settings
from app.core.model_probe import historical_observation
from app.schemas.common import Envelope
from app.schemas.health import HealthReport, ModelRoleConfig, PublicConfig
from app.services.health import collect_health

router = APIRouter(prefix="/api/v1")
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("/health/live", tags=["health"])
def liveness(request: Request) -> Envelope[dict[str, str]]:
    return Envelope(
        data={"status": "alive", "version": __version__}, request_id=request.state.request_id
    )


@router.get(
    "/health",
    response_model=Envelope[HealthReport],
    tags=["health"],
    responses={503: {"model": Envelope[HealthReport], "description": "依赖未就绪"}},
)
async def health(
    request: Request, response: Response, settings: SettingsDependency
) -> Envelope[HealthReport]:
    report = await collect_health(settings)
    if report.status == "degraded":
        response.status_code = 503
    return Envelope(data=report, request_id=request.state.request_id)


@router.get("/system/config", response_model=Envelope[PublicConfig], tags=["system"])
def public_config(request: Request, settings: SettingsDependency) -> Envelope[PublicConfig]:
    return Envelope(
        data=PublicConfig(
            app_version=__version__,
            environment=settings.app_env,
            model_key_configured=bool(settings.deepseek_api_key.get_secret_value()),
            model_connectivity="historically_verified"
            if historical_observation(settings)
            else "not_probed",
            paid_calls_enabled=settings.allow_paid_model_calls,
            run_concurrency=settings.run_concurrency,
            executor_paid_calls_enabled=settings.executor_allow_paid_model_calls,
            executor_agent_budget_usd=settings.executor_agent_budget_usd,
            executor_agent_budget_unlimited=settings.executor_agent_budget_unlimited,
            roles=[
                ModelRoleConfig(
                    role="agent",
                    model=settings.agent_model,
                    thinking=settings.agent_thinking,
                    max_output_tokens=settings.agent_max_output_tokens,
                    budget_usd=settings.agent_budget_usd,
                ),
                ModelRoleConfig(
                    role="case_generator",
                    model=settings.casegen_model,
                    thinking=settings.casegen_thinking,
                    max_output_tokens=settings.casegen_max_output_tokens,
                    budget_usd=settings.casegen_budget_usd,
                ),
                ModelRoleConfig(
                    role="judge",
                    model=settings.judge_model,
                    thinking=settings.judge_thinking,
                    max_output_tokens=settings.judge_max_output_tokens,
                    budget_usd=settings.judge_budget_usd,
                ),
            ],
        ),
        request_id=request.state.request_id,
    )

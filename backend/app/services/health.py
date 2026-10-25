import asyncio
from datetime import UTC, datetime
from time import perf_counter

from redis import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app import __version__
from app.core.config import Settings
from app.core.model_probe import historical_observation
from app.schemas.health import ComponentCheck, HealthReport


def probe_mysql(settings: Settings) -> ComponentCheck:
    started = perf_counter()
    engine = create_engine(
        settings.mysql_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": settings.dependency_timeout_seconds,
            "read_timeout": settings.dependency_timeout_seconds,
            "write_timeout": settings.dependency_timeout_seconds,
        },
    )
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT VERSION()")).scalar_one()
            connection.execute(text("SELECT 1")).scalar_one()
        return ComponentCheck(
            name="mysql",
            status="ok",
            code="mysql_connected",
            message="已连接应用数据库。",
            version=str(version),
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
    except (SQLAlchemyError, OSError):
        # Driver messages may contain connection details; expose stable codes only.
        return ComponentCheck(
            name="mysql",
            status="error",
            code="mysql_unreachable",
            message="数据库连接失败，请检查本地配置、服务与授权。",
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
    finally:
        engine.dispose()


def probe_redis(settings: Settings) -> ComponentCheck:
    started = perf_counter()
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.dependency_timeout_seconds,
        socket_timeout=settings.dependency_timeout_seconds,
        decode_responses=True,
    )
    try:
        client.ping()
        version = client.info("server").get("redis_version", "unknown")
        capabilities = {"json": False, "search": False}
        for name, args in (
            ("json", ("JSON.TYPE", "ac:health:capability-probe")),
            ("search", ("FT._LIST",)),
        ):
            try:
                client.execute_command(*args)
                capabilities[name] = True
            except ResponseError:
                pass
        supported = all(capabilities.values())
        return ComponentCheck(
            name="redis",
            status="ok" if supported else "error",
            code="redis_connected" if supported else "redis_capabilities_missing",
            message="PING、JSON 与 Search 已验证。"
            if supported
            else "连接成功，但 JSON/Search 未齐备。",
            latency_ms=round((perf_counter() - started) * 1000, 2),
            version=str(version),
            capabilities=capabilities,
        )
    except (RedisError, OSError):
        return ComponentCheck(
            name="redis",
            status="error",
            code="redis_unreachable",
            message="Redis 连接失败，请检查本地配置与服务。",
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
    finally:
        client.close()


async def collect_health(settings: Settings) -> HealthReport:
    mysql, redis = await asyncio.gather(
        asyncio.to_thread(probe_mysql, settings), asyncio.to_thread(probe_redis, settings)
    )
    has_key = bool(settings.deepseek_api_key.get_secret_value())
    observation = historical_observation(settings) if has_key else None
    return HealthReport(
        status=(
            "infrastructure_ready" if mysql.status == "ok" and redis.status == "ok" else "degraded"
        ),
        checked_at=datetime.now(UTC),
        checks=[
            ComponentCheck(
                name="api",
                status="ok",
                code="api_running",
                message="API 进程正常响应。",
                version=__version__,
            ),
            mysql,
            redis,
            ComponentCheck(
                name="model",
                status="ok" if observation else ("not_probed" if has_key else "not_configured"),
                code="model_historically_verified"
                if observation
                else ("model_not_probed" if has_key else "model_key_missing"),
                message=f"agent非思考模式于{observation['checked_at']}真实调用成功；此页显示历史结果，刷新不调用模型。"
                if observation
                else (
                    "已配置凭据；当前配置尚无本地成功调用记录。"
                    if has_key
                    else "尚未配置凭据，未调用模型。"
                ),
                capabilities={"historical_agent_probe": True} if observation else {},
            ),
        ],
    )

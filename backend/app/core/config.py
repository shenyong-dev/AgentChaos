from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["development", "test", "production"] = "development"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=13306, ge=1, le=65535)
    mysql_database: str = "agentchaos"
    mysql_user: str = "agentchaos"
    mysql_password: SecretStr = SecretStr("")
    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=16379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_password: SecretStr = SecretStr("")
    dependency_timeout_seconds: int = Field(default=2, ge=1, le=10)
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: Literal["https://api.deepseek.com"] = "https://api.deepseek.com"
    agent_model: str = "deepseek-flash"
    casegen_model: str = "deepseek-flash"
    judge_model: str = "deepseek-flash"
    agent_thinking: Literal["enabled", "disabled"] = "disabled"
    casegen_thinking: Literal["enabled", "disabled"] = "disabled"
    judge_thinking: Literal["enabled", "disabled"] = "disabled"
    model_timeout_seconds: int = Field(default=30, ge=1, le=300)
    model_max_retries: int = Field(default=0, ge=0, le=3)
    agent_max_output_tokens: int = Field(default=1024, ge=1)
    casegen_max_output_tokens: int = Field(default=2048, ge=1)
    judge_max_output_tokens: int = Field(default=1024, ge=1)
    allow_paid_model_calls: bool = False
    agent_budget_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    casegen_budget_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    judge_budget_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    run_concurrency: int = Field(default=1, ge=1, le=5)

    @property
    def mysql_url(self) -> URL:
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )

    @property
    def redis_url(self) -> str:
        password = self.redis_password.get_secret_value()
        auth = f":{quote(password, safe='')}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()

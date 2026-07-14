import os
from pathlib import Path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache

# .env는 프로젝트 루트(backend/ 상위)에 위치
_ENV_FILE = Path(__file__).parent.parent / ".env"

# DB 기본 경로를 backend/ 기준 절대경로로 고정
_DEFAULT_DB = Path(__file__).parent / "data" / "gpu_monitor.db"


class Settings(BaseSettings):
    secret_key: str
    admin_password: str
    database_url: str = f"sqlite+aiosqlite:///{_DEFAULT_DB}"

    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_signing_secret: str = ""
    slack_log_channel: str = Field(
        default="#gpu-monitor",
        validation_alias=AliasChoices("SLACK_LOG_CHANNEL", "SLACK_LOG_CHANNEL_ID"),
    )

    # SSH collector
    collect_interval: int = 10   # seconds, real-time polling
    archive_interval: int = 60   # seconds, history save
    history_days: int = 7        # days to keep history

    # Development-safe switches. They default to false so production behavior is unchanged.
    monitoring_disable_collectors: bool = False
    monitoring_disable_slack: bool = False

    @field_validator("secret_key")
    @classmethod
    def secret_key_must_be_set(cls, v: str) -> str:
        if not v or v.startswith("dev-") or len(v) < 16:
            raise ValueError(
                "SECRET_KEY must be set to a random string of at least 16 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @field_validator("admin_password")
    @classmethod
    def admin_password_must_be_set(cls, v: str) -> str:
        if not v or v in ("admin", "password", "1234", "123456"):
            raise ValueError(
                "ADMIN_PASSWORD must be set to a non-trivial password in .env"
            )
        return v

    class Config:
        env_file = str(_ENV_FILE)
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

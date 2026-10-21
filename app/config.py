from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_CREDENTIALS = {
    "secret_key": "change-this-development-secret-key-32chars",
    "admin_password": "Admin123!",
    "reviewer_password": "Reviewer123!",
    "submitter_password": "Submitter123!",
    "minio_access_key": "minioadmin",
    "minio_secret_key": "minioadmin",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "工业图档异步处理与审核平台"
    app_env: str = "development"
    secret_key: str = "change-this-development-secret-key-32chars"
    access_token_minutes: int = 480
    database_url: str = "postgresql+psycopg://industrial:industrial@localhost:5432/industrial"
    redis_url: str = "redis://localhost:6379/1"
    celery_broker_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    raw_bucket: str = "raw"
    result_bucket: str = "results"

    max_upload_bytes: int = 50 * 1024 * 1024
    active_task_limit: int = 20
    review_lease_minutes: int = 30
    execution_lease_seconds: int = 120
    retry_max_retries: int = 3
    retry_base_seconds: int = 2
    retry_max_seconds: int = 60
    worker_concurrency: int = 4
    outbox_poll_seconds: float = 0.5
    outbox_batch_size: int = 50

    admin_email: str = "admin@example.com"
    admin_password: str = "Admin123!"
    reviewer_email: str = "reviewer@example.com"
    reviewer_password: str = "Reviewer123!"
    submitter_email: str = "submitter@example.com"
    submitter_password: str = "Submitter123!"

    static_dir: Path = Field(default=Path(__file__).parent / "static")
    template_dir: Path = Field(default=Path(__file__).parent / "templates")

    @model_validator(mode="after")
    def _reject_default_secrets_in_production(self) -> "Settings":
        """生产环境使用演示默认凭据时直接拒绝启动，而不是带着已知口令上线。"""
        if self.app_env.lower() != "production":
            return self
        unchanged = [
            name for name, default in _DEFAULT_CREDENTIALS.items()
            if str(getattr(self, name)) == default
        ]
        if unchanged:
            raise ValueError(
                "生产环境必须覆盖以下默认凭据: " + ", ".join(unchanged))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

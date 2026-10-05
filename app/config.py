from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    return Settings()

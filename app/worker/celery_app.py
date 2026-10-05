from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "industrial_document_platform",
    broker=settings.celery_broker_url,
    backend=None,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    task_default_queue="tasks.normal",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
)

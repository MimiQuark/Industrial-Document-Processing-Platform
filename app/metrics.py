from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status_code"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "path"]
)
TASK_STATE = Gauge("tasks_by_status", "Tasks by current status", ["status", "task_type"])
TASK_PROGRESS = Gauge("task_progress_average", "Average task progress")
OUTBOX_PENDING = Gauge("outbox_pending", "Pending outbox events")
DEAD_LETTERS = Gauge("dead_letters_open", "Open dead letters")
REVIEW_PENDING = Gauge("review_cases_pending", "Pending review cases")
QUEUE_DEPTH = Gauge("celery_queue_depth", "Celery queue length", ["queue"])

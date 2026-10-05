from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import admin, auth, health, reviews, tasks
from app.config import get_settings
from app.errors import register_exception_handlers
from app.logging_config import configure_logging, trace_id_var
from app.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.services.storage import storage

settings = get_settings()
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(settings.template_dir))


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    storage.ensure_buckets()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="工业 PDF、SVG、Gerber 图档异步解析与人工审核平台",
    lifespan=lifespan,
)
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(reviews.router)
app.include_router(admin.router)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")


@app.middleware("http")
async def request_context(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID")
    if not trace_id or len(trace_id) > 64:
        trace_id = uuid4().hex
    request.state.trace_id = trace_id
    token = trace_id_var.set(trace_id)
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Trace-ID"] = trace_id
        return response
    finally:
        latency = time.perf_counter() - started
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        HTTP_REQUESTS.labels(request.method, path, str(status_code)).inc()
        HTTP_LATENCY.labels(request.method, path).observe(latency)
        logger.info(
            "request completed",
            extra={"status_code": status_code, "latency_ms": round(latency * 1000, 2)},
        )
        trace_id_var.reset(token)


def render(request: Request, name: str, **context):
    return templates.TemplateResponse(request=request, name=name, context=context)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render(request, "tasks.html", active="tasks")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", active="login")


@app.get("/tasks/new", response_class=HTMLResponse)
def new_task_page(request: Request):
    return render(request, "task_new.html", active="tasks")


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail_page(request: Request, task_id: UUID):
    return render(request, "task_detail.html", active="tasks", task_id=str(task_id))


@app.get("/reviews", response_class=HTMLResponse)
def reviews_page(request: Request):
    return render(request, "reviews.html", active="reviews")


@app.get("/reviews/{review_id}", response_class=HTMLResponse)
def review_detail_page(request: Request, review_id: UUID):
    return render(request, "review_detail.html", active="reviews", review_id=str(review_id))


@app.get("/admin/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    return render(request, "rules.html", active="rules")


@app.get("/admin/dead-letters", response_class=HTMLResponse)
def dead_letters_page(request: Request):
    return render(request, "dead_letters.html", active="dlq")


@app.get("/admin/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    return render(request, "audit.html", active="audit")

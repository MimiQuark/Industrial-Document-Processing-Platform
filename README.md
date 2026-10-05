# 工业图档异步处理与审核平台

[![CI](https://github.com/MimiJimmy001/industrial-document-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/MimiJimmy001/industrial-document-platform/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

基于 Python 3.12、FastAPI、Celery、Redis、PostgreSQL 和 MinIO 的可运行后端平台。支持上传 PDF、SVG、Gerber 图档，异步解析和规则分流，并对告警任务执行带并发锁的人工审核。

## 核心能力

- 文件上传、SHA256 内容去重、`Idempotency-Key` 幂等提交
- 数据库事务 Outbox 与 Redis/Celery 异步任务投递
- 任务状态机、进度、取消、自动重试和手工重试
- 指数退避、执行租约恢复、重试耗尽进入死信队列
- PDF、SVG、Gerber 基础真实解析
- 版本化规则配置、自动通过/失败/转人工
- 审核领取锁、禁止自审、通过/驳回和不可覆盖的审核结论
- append-only 哈希链审计日志
- SSE 实时进度、trace ID、结构化日志和 Prometheus 指标
- Jinja2 中文管理页与完整 OpenAPI 文档

## 快速启动

前置要求：Docker Desktop，至少分配 4 GB 内存。

```bash
docker compose up --build
```

服务地址：

| 服务 | 地址 |
| --- | --- |
| 管理页 | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/health/ready |
| Prometheus | http://localhost:9090 |
| MinIO Console | http://localhost:9001 |

首次启动会自动执行 Alembic 迁移、创建 MinIO bucket 和种子账号。

如果所在网络无法直接访问 Docker Hub，可在 `.env` 中通过 `POSTGRES_IMAGE`、`REDIS_IMAGE`、`MINIO_IMAGE`、`PROMETHEUS_IMAGE` 切换到可信镜像仓库；不要使用来源不明的镜像。

## 演示账号

| 角色 | 邮箱 | 密码 |
| --- | --- | --- |
| 管理员 | `admin@example.com` | `Admin123!` |
| 审核员 | `reviewer@example.com` | `Reviewer123!` |
| 提交者 | `submitter@example.com` | `Submitter123!` |

生产部署必须通过环境变量替换默认密码和 `SECRET_KEY`。

## 任务状态

```text
QUEUED -> RUNNING -> SUCCEEDED
                  -> REVIEW_PENDING -> SUCCEEDED / FAILED
                  -> RETRYING -> RUNNING
                  -> DEAD
                  -> FAILED
                  -> CANCELING -> CANCELED
```

critical 规则命中时任务失败；仅有 warning 时创建人工审核单；无告警时自动成功。

## 接口概览

- `POST /api/v1/tasks`：上传并创建任务，必须提供 `Idempotency-Key`
- `GET /api/v1/tasks`、`GET /api/v1/tasks/{id}`：列表与详情
- `POST /api/v1/tasks/{id}/cancel`、`retry`：取消与重试
- `GET /api/v1/tasks/{id}/events`：SSE 进度
- `GET /api/v1/tasks/{id}/download?kind=raw|result`：文件下载
- `GET /api/v1/reviews`、`claim`、`release`、`decision`：人工审核
- `GET/POST/PATCH /api/v1/admin/rules`：规则版本管理
- `GET/POST /api/v1/admin/dead-letters/*`：死信查看与重投
- `GET /api/v1/admin/audit`、`POST /api/v1/admin/audit/verify`：审计与完整性校验

完整契约以 Swagger 或 `app.openapi()` 为准。

## 本地质量检查

容器内 Python 版本为 3.12。若本机为受项目支持的 Python 版本：

```bash
pip install -e ".[dev]"
ruff check app tests alembic
mypy app
pytest -q
```

端到端测试：

```bash
$env:E2E_BASE_URL = "http://localhost:8000"
pytest tests/test_e2e.py -q
```

## 项目结构

```text
app/
  api/           REST 与 SSE 路由
  domain/        状态机、枚举和规则判断
  parsers/       PDF、SVG、Gerber 解析器
  services/      任务、审核、审计、存储和事件服务
  worker/        Celery 应用与任务执行
  templates/     管理页
alembic/         数据库迁移
tests/           单元与 E2E 测试
docs/            架构、Runbook 和安全说明
```

更多实现说明见 [架构文档](docs/architecture.md)、[运行手册](docs/runbook.md) 和 [安全边界](docs/security.md)。

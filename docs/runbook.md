# 运行手册

## 启动与停止

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api worker scheduler
docker compose down
```

保留数据卷使用普通 `down`；确认需要清空本地数据时才使用 `docker compose down -v`。

## 健康检查

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
curl http://localhost:8000/metrics
```

`/health/ready` 中任一依赖为 `error` 时不应接收流量。

## 常见故障

### 任务长期处于 RUNNING

1. 查看 `docker compose logs worker`。
2. 检查 `/metrics` 中 `celery_queue_depth` 和 `outbox_pending`。
3. 如果 Worker 崩溃，Scheduler 会在执行租约过期后自动恢复到 `RETRYING`。
4. 检查任务详情中的 `task_executions` 和 TaskEvent，确认是否存在重复消息或持续故障。

### Outbox 持续积压

- 确认 Redis 可访问且 Celery Broker URL 正确。
- 检查 Scheduler 日志中的 `last_error` 对应错误。
- 恢复 Redis 后 Scheduler 会按退避时间重新发布。

### 任务进入 DEAD

1. 在管理页“死信”查看原因。
2. 修复外部依赖或文件问题。
3. 点击“重新入队”，平台创建新的 Outbox 事件。
4. 若失败原因来自审核驳回，应提交新任务，不能重投旧任务。

### 审核单被锁

- 审核单租约默认 30 分钟。
- 持有者可点击“释放审核单”。
- 管理员可以领取过期审核单。

## 数据库迁移

```bash
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate python -m app.bootstrap
```

发布前先备份 PostgreSQL。迁移应向前兼容，代码回滚前确认旧版本可读取新 schema。

## 数据恢复

PostgreSQL、Redis 和 MinIO 使用命名卷。生产环境应配置：
- PostgreSQL 定时全量备份和 WAL 归档
- MinIO 版本控制或远端复制
- Redis AOF 与持久化监控
- 定期恢复演练

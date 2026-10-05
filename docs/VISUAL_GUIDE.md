# 项目可视化与可解释性指南

本页用 Mermaid 文本描述系统结构、任务生命周期、并发审核和验证证据。所有图都可以直接在 GitHub 渲染、编辑和代码评审，不依赖外部图片。

## 图 1：项目能力思维导图

```mermaid
flowchart TD
    ROOT((工业图档审核平台))
    ROOT --> BIZ[业务能力]
    ROOT --> ARCH[系统架构]
    ROOT --> REL[可靠性]
    ROOT --> SEC[安全边界]
    ROOT --> OBS[可观测性]
    ROOT --> QA[质量保障]

    BIZ --> B1[PDF / SVG / Gerber 上传]
    BIZ --> B2[异步解析与规则分流]
    BIZ --> B3[人工审核闭环]
    BIZ --> B4[任务重试与取消]

    ARCH --> A1[FastAPI API]
    ARCH --> A2[Celery Worker]
    ARCH --> A3[PostgreSQL]
    ARCH --> A4[Redis]
    ARCH --> A5[MinIO]

    REL --> R1[事务 Outbox]
    REL --> R2[执行租约]
    REL --> R3[幂等消费]
    REL --> R4[死信队列]

    SEC --> S1[JWT + RBAC]
    SEC --> S2[Argon2]
    SEC --> S3[安全 XML]
    SEC --> S4[哈希链审计]

    OBS --> O1[Trace ID]
    OBS --> O2[SSE 进度]
    OBS --> O3[Prometheus]
    OBS --> O4[结构化日志]

    QA --> Q1[单元测试]
    QA --> Q2[Docker Compose E2E]
    QA --> Q3[Ruff + Mypy]
```

## 图 2：运行时组件与数据流

```mermaid
flowchart LR
    USER[提交者 / 审核员] --> API[FastAPI]
    API --> PG[(PostgreSQL)]
    API --> REDIS[(Redis)]
    API --> MINIO[(MinIO)]

    PG --> OUTBOX[Outbox Scheduler]
    OUTBOX --> REDIS
    REDIS --> WORKER[Celery Worker]
    WORKER --> PG
    WORKER --> MINIO
    WORKER --> EVENTS[Task Events]

    API --> SSE[SSE 进度流]
    EVENTS --> SSE
    API --> METRICS[Prometheus Metrics]
```

数据库保存任务、审核、审计和 Outbox 的权威状态；Redis 只承担消息、事件和队列职责；MinIO 保存原始图档与解析结果。

## 图 3：任务状态机

```mermaid
stateDiagram-v2
    [*] --> QUEUED
    QUEUED --> RUNNING
    RUNNING --> SUCCEEDED
    RUNNING --> RETRYING
    RUNNING --> REVIEW_PENDING
    RUNNING --> FAILED
    RETRYING --> RUNNING
    RETRYING --> DEAD
    REVIEW_PENDING --> SUCCEEDED
    REVIEW_PENDING --> FAILED
    QUEUED --> CANCELING
    RUNNING --> CANCELING
    CANCELING --> CANCELED
    DEAD --> QUEUED: 管理员重投
```

关键解释：`REVIEW_PENDING` 只由 warning 规则触发；critical 规则直接失败；重试耗尽进入 `DEAD`，管理员重投会创建新的执行记录，而不是覆盖旧历史。

## 图 4：并发审核时序

```mermaid
sequenceDiagram
    participant R1 as 审核员 A
    participant API as Review API
    participant DB as PostgreSQL
    participant R2 as 审核员 B

    R1->>API: 领取审核单
    API->>DB: 条件更新 + FOR UPDATE SKIP LOCKED
    DB-->>API: 锁定成功
    API-->>R1: 返回 30 分钟租约
    R2->>API: 尝试领取同一审核单
    API->>DB: 查询可领取记录
    DB-->>API: 已被占用
    API-->>R2: 409 / 不可领取

    R1->>API: 提交通过或驳回
    API->>DB: 锁定审核单和任务
    DB->>DB: 校验禁止自审
    DB->>DB: 写入不可覆盖结论
    DB->>DB: 追加哈希链审计事件
    API-->>R1: 返回决策结果
```

## 图 5：可解释性与故障诊断地图

```mermaid
flowchart TD
    SYMPTOM[异常现象] --> S1[任务长期 RUNNING]
    SYMPTOM --> S2[Outbox 持续积压]
    SYMPTOM --> S3[任务进入 DEAD]
    SYMPTOM --> S4[审核单被锁]
    SYMPTOM --> S5[审计校验失败]

    S1 --> C1[检查 Worker 日志 / 租约 / 执行记录]
    S2 --> C2[检查 Redis / Scheduler / last_error]
    S3 --> C3[查看 DLQ 原因并修复依赖]
    S4 --> C4[等待租约释放 / 管理员接管]
    S5 --> C5[比对审计事件哈希链]

    C1 --> METRIC[查看 /metrics 与 TaskEvent]
    C2 --> METRIC
    C3 --> METRIC
    C4 --> METRIC
    C5 --> METRIC
```

## 视觉阅读顺序

1. 先看图 1，了解项目能力边界。
2. 再看图 2，理解 API、数据库、消息和对象存储的职责。
3. 用图 3 解释任务为什么不会只停留在 `RUNNING`。
4. 用图 4 回答审核并发和职责分离问题。
5. 用图 5 说明可观测性和故障排查路径。
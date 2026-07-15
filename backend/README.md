# Backend Developer Guide

`backend/` 提供论文解析与多 Agent 编排、FastAPI 接口、持久任务、Celery Worker、检索/问答/比较服务以及报告导出。本页只说明后端开发；产品能力和完整架构见[根 README](../README.md)。

## 职责与组件

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| FastAPI | `api/` | 上传校验、任务/会话/比较协议、持久 Store、SSE、错误脱敏和产物下载。 |
| 工作流 | `core/` | 端到端编排、`AnalysisState`、阶段检查点、取消、报告质量门。 |
| Agent | `agents/` | Planner、Reader、Critic、Writer、Verifier；通过 Pydantic Schema 交换数据。 |
| 文档与检索 | `tools/`、`ask_retrieval.py`、`document_search.py` | 版面感知 PDF 解析、稳定分块、BM25、向量索引、RRF 和安全降级。 |
| 异步执行 | `worker/` | Celery 分析、Ask Paper 和 Compare Papers 任务。 |
| 论文问答 | `ask_paper.py`、`api/ask_store.py` | 范围校验、上下文预算、Evidence 快照、持久消息与流事件。 |
| 跨论文比较 | `comparisons/`、`api/comparison_store.py` | 来源快照、七维矩阵、引用白名单和比较产物。 |
| 导出 | `exporters/` | Markdown、JSON、HTML、PDF、DOCX 与会话归档。 |
| 配置与 Prompt | `core/config.py`、`prompts/` | 环境变量、模型/请求策略和可版本化 Markdown Prompt。 |

主要运行关系：

```mermaid
flowchart LR
    Client --> API[FastAPI]
    API --> DB[(PostgreSQL / SQLite)]
    API --> Redis[(Redis)]
    Redis --> Worker[Celery Worker]
    Worker --> DB
    Worker --> Workflow[Analysis / Ask / Comparison]
    Workflow --> Files[(backend/outputs)]
    API --> Files
```

SQLite 是轻量 CLI/同步接口/测试配置。完整后台任务需要 API 与 Worker 共享 PostgreSQL，并通过 Redis 传递任务；Docker Compose 已提供该组合。

## 开发启动

以下命令均从仓库根目录运行。

### 安装依赖

```bash
uv sync
cp backend/.env.example backend/.env
```

默认配置使用 Mock LLM、Mock Embedding 和 SQLite，不需要 API Key。

### 轻量 API

```bash
uv run uvicorn backend.api.main:app --reload
```

- API：<http://127.0.0.1:8000>
- Swagger：<http://127.0.0.1:8000/docs>
- ReDoc：<http://127.0.0.1:8000/redoc>
- 健康检查：<http://127.0.0.1:8000/api/health>

SQLite 配置适合健康检查、同步 `POST /api/analyze/upload`、只读开发和测试。`POST /api/tasks/analyze` 等后台入口仍需要一个可与 API 共享 Broker 的 Worker。

### API + Worker

先启动基础设施：

```bash
docker compose up -d postgres redis
```

将 `backend/.env` 的数据库改为宿主机地址，Redis 默认值已指向 `localhost:6379`：

```env
DATABASE_URL=postgresql+psycopg://paper_reader:paper_reader@localhost:5432/paper_reader
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

分别启动 API 和 Worker：

```bash
uv run uvicorn backend.api.main:app --reload
```

```bash
uv run celery -A backend.worker.celery_app:celery_app worker --loglevel=INFO
```

数据库表由应用 Store 初始化。需要一键运行完整五服务栈时，在根目录复制 `.env.example` 后使用 `docker compose up --build`。

### CLI

CLI 同步执行完整分析，不经过 API、Redis 或 Celery：

```bash
uv run python -m backend.app.cli \
  --pdf backend/data/raw/layout_demo.pdf \
  --output backend/outputs/reports/layout-demo-report.md \
  --state-json backend/outputs/logs/layout-demo-state.json \
  --language zh \
  --verbose
```

如果演示 PDF 不存在，可先生成：

```bash
uv run python -m backend.scripts.generate_layout_demo_pdf
```

## 环境配置

宿主机后端从 `backend/.env` 读取配置，模板为 [`backend/.env.example`](.env.example)。Compose 从仓库根 `.env` 读取，模板为 [`.env.example`](../.env.example)。不要提交真实密钥。

| 配置组 | 主要变量 |
| --- | --- |
| 模型 | `LLM_PROVIDER`、`LLM_VENDOR`、`LLM_MODEL`、`LLM_API_KEY`、`LLM_BASE_URL` |
| Embedding | `EMBEDDING_PROVIDER`、`EMBEDDING_MODEL`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL` |
| 数据与队列 | `DATABASE_URL`、`CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND` |
| 任务恢复 | `TASK_HEARTBEAT_SECONDS`、`TASK_STALE_AFTER_SECONDS`、`CHECKPOINT_SCHEMA_VERSION`、`SSE_HEARTBEAT_SECONDS` |
| 解析与分块 | `PDF_LAYOUT_MODE`、`CHUNK_SIZE`、`CHUNK_OVERLAP`、`DEFAULT_TOP_K` |
| Ask/Search | `ASK_CANDIDATE_COUNT`、`ASK_EVIDENCE_COUNT`、`ASK_RRF_K`、`ASK_INDEX_DIR`、`ASK_RERANKER_*` |
| 外部请求 | `REQUEST_CONNECT_TIMEOUT`、`REQUEST_READ_TIMEOUT`、`REQUEST_TOTAL_BUDGET`、`REQUEST_MAX_RETRIES` |
| 生命周期 | `MAX_UPLOAD_BYTES`、`FILE_RETENTION_DAYS`、`PROMPT_SET_VERSION` |
| 报告质量 | `VERIFIER_ENABLED`、`QUALITY_PASS_SCORE`、`CITATION_VALIDITY_MIN_SCORE` |

离线模式：

```env
LLM_PROVIDER=mock
EMBEDDING_PROVIDER=mock
```

OpenAI-compatible 模式：

```env
LLM_PROVIDER=openai_compatible
LLM_VENDOR=custom
LLM_MODEL=your-chat-model
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://your-provider.example/v1

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_VENDOR=custom
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=your-api-key
EMBEDDING_BASE_URL=https://your-provider.example/v1
```

LLM 与 Embedding 可以独立配置。真实服务调用可能产生费用并把论文内容发送到外部端点。

## API 与 Worker

根 README 提供[分组概览](../README.md#api-能力概览)，完整字段以 Swagger 为准。后端主要路由为：

```text
GET    /api/health
POST   /api/analyze/upload

GET    /api/tasks
POST   /api/tasks/analyze
GET    /api/tasks/{task_id}
GET    /api/tasks/{task_id}/detail
POST   /api/tasks/{task_id}/{cancel|retry|resume|rerun}
GET    /api/tasks/{task_id}/events
GET    /api/tasks/{task_id}/{report|report/structured|evidence/{evidence_id}|artifacts/{format}}
POST   /api/tasks/{task_id}/search

GET|POST /api/tasks/{task_id}/conversations
GET|PATCH|DELETE /api/conversations/{conversation_id}
POST   /api/conversations/{conversation_id}/messages
POST   /api/conversations/{conversation_id}/messages/{message_id}/{cancel|retry}
GET    /api/conversations/{conversation_id}/messages/{message_id}/events

GET|POST /api/comparisons
GET|PATCH|DELETE /api/comparisons/{comparison_id}
POST   /api/comparisons/{comparison_id}/{cancel|retry}
GET    /api/comparisons/{comparison_id}/{events|report|report/structured|evidence/{evidence_id}|artifacts/{format}}
```

`{format}` 对单篇报告和比较报告支持 `markdown | json | html | pdf | docx`，会话归档支持 `markdown | json`。

Celery 入口位于 `backend.worker.tasks`：

- `analyze_paper`：读取任务、按阶段保存检查点、导出报告并预构建检索索引。
- `answer_paper_question`：检索范围内 Evidence，持久化 token/终态事件。
- `compare_papers`：加载来源快照、生成矩阵与综合报告、清理非白名单引用并导出。

任务、问答和比较 SSE 都保存递增序号，支持通过 `after`/`Last-Event-ID` 续传。取消是阶段边界的协作式取消；正在进行的外部请求需要先返回或超时。

## 测试与检查

```bash
# 与当前 CI 一致的后端测试
uv run pytest backend/tests -q -rs

# 静态检查
uv run ruff check backend

# 检索评估（固定离线夹具）
uv run python -m backend.evaluation.ask_paper \
  --mode all \
  --gate \
  --output backend/outputs/logs/ask-paper-eval.json
```

真实模型测试默认跳过；只有 `RUN_REAL_LLM_TESTS=1` 时才会执行。默认全量回归已包含 `backend/tests/test_api_tasks.py`，不再使用 API 测试忽略参数。

仓库级交付检查还包括：

```bash
docker compose config --quiet
(cd frontend && npm test && npm run lint && npm run build)
git diff --check
```

## 运行产物

| 路径 | 内容 |
| --- | --- |
| `backend/data/raw/` | 本地输入和生成的演示 PDF。 |
| `backend/data/tasks.db` | SQLite 轻量任务数据库。 |
| `backend/outputs/uploads/` | 后台任务上传 PDF。 |
| `backend/outputs/reports/` | 单篇报告、五格式导出与 `comparisons/`。 |
| `backend/outputs/logs/` | 完整 `AnalysisState`、CLI/API 状态和评估日志。 |
| `backend/outputs/indexes/` | `ask-retrieval-index-v1` 持久检索索引。 |

Compose 将 `backend/outputs` 映射到 `runtime_data` 卷。终态任务可通过 API 永久删除；应用启动时按 `FILE_RETENTION_DAYS` 清理过期文件。运行产物、上传 PDF、私有评估数据和 `.env` 不应提交到公开仓库。

## 专项文档

- [项目主页与完整架构](../README.md)
- [前端开发指南](../frontend/README.md)
- [文档检索增强设计](../docs/document-retrieval-enhancement.md)
- [Ask Paper 检索评估](evaluation/README.md)
- [示例阅读报告](outputs/reports/example_report.md)

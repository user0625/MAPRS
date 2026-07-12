# Multi-Agent Paper Reader System

## 1. Project Overview

Multi-Agent Paper Reader System 是一个面向科研论文阅读的多智能体 MVP。系统接收本地 PDF，通过文档解析、文本分块和检索增强，为多个职责明确的 Agent 提供上下文，最终生成结构化的 Markdown 论文阅读报告。

项目采用 Planner + Specialized Agents 架构：Planner 规划阅读任务，Reader 提取论文内容，Critic 进行批判性分析，Writer 汇总生成报告。所有 Agent 通过统一的 LLM Client 调用模型，并使用 Pydantic Schema 约束输入和输出。

当前项目重点是展示一条清晰、可测试、可扩展的端到端 Agent 工作流，而不是提供生产级论文管理平台。

## 2. Key Features

- 解析本地文本型 PDF，并保留页码信息。
- 按可配置长度和重叠区间切分论文文本。
- 使用 embedding、内存向量库和相似度检索构建轻量 RAG 流程。
- 通过 Planner、Reader、Critic、Writer 四个 Agent 分工完成论文分析。
- 使用 Pydantic 校验 Agent 的结构化 JSON 输出。
- 从 `prompts/` 中加载可独立迭代的 Markdown Prompt 模板。
- 宽容提取被代码块或解释文字包裹、含尾随逗号的 JSON，并在 Schema 校验失败时把错误反馈给模型自动重试。
- 支持完全离线、可重复的 mock 模式。
- 支持 OpenAI-compatible LLM 和 embedding API，例如阿里云百炼 Qwen。
- 通过 CLI 输出中文或英文 Markdown 报告，并可选保存完整运行状态 JSON。
- 提供 FastAPI 同步上传分析、Celery 后台任务 API，以及健康检查、任务状态、报告查询和 Swagger/ReDoc 文档。
- 为已完成任务提供持久化 Ask Paper 会话、章节检索、流式回答恢复、Evidence、取消和失败重试。
- 为 LLM 与 embedding 提供统一超时、指数退避和有限总预算；仅重试连接错误、超时、429 与 5xx。
- 严格校验 PDF 上传并支持活动任务去重、协作式取消、失败/取消后重试和文件保留期清理。
- 记录 Prompt Set、模板哈希及结构化输出成功/重试/失败统计，不保存模型原始响应。
- 提供单元测试、组件集成测试和显式启用的真实模型 smoke tests。

## 3. System Architecture

```mermaid
flowchart TD
    ENTRY[Typer CLI / FastAPI] --> ORCH[PaperAnalysisOrchestrator]
    ORCH --> PDF[PDF Loader]
    PDF --> CHUNK[Document Chunker]
    CHUNK --> PLAN[Planner Agent]
    PLAN --> INDEX[Embedder + Vector Store]
    INDEX --> RETRIEVE[Paper Retriever]
    RETRIEVE --> READ[Reader Agent]
    READ --> CRITIC[Critic Agent]
    CRITIC --> WRITER[Writer Agent]
    WRITER --> EXPORT[Markdown / State JSON Exporter]
    ORCH <--> STATE[Analysis State]
    LLM[Mock or OpenAI-compatible LLM] --> PLAN
    LLM --> READ
    LLM --> CRITIC
    LLM --> WRITER
    PROMPT[Markdown Prompt Templates] --> PLAN
    PROMPT --> READ
    PROMPT --> CRITIC
    PROMPT --> WRITER
```

核心模块之间通过 Schema 传递数据：`PaperDocument` 保存解析后的论文，`AnalysisPlan` 定义任务计划，`EvidenceBundle` 保存检索证据，`ReaderNotes` 和 `CriticNotes` 保存中间分析，`FinalReport` 表示最终报告。

## 4. Workflow

1. `PDFLoader` 从本地 PDF 提取逐页文本和基础元数据。
2. `DocumentChunker` 生成带页码和稳定 ID 的文本分块。
3. `PlannerAgent` 根据论文元数据和用户问题生成分析任务与关注问题。
4. Embedder 将论文分块向量化，`NumpyVectorStore` 在内存中建立索引。
5. `PaperRetriever` 按 Planner 的关注问题检索相关证据。
6. `ReaderAgent` 基于证据忠实提取研究问题、贡献、方法和实验信息。
7. `CriticAgent` 分析优点、局限、缺失实验、可靠性和可复现性。
8. `WriterAgent` 汇总上述结果，生成中文或英文结构化报告。
9. `ReportExporter` 保存 Markdown，并可选保存完整 `AnalysisState` JSON；API 可同步返回结果，或在后台任务中更新 SQLite 任务状态供轮询。

当前 Orchestrator 按上述顺序串行执行，各步骤状态会记录在 `step_history` 中。

## 5. Tech Stack

- Python 3.12+
- Pydantic v2 / pydantic-settings：Schema 与环境配置
- PyMuPDF：PDF 文本提取
- NumPy：内存向量存储与余弦相似度检索
- OpenAI Python SDK：OpenAI-compatible 模型接口
- FastAPI / Uvicorn：HTTP API 与开发服务器
- Typer / Rich：命令行入口与运行状态展示
- pytest：单元测试、集成测试和真实模型 smoke tests
- uv：依赖和虚拟环境管理

## 6. Project Structure

```text
backend/
├── agents/                 # BaseAgent、Planner、Reader、Critic、Writer
├── api/                    # FastAPI 应用、同步分析与后台任务路由
├── app/
│   ├── cli.py              # 当前可用的命令行入口
│   └── streamlit_app.py    # 预留的 Web UI 入口，尚未实现
├── core/
│   ├── config.py           # 环境变量与运行配置
│   ├── orchestrator.py     # 端到端工作流编排
│   └── state.py            # 工作流状态与步骤记录
├── exporters/              # Markdown 和 JSON 导出
├── llm/                    # Mock 与 OpenAI-compatible LLM Client
├── prompts/                # 可外置迭代的 Markdown Prompt 模板
├── schemas/                # 论文、Agent I/O、报告数据模型
├── tools/                  # PDF、分块、embedding、检索、向量存储
├── tests/                  # 单元、集成和真实 API 测试
├── data/raw/               # 示例与本地输入 PDF
├── outputs/reports/        # 生成的阅读报告
├── outputs/uploads/        # API 上传文件（运行时生成）
├── outputs/logs/           # API/CLI 状态 JSON（运行时生成）
├── .env.example            # 安全的环境变量模板
└── README.md
```

## 7. Installation

以下命令均在仓库根目录执行。

```bash
git clone <your-repository-url>
cd Multi-Agent_Paper_Reader_System_Design
uv sync
cp backend/.env.example backend/.env
```

验证 CLI 是否安装成功：

```bash
uv run python -m backend.app.cli --help
```

默认配置使用 mock LLM 和 mock embedding，不需要 API Key，也不会访问外部模型服务。

## 8. Environment Configuration

应用从 `backend/.env` 读取配置。请复制 `.env.example` 后修改，切勿提交包含真实密钥的 `.env`。

### Offline mock mode

```env
LLM_PROVIDER=mock
LLM_VENDOR=mock
LLM_MODEL=mock-llm

EMBEDDING_PROVIDER=mock
EMBEDDING_VENDOR=mock
EMBEDDING_MODEL=mock-embedding
```

该模式适合本地演示、开发和测试。它会执行完整工作流，但 Agent 输出和 embedding 是确定性的模拟结果，不代表真实语义分析质量。

### Qwen through DashScope

```env
LLM_PROVIDER=openai_compatible
LLM_VENDOR=qwen
LLM_MODEL=qwen-max
LLM_API_KEY=your_dashscope_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_VENDOR=qwen
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_API_KEY=your_dashscope_api_key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

LLM 与 embedding 可以独立配置。例如使用 DeepSeek LLM 时，可以继续使用 mock embedding，或者配置另一个兼容 embedding 服务。

主要运行参数：

| Variable | Default | Description |
| --- | --- | --- |
| `DEFAULT_TOP_K` | `5` | 默认检索结果数量 |
| `CHUNK_SIZE` | `1200` | 单个文本分块的目标字符数 |
| `CHUNK_OVERLAP` | `150` | 相邻分块的重叠字符数 |
| `DATABASE_URL` | `sqlite:///backend/data/tasks.db` | 任务历史 SQLite 数据库 |
| `REQUEST_CONNECT_TIMEOUT` / `REQUEST_READ_TIMEOUT` | `10` / `60` | 外部请求连接与读取超时（秒） |
| `REQUEST_TOTAL_BUDGET` | `120` | 单次任务请求重试总预算（秒） |
| `REQUEST_MAX_RETRIES` | `2` | 可重试请求的最大重试次数 |
| `REQUEST_BACKOFF_BASE` / `REQUEST_BACKOFF_MAX` | `1` / `8` | 指数退避基数与上限（秒） |
| `MAX_UPLOAD_BYTES` | `52428800` | 上传上限，默认 50 MiB |
| `FILE_RETENTION_DAYS` | `30` | 终态任务产物保留天数 |
| `PROMPT_SET_VERSION` | `v1` | Prompt 集版本标识 |
| `RUN_REAL_LLM_TESTS` | `0` | 是否执行真实 API 测试；仅值为 `1` 时启用 |

## 9. Usage

### Offline demo

确认 `backend/.env` 使用 mock 配置，然后运行：

```bash
uv run python -m backend.app.cli \
  --pdf backend/data/raw/example.pdf \
  --output backend/outputs/reports/report.md \
  --language zh \
  --verbose
```

生成英文报告并保存完整状态：

```bash
uv run python -m backend.app.cli \
  --pdf backend/data/raw/example.pdf \
  --output backend/outputs/reports/report_en.md \
  --state-json backend/outputs/reports/state.json \
  --language en
```

CLI 参数：

- `--pdf, -p`：输入 PDF 路径，必填。
- `--output, -o`：Markdown 报告输出路径。
- `--query, -q`：自定义论文分析要求。
- `--language, -l`：`zh` 或 `en`。
- `--verbose, -v`：打印各工作流步骤。
- `--state-json`：可选的完整状态 JSON 输出路径。

真实模型模式使用相同命令，只需先在 `backend/.env` 中配置有效 API。真实调用依赖网络、服务配额和模型可用性，并可能产生费用。

### FastAPI

启动开发服务器：

```bash
uv run uvicorn backend.api.main:app --reload
```

健康检查与交互式文档分别位于 `GET /api/health`、<http://127.0.0.1:8000/docs> 和 <http://127.0.0.1:8000/redoc>。同步上传会等待完整分析完成：

```bash
curl -X POST http://127.0.0.1:8000/api/analyze/upload \
  -F 'file=@backend/data/raw/example.pdf;type=application/pdf' \
  -F 'query=分析这篇论文' -F 'language=zh'
```

后台任务接口会立即返回 `task_id`，随后可轮询状态并读取完成后的报告：

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/analyze \
  -F 'file=@backend/data/raw/example.pdf;type=application/pdf' \
  -F 'language=zh'
curl http://127.0.0.1:8000/api/tasks/{task_id}
curl http://127.0.0.1:8000/api/tasks/{task_id}/report
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/cancel
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/retry
```

任务历史和安全详情接口：

```bash
curl 'http://127.0.0.1:8000/api/tasks?limit=20&offset=0'
curl http://127.0.0.1:8000/api/tasks/{task_id}/detail
```

详情可返回论文标题、作者、工作流步骤摘要和可用的 Markdown 报告；不会返回论文全文分块、Agent 原始中间内容、模型原始响应或 API Key。

### Ask Paper API

Ask Paper 仅接受已完成且 state 产物可用的单篇论文任务。会话、消息、Evidence 和流事件保存在任务数据库中，生成由 Celery Worker 执行，因此浏览器断开不会取消回答。

```text
POST  /api/tasks/{task_id}/conversations
GET   /api/tasks/{task_id}/conversations
GET   /api/conversations/{conversation_id}
PATCH /api/conversations/{conversation_id}
POST  /api/conversations/{conversation_id}/messages
GET   /api/conversations/{conversation_id}/messages/{message_id}/events?after={sequence}
POST  /api/conversations/{conversation_id}/messages/{message_id}/cancel
POST  /api/conversations/{conversation_id}/messages/{message_id}/retry
```

SSE 事件包括 `token`、`completed`、`failed`、`canceled` 和 `heartbeat`。客户端可使用 `after` 或 `Last-Event-ID` 恢复；事件序号和最终消息正文都来自持久化存储。问答 Evidence ID 使用消息命名空间，并可通过现有 `GET /api/tasks/{task_id}/evidence/{evidence_id}` 查询。

数据库升级由 `backend/migrations/versions/0002_ask_paper.py` 提供。任务删除时会同步清理其全部问答数据。

后台上传接口只接受 `.pdf`、`application/pdf` 或 `application/octet-stream`，并校验 `%PDF-` 文件头和大小。相同 SHA-256、标准化 query 与 language 的活动任务会复用原 `task_id`。取消在工作流阶段边界生效；重试仅适用于 `failed/canceled`，并创建通过 `retry_of` 关联的新任务。

每个 HTTP 响应都包含 `X-Request-ID`。错误响应包含兼容的 `detail` 以及 `code`、`request_id`。任务详情的 workflow metadata 会提供 Prompt 版本、模板哈希和结构化输出统计。

上传文件写入 `OUTPUT_DIR/uploads`，报告和状态分别写入 `REPORT_DIR`、`LOG_DIR`；相对路径以 `PROJECT_ROOT` 为基准。

## 10. Testing

运行默认测试套件：

```bash
uv run pytest backend/tests -q -rs
```

普通测试使用 mock 客户端，不需要网络。真实测试默认被跳过。

单独运行组件测试：

```bash
uv run pytest backend/tests/test_retriever.py -v
uv run pytest backend/tests/test_orchestrator.py -v
uv run pytest backend/tests/test_cli.py -v
```

配置好真实 LLM 后，可以显式执行真实 smoke tests：

```bash
RUN_REAL_LLM_TESTS=1 uv run pytest backend/tests/test_planner_agent_real.py -v -s
RUN_REAL_LLM_TESTS=1 uv run pytest backend/tests/test_orchestrator_real.py -v -s
```

真实 Orchestrator 测试还会使用当前 embedding 配置。执行前请确认 API Key、Base URL、模型、网络和账户配额均有效。

## 11. Example Output

仓库可提供一份[示例论文阅读报告](outputs/reports/example_report.md)。示例 PDF 与报告仅应在确认版权、隐私和再分发许可后加入公开仓库。最终报告通常包含：

- 基本信息与 TL;DR
- 研究问题与背景
- 主要贡献
- 方法总结
- 实验与结果
- 优点与局限
- 缺失实验与潜在风险
- 可复现性说明
- 创新性、可靠性与综合评价
- 与正文分块对应的 evidence IDs

实际内容取决于 PDF 文本质量、检索结果、用户 query 和所选模型。mock 模式只用于展示数据流和输出结构。

## 12. Development Roadmap

已完成的基础能力包括 React Web UI、PostgreSQL/SQLite 持久化、Redis + Celery 任务队列、可恢复 SSE、结构化报告与 Evidence，以及单篇论文 Ask Paper。后续工作按优先级为：

1. **检索与问答质量**：BM25/向量混合召回、Rerank、持久化向量库、检索评估集，以及 Ask Paper 忠实度、引用和 prompt-injection 专项回归。
2. **文档理解**：OCR、复杂多栏版面、表格、公式、图片与图注，并保留可引用的位置关系。
3. **Ask Paper 深化**：页码范围、会话搜索/删除/导出、更精细的上下文压缩和成本统计。
4. **多论文研究**：批量/arXiv/DOI/URL 输入、跨论文证据检索、对比矩阵、研究脉络与文献综述。
5. **可靠执行与运维**：可恢复任务图、Worker 水平扩容、Metrics/Tracing、Secret 管理、数据库备份和生产迁移流程。
6. **产品安全**：认证、租户隔离、上传与调用限流、用户配额、审计、安全响应头和数据保留策略。
7. **模型生态**：更多厂商、本地模型和 vendor-specific structured output，并建立 token、延迟和成本看板。

## 13. Notes and Limitations

- 当前端到端入口只支持本地文本型 PDF；`arxiv` 和 `url` 仅在 Schema 中预留。
- 未集成 OCR，扫描版或复杂排版 PDF 的文本提取质量可能较差。
- 标题、作者、摘要、DOI、arXiv、年份、关键词和章节使用 PDF metadata、首页版面与文本规则组合抽取，并记录来源和置信度；扫描件仍需 OCR。
- 创建分析支持 `analysis_depth`、`target_audience`、`report_template` 与受限的 `custom_sections`。报告完成后可通过 `/api/tasks/{task_id}/artifacts/{format}` 按需导出 Markdown、JSON、HTML、PDF 或 DOCX。
- Writer 后始终执行引用可追溯性与证据覆盖检查；未通过时清理无效引用并复核一次，仍不通过则交付带质量警告的报告。
- `LLM_PROVIDER=openai_compatible` 时，标题、作者和 venue 默认由结构化 Metadata Extractor 对首页版面候选进行裁决，其他缺失或低置信字段由其补充。输入严格限制为首页文本块及字号/坐标/旋转方向、摘要候选和章节标题；模型结果必须通过离线候选一致性检查。真实 Verifier 检查报告与证据并产生五维评分、问题和修订指令，Writer 最多修订一次。state 只保存质量摘要，不保存 Verifier 原始响应。
- 元数据裁决不是自由生成：标题、作者和 venue 至少 90% 的词必须回溯到首页候选；旋转 arXiv 标记、日期、机构、邮箱、摘要/章节标题及与标题高度重合的作者结果会被拒绝。模型失败时保留离线结果。
- Orchestrator 当前串行执行，尚未实现真正的并行多 Agent 调度。
- `NumpyVectorStore` 仅保存在进程内，退出后索引不会持久化。
- mock embedding 不具备语义检索能力，mock Agent 输出也不是论文真实分析。
- Prompt 模板可独立修改，但其中的模板变量必须与对应 Agent 的渲染参数匹配。
- JSON 解析器可处理代码块、周边解释文字和尾随逗号；Schema 失败会把校验错误反馈给模型重试。网络错误由独立的共享请求策略处理。
- 长论文和大量检索证据可能受到模型上下文窗口限制。
- 真实 API 调用可能遇到网络超时、鉴权失败、限流、模型不可用和调用费用。
- Compose 后台任务由独立 Celery Worker 执行，任务元数据持久化到 PostgreSQL；兼容检查点的失败任务可恢复。SQLite 仍用于测试和轻量本地开发。
- 状态 JSON 和 Markdown 报告仍是独立文件。文件被移动或删除后历史元数据仍可查询，但详情中的步骤或报告内容不可用；系统不会自动导入旧运行产物。
- 默认上传上限为 50 MiB；终态任务文件默认保留 30 天并在启动时清理，任务元数据仍会保留。
- 取消在阶段边界生效，不会强制终止正在进行的单次模型请求。系统仍没有认证、权限控制、限流和生产部署保障。
# Durable API and worker

Compose starts PostgreSQL, Redis, the API, Celery worker, and Nginx frontend. The API runs `alembic upgrade head` before Uvicorn; the worker uses the same image and shared output volume. The application is available at <http://localhost:3000>, while FastAPI remains directly available at <http://localhost:8000>. Useful commands:

```bash
docker compose logs -f worker
docker compose exec api alembic upgrade head
curl http://localhost:3000/api/health
```

Task operations include `/cancel`, `/retry`, `/resume`, `/rerun`, `DELETE /api/tasks/{id}`, durable SSE `/events`, structured `/report/structured`, and task-scoped `/evidence/{evidence_id}`. SQLite import is always explicit and idempotently skips existing task IDs.

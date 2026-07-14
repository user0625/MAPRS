# 📚 Multi-Agent Paper Reader System

> 基于多智能体协作与轻量 RAG 的科研论文阅读、批判和报告生成系统

Multi-Agent Paper Reader System 是一个面向科研论文阅读场景的全栈 MVP。用户通过 Web 页面上传 PDF、填写分析要求并选择报告语言后，系统会依次完成文档解析、文本分块、检索增强、多 Agent 分析，并生成结构化 Markdown 阅读报告。

项目采用 **Planner + Specialized Agents** 架构：Planner 制定阅读计划，Reader 提取论文内容，Critic 进行批判性分析，Writer 汇总最终报告。相比直接让单个模型总结全文，本项目更关注职责拆分、证据检索、结构化中间状态和可测试的端到端工作流。

当前版本定位为用于功能展示的完整全栈项目：优先保证核心流程闭环、模块边界清晰、异常可降级、功能可演示且测试可重复，而不是追求生产级规模、正式学术评测或复杂基础设施。

当前主线已经覆盖论文分析、独立文档搜索、Ask Paper、多论文对比与持久化任务。[文档检索增强 MVP](docs/document-retrieval-enhancement.md) 已把底层混合检索扩展为用户可直接操作、可解释且可安全降级的任务内搜索闭环。

---

## 📝 最近更新

- `2026-07-14`
  - Search Document MVP：新增 `POST /api/tasks/{task_id}/search` 和独立前端工作区，支持 Auto/BM25、章节、1-based 页码闭区间、Top-K、稳定排序、分数与命中来源解释。
  - 搜索上下文与联动：命中片段围绕查询词截取，passage drawer 展示同章节且不越页码边界的直接相邻 chunk；可复制上下文，并将论文与查询预填到 Ask Paper（不自动发送、不创建伪 Evidence ID）。
  - 安全降级：Auto 复用 `ask-retrieval-index-v1` 与进程内缓存；Mock/未配置 embedding 使用 BM25，索引构建或查询失败显示 `Degraded to BM25`，响应只返回脱敏类别、候选数、耗时和索引来源。
  - 检索索引持久化：新增 `ask-retrieval-index-v1`，只保存 chunk ID、归一化向量、模型和内容摘要；支持原子写入、损坏恢复、自动失效、全文索引范围切片、任务删除与保留期清理。
  - Pilot v3 工程准备：新增脱敏 embedding/reranker preflight、共享 reranker 请求策略，以及 BM25/RRF/候选深度的一次采集与内存离线重放；真实 preflight 当前仍因两个上游均超时而阻止 Pilot 启动。
  - 项目范围调整：以项目的功能完整性和演示闭环为优先，下一阶段实施任务内文档搜索、检索解释、相邻分块上下文和前端搜索面板；正式 gold 与生产 reranker 不作为当前阻塞项。
  - Pilot-only 质量门：新增从专家裁决结果派生的只读 validation 视图，不改写 `cases.jsonl`、不提升正式 `reviewed` 状态、不访问或冻结 test；报告固定标记 `evidence_grade: pilot_only`，脱敏聚合失败归因，并强制生产 reranker 保持 `disabled`。
  - Pilot 最新结论：20 条可接受 validation 样本的 embedding/reranker 降级路径均完成，但当前配置的真实端点在放宽网络后仍不可用，`pilot-only-v2` 尚有 7 项硬门失败，`exit_ready_for_comparison_mvp=false`；该结果不构成正式质量或生产启用结论。
  - 多论文对比 MVP：可从历史中选择 2–5 个唯一的已完成任务，持久化生成逐论文 profile、七维对比矩阵、共同点/差异/冲突/方法演进/适用场景，以及带来源任务、页码、章节和正文快照的跨论文 Evidence。
  - 比较生命周期：新增独立比较状态、顺序化源论文元数据快照、持久 SSE、取消、失败重试、终态删除和活动源任务删除保护；终态比较不再依赖源任务文件。
  - 比较交付：结构化产物版本为 `paper-comparison-v1`，引用在落盘前按比较命名空间白名单清理，并提供 Markdown、JSON、HTML、PDF、DOCX 五种导出。
  - 前端：新增 **Compare Papers** 工作区、持久历史、搜索、进度恢复、响应式横向矩阵、来源论文标签、Evidence 抽屉和桌面/移动端回归。
  - 验证：后端 `196 passed, 6 skipped`，Ruff 通过；前端 Vitest `17 passed`、Oxlint、build 和 Playwright 桌面/移动端通过；Compose、API/Worker/Frontend 三镜像构建和 whitespace 检查通过。
- `2026-07-13`
  - Ask Paper 范围与上下文：新增可与章节组合的 1-based 页码闭区间筛选；消息、重试和归档保留原范围，并以最近消息数、历史 token 预算和 Evidence token 预算自动控制模型输入。
  - Ask Paper 会话管理：支持当前论文内按标题和消息正文搜索、单会话永久删除，以及包含实际引用 Evidence 的 Markdown/JSON 内存导出；生成中的会话需先取消并等待终态。
  - 私有论文评估基础设施：新增真实评估集 Schema、候选生成/校验/校准三个内部 CLI、论文级 split、test 内容冻结及单次冻结质量门；私有 PDF、标注和校准产物继续留在 Git 之外。
  - Pilot 结论：`pilot-v1` 仅完成工程链路验证；122 条样本中 validation 为 20 条，未运行 frozen-test，validation 仍有 8 项硬门槛失败，因此当前保持 BM25 基线与 reranker `disabled`。
  - 检索隐私：shadow 日志只记录问题摘要、延迟、top score 和排序变化，不记录完整问题或论文正文。
  - 验证：后端完整回归 `169 passed, 6 skipped`，评估/检索专项 `35 passed`，Ruff 通过；前端 Vitest `10 passed`、Oxlint、build、Ask Paper Playwright 桌面/移动端和 Compose 配置检查通过。
- `2026-07-12`
  - 检索质量升级：BM25 与向量召回分别扩展到前 20 条候选，保留原始分数、排名和命中来源；改进中英文规范化、停用词及中文字符 bigram，并保证向量阈值不会删除 BM25 命中。
  - Reranker 上线：新增 `disabled`、`shadow`、`enabled` 三种模式，支持阿里云 `qwen3-rerank` 的 `/reranks` 协议、独立 Evidence/answerability 阈值、稳定排序及超时异常自动降级。
  - 质量评估：新增 candidate Recall@20、macro Precision@6、Recall@6、MRR、Evidence coverage、错误拒答率、平均返回数和分组指标；合成集继续用于 CI，生产阈值要求使用按论文隔离的真实验证集校准。
  - PDF 兼容性：修复内嵌标题/作者 metadata 与首页布局候选同时存在时的类型不一致问题，原失败的 9 页测试论文已可解析出标题和 15 个章节。
  - 验证：后端完整回归 `148 passed, 6 skipped`，新增 reranker 协议、双阈值、降级和 PDF metadata 专项测试，Ruff 检查通过。
  - Ask Paper：新增仅面向已完成单篇论文任务的独立问答工作区，支持多轮会话、全篇/章节范围、自动/中英文回答、Evidence 回溯和会话标题编辑。
  - 持久化流式恢复：问答消息、Evidence 和 token 事件写入数据库；Celery Worker 独立生成，浏览器刷新或断线后可按事件序号续传，且支持取消和失败重试。
  - CI 基线：GitHub Actions 在 push 与 pull request 上运行后端测试/Ruff、前端 lint/build、Compose 校验和前后端镜像构建；同分支旧任务自动取消，镜像仅构建不推送。
  - 交互报告：新建分析与任务历史共享结构化阅读组件，支持稳定目录、关键词定位、证据抽屉、旧报告 Markdown 回退及导航状态复原。
  - 容器化：新增 Node 20 + Nginx 前端生产镜像，Compose 统一启动 PostgreSQL、Redis、FastAPI、Celery Worker 与 Frontend。
  - 请求链路：浏览器统一访问 `http://localhost:3000`，Nginx 代理 `/api`、上传下载与无缓冲 SSE；FastAPI 的 `8000` 端口继续保留用于直接调试。
  - 工作区布局：新建分析页左右首个面板顶部对齐；Task History 桌面双栏固定为 560px，详情内部滚动，移动端恢复自然高度。
  - 基础设施验证：五服务镜像构建、容器启动和主要功能测试通过；前端 Vitest/build/lint、Playwright 桌面/移动端、Compose 配置与差异检查通过。
- `2026-07-11`
  - Phase A 稳定性：LLM 与 Embedding 统一超时/重试策略，支持 `Retry-After`、总请求预算和脱敏错误。
  - 任务控制：支持活动任务去重、阶段边界取消、失败/取消任务重试，以及终态文件定期清理。
  - 上传防护：后台与同步接口统一校验扩展名、MIME、PDF 文件头和大小，并在写入时计算 SHA-256。
  - 可追溯性：响应返回 `X-Request-ID`；任务详情记录 Prompt Set、模板哈希和结构化输出统计。
  - 报告交互：新建分析与历史详情中的 Markdown 报告均支持一键复制和下载 `.md` 文件。
  - 历史工作区：调整为顶部 Archive + Analysis Detail、底部 Workflow Timeline + Report；Archive 每页固定三项，时间线和报告可独立折叠。
  - 论文信息：任务详情补充论文标题与作者，兼容从旧任务 state 文件安全读取。
  - 持久化：使用 SQLModel + SQLite 保存任务历史，服务重启后仍可分页查询；中断任务会明确标记为失败。
  - Phase D：新增版面候选 + LLM 元数据裁决、章节识别、Verifier 质量门禁、报告预设和五种格式导出。
  - 验证：后端非 API 测试 `101 passed, 6 skipped`，前端 `npm run build`、相关 Ruff 检查与 `git diff --check` 通过。

## 📊 当前完成度

| 模块 | 状态 | 当前能力 |
| :--- | :---: | :--- |
| PDF 解析与分块 | ✅ 已完成 | 文本型 PDF、版面元数据候选、章节识别、页码和稳定分块 ID |
| 多 Agent 分析 | ✅ 已完成 | Planner、Reader、Critic、Writer 串行协作 |
| 文档检索/RAG | ✅ 搜索 MVP 完成 | Search Document、BM25 + 向量 + RRF、持久索引、范围过滤、相邻上下文、分数解释与安全降级 |
| 结构化报告 | ✅ 已完成 | 中英文 Markdown、Schema 校验、状态 JSON |
| Web 分析工作区 | ✅ 已完成 | 上传、参数配置、状态轮询、失败反馈、报告展示 |
| 任务持久化与历史 | ✅ 已完成 | SQLite、分页历史、安全详情、重启中断处理 |
| 历史详情体验 | ✅ 已完成 | 论文信息、三项分页 Archive、可折叠时间线与报告 |
| 报告操作 | ✅ 已完成 | 复制 Markdown，下载 Markdown、JSON、HTML、PDF、DOCX |
| 报告质量 | ✅ Phase D 核心完成 | 引用回溯、五维评分、真实 Verifier、最多一次自动修订 |
| 工程稳定性 | ✅ Phase A 完成 | 请求重试、严格上传、去重、取消/重试、文件清理、Prompt 统计 |
| 可靠任务队列 | ✅ 基础完成 | PostgreSQL 持久化、Redis Broker、独立 Celery Worker、检查点恢复与持久化 SSE |
| Ask-the-Paper | ✅ 单论文完成 | 页码/章节范围、预算化上下文、会话管理与归档、流式恢复、Evidence 与取消/重试 |
| 私有检索评估 | ✅ 基础设施完成 | 脱敏 Schema/fixture、候选生成、校验、validation 校准、test 冻结与质量门 |
| 多论文对比 | ✅ MVP 完成 | 2–5 个历史完成任务、持久矩阵与综合报告、Evidence 快照、SSE、五格式导出 |
| 跨论文对话 | 🗓️ 规划中 | 当前 Ask Paper 会话仍只绑定一个已完成任务 |

---

## ✨ 项目亮点

- 🤖 **多 Agent 协作**：Planner、Reader、Critic、Writer 分别负责规划、忠实提取、批判分析和报告撰写。
- 📄 **论文处理流水线**：基于 PyMuPDF 提取文本和页码，按可配置窗口切分为稳定的论文分块。
- 🔎 **可降级混合检索**：通过 BM25、Embedding、RRF 和持久向量索引提供可引用证据；真实向量服务不可用时仍可使用 BM25。
- 🔍 **即时文档搜索**：已完成论文可按章节、页码和 Top-K 搜索原文，查看来源分数与相邻上下文，再无缝转入 Ask Paper。
- 🧩 **结构化输出**：所有 Agent 输入输出均使用 Pydantic Schema，减少自由文本在工作流中的不确定性。
- 🛡️ **输出容错与重试**：支持从 Markdown 代码块、解释文字和带尾随逗号的响应中提取 JSON；Schema 校验失败时会将错误反馈给模型重试。
- 📝 **外置 Prompt**：Prompt 以 Markdown 文件保存在 `backend/prompts/`，可不改 Agent 代码独立迭代。
- ⚡ **同步与后台 API**：FastAPI 提供同步分析接口，后台任务由 Redis + Celery Worker 执行并将状态持久化到 PostgreSQL。
- 🖥️ **完整 Web MVP**：React + TypeScript 前端支持新建分析与持久化任务历史；历史页展示论文信息、工作流时间线和 Markdown 报告。
- 💬 **可恢复论文问答**：已完成任务可创建持久化会话；回答由 Worker 流式生成，断线续传且引用可回溯到论文分块。
- 🧪 **离线 Mock 模式**：无需 API Key 或网络即可运行完整工作流和默认测试，适合开发与演示。
- 🔌 **OpenAI-compatible 接口**：可接入 Qwen、DeepSeek、OpenAI 或其他兼容的 LLM/Embedding 服务。

---

## 🏗️ 系统架构

### 技术栈

| 层级 | 技术 | 职责 |
| :--- | :--- | :--- |
| 前端 | React 19、TypeScript、Vite、react-markdown | 上传论文、展示任务状态和阅读报告 |
| API | FastAPI、Uvicorn、SQLModel、PostgreSQL / SQLite | 文件上传、后台任务、持久化历史、状态与结构化报告查询 |
| 工作流 | Python、Pydantic | 串行编排论文解析、检索与多 Agent 分析 |
| 文档处理 | PyMuPDF | PDF 文本、页码和基础元数据提取 |
| RAG | NumPy / FAISS-compatible dependency、Embedding | 分块向量化、内存索引和相似度检索 |
| 模型接入 | OpenAI Python SDK | Mock 或 OpenAI-compatible LLM/Embedding API |
| CLI | Typer、Rich | 命令行分析和运行状态输出 |
| 测试与 CI | pytest、Ruff、GitHub Actions、Buildx | 单元、集成、静态检查、前端构建与容器镜像验证 |

### 核心架构

```mermaid
flowchart TD
    Browser["React Web UI"] --> API["FastAPI"]
    CLI["Typer CLI"] --> ORCH["PaperAnalysisOrchestrator"]
    API --> TASK["In-process Background Task"]
    TASK --> ORCH

    ORCH --> PDF["PDF Loader"]
    PDF --> CHUNK["Document Chunker"]
    CHUNK --> PLAN["Planner Agent"]
    PLAN --> INDEX["Embedder + Vector Store"]
    INDEX --> RETRIEVE["Paper Retriever"]
    RETRIEVE --> READER["Reader Agent"]
    READER --> CRITIC["Critic Agent"]
    CRITIC --> WRITER["Writer Agent"]
    WRITER --> EXPORT["Markdown + State JSON"]

    PROMPTS["Markdown Prompt Templates"] --> PLAN
    PROMPTS --> READER
    PROMPTS --> CRITIC
    PROMPTS --> WRITER

    MODEL["Mock / OpenAI-compatible Models"] --> PLAN
    MODEL --> READER
    MODEL --> CRITIC
    MODEL --> WRITER

    ORCH <--> STATE["AnalysisState"]
    API --> STORE["SQLModel + SQLite Task Store"]
```

### 端到端流程

```text
上传 PDF
  ↓
解析逐页文本与基础元数据
  ↓
生成带页码和稳定 ID 的文本分块
  ↓
Planner 创建分析任务与关注问题
  ↓
Embedding + 向量索引 + 证据检索
  ↓
Reader 提取问题、贡献、方法和实验
  ↓
Critic 分析局限、风险和可复现性
  ↓
Writer 生成中文或英文 Markdown 报告
  ↓
保存报告和完整 AnalysisState JSON
```

工作流当前按顺序串行执行，各阶段状态会记录在 `AnalysisState.step_history`。后台任务由 Celery Worker 执行，进度通过可恢复 SSE 推送；任务元数据在 Compose 中持久化到 PostgreSQL，轻量开发和测试仍可使用 SQLite。任务完成后前端优先加载结构化报告，旧产物则回退为完整 Markdown。

---

## 🚀 快速开始

### Ask Paper 离线质量基线

仓库内置固定的中英文合成检索集，不包含论文全文，也不会访问网络。以下命令分别运行 BM25、原始 hybrid、filtered-hybrid，以及 embedding 不可用时的显式降级场景，并写出可供 CI 保存的 JSON：

```bash
uv run python -m backend.evaluation.ask_paper \
  --mode all \
  --gate \
  --output backend/outputs/logs/ask-paper-eval.json
```

报告包含 candidate Recall@20、macro Precision@6、Recall@6、MRR、Evidence coverage、拒答率、错误拒答率、平均返回数和延迟，并按语言、可回答性、章节约束及干扰类型分组。合成集用于 CI 回归；正式 validation/test 流程作为生产化扩展保留，不阻塞当前项目的离线演示与功能闭环。完整限制和指标口径见 [Ask Paper 离线质量评估文档](backend/evaluation/README.md)。

### Docker Compose（推荐）

安装 Docker 与 Compose 后，在仓库根目录运行：

```bash
docker compose up --build
```

等待五个服务通过健康检查，然后访问 <http://localhost:3000>。前端由 Nginx 托管，`/api`、文件上传/下载和 SSE 事件流会代理到 FastAPI；API 仍可通过 <http://localhost:8000> 直接调试。

```bash
docker compose ps
curl http://localhost:3000/api/health
```

`docker compose ps` 应显示 `postgres`、`redis`、`api`、`worker` 和 `frontend` 五个服务，其中配置了探针的服务应为 `healthy`；健康接口应返回成功响应。

停止服务使用 `docker compose down`。如需同时删除数据库、Redis 和运行产物卷，可使用 `docker compose down -v`。

### 宿主机开发

### 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- npm 10+

以下命令均在仓库根目录执行。

#### 1. 安装后端依赖

```bash
uv sync
cp backend/.env.example backend/.env
```

默认 `.env.example` 使用完全离线的 Mock LLM 和 Mock Embedding，不需要 API Key。

#### 2. 启动后端

```bash
uv run uvicorn backend.api.main:app --reload
```

后端默认运行在 <http://127.0.0.1:8000>：

- 健康检查：<http://127.0.0.1:8000/api/health>
- Swagger UI：<http://127.0.0.1:8000/docs>
- ReDoc：<http://127.0.0.1:8000/redoc>

#### 3. 安装并启动前端

打开另一个终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 Vite 输出的本地地址，通常为 <http://127.0.0.1:5173>。

Vite 开发服务器默认将相对 `/api` 请求代理到 `http://127.0.0.1:8000`。如后端运行在其他地址，可创建 `frontend/.env.local`：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

修改 Vite 环境变量后需要重新启动前端开发服务器。

#### 4. 使用 Web 页面

1. 选择一个文本型 PDF 文件。
2. 填写希望 Agent 重点分析的问题。
3. 选择中文或英文报告。
4. 点击 **Start Analysis**。
5. 等待任务从 `pending`、`running` 进入 `completed`；运行中可点击 **Cancel**。
6. 在页面中阅读生成的 Markdown 报告。
7. 使用目录或关键词在章节间定位，点击 Evidence 标签查看页码、分块和证据原文；Reset/Overview 可恢复完整目录与报告顶部。
8. 切换到 **Search Document**，选择已完成论文，以 Auto 或 BM25 模式按章节、页码和 Top-K 搜索；打开 passage drawer 查看命中与直接相邻上下文。
9. 点击 **Continue in Ask Paper**，系统会选择同一论文并预填当前查询，但不会自动创建会话或发送问题；也可直接在 Ask Paper 创建持久会话。
10. 切换到 **Compare Papers**，搜索并选择 2–5 篇已完成论文，设置关注点和中英文输出；可恢复查看进度、横向浏览矩阵、打开跨论文 Evidence，并下载五种产物。

切换到 **Task History** 可查看历史任务。`failed` 或 `canceled` 任务可点击 **Retry** 创建关联的新任务；原任务记录不会被覆盖。详情还会展示 Prompt Set 和结构化输出调用摘要。

Ask Paper 的会话和消息跟随所属任务保存。关闭页面不会停止生成；重新进入会加载数据库中的消息，并从最后收到的事件继续流式显示。会话导出只收录助手答案实际引用的 Evidence，不包含未引用候选和 SSE token 事件。删除单个会话或所属任务时，对应消息、流事件和问答 Evidence 会在同一事务中一并清理。

示例 PDF 仅应在确认版权、隐私和再分发许可后加入公开仓库。

---

## ⚙️ 模型配置

应用从 `backend/.env` 读取配置。不要提交真实 API Key。

### 离线 Mock 模式

```env
LLM_PROVIDER=mock
LLM_VENDOR=mock
LLM_MODEL=mock-llm

EMBEDDING_PROVIDER=mock
EMBEDDING_VENDOR=mock
EMBEDDING_MODEL=mock-embedding
```

Mock 模式会走完整的解析、分块、检索、Agent 和导出流程，但模拟输出不代表真实论文分析质量。

### Qwen / DashScope

```env
LLM_PROVIDER=openai_compatible
LLM_VENDOR=qwen
LLM_MODEL=qwen-plus
LLM_API_KEY=your_dashscope_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_VENDOR=qwen
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_API_KEY=your_dashscope_api_key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

LLM 和 Embedding 可以独立配置。例如可以使用真实 LLM 配合 Mock Embedding，以减少外部调用。

### 主要运行参数

| 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DEFAULT_TOP_K` | `5` | 每个检索问题返回的证据数量 |
| `CHUNK_SIZE` | `1200` | 文本分块目标字符数 |
| `CHUNK_OVERLAP` | `150` | 相邻分块重叠字符数 |
| `OUTPUT_DIR` | `backend/outputs` | 运行产物根目录 |
| `REPORT_DIR` | `backend/outputs/reports` | Markdown 报告目录 |
| `LOG_DIR` | `backend/outputs/logs` | AnalysisState JSON 目录 |
| `ASK_BM25_K1` | `1.5` | Ask Paper BM25 词频饱和参数 |
| `ASK_BM25_B` | `0.75` | Ask Paper BM25 文档长度归一化参数 |
| `ASK_INDEX_PREBUILD_ENABLED` | `true` | 分析完成前尽力预构建全文向量索引 |
| `ASK_INDEX_DIR` | `backend/outputs/indexes` | 不含正文的版本化 Ask Paper 索引目录 |
| `DATABASE_URL` | `sqlite:///backend/data/tasks.db` | 持久化任务历史的 SQLite 数据库 |
| `REQUEST_CONNECT_TIMEOUT` | `10` | 外部请求连接超时，单位秒 |
| `REQUEST_READ_TIMEOUT` | `60` | 外部请求读取超时，单位秒 |
| `REQUEST_TOTAL_BUDGET` | `120` | 单次工作流外部请求重试预算，单位秒 |
| `REQUEST_MAX_RETRIES` | `2` | 连接错误、超时、HTTP 429/5xx 的最大重试次数 |
| `REQUEST_BACKOFF_BASE` | `1` | 指数退避基数，单位秒 |
| `REQUEST_BACKOFF_MAX` | `8` | 单次退避上限，单位秒 |
| `MAX_UPLOAD_BYTES` | `52428800` | PDF 上传上限，默认 50 MiB |
| `FILE_RETENTION_DAYS` | `30` | 终态任务文件保留天数 |
| `PROMPT_SET_VERSION` | `v1` | 写入任务状态的 Prompt 集版本 |
| `HIERARCHICAL_PAGE_THRESHOLD` | `20` | 长论文层次化路径的页数阈值 |
| `HIERARCHICAL_CHAR_THRESHOLD` | `60000` | 长论文层次化路径的字符阈值 |
| `VERIFIER_ENABLED` | `true` | 是否执行质量门禁和 Verifier |
| `QUALITY_PASS_SCORE` | `75` | 报告质量总分通过线 |
| `CITATION_VALIDITY_MIN_SCORE` | `80` | 引用有效性最低通过分 |
| `MAX_CUSTOM_SECTIONS` | `20` | 自定义报告章节数量上限 |
| `RUN_REAL_LLM_TESTS` | `0` | 设置为 `1` 时启用真实模型测试 |
| `COMPARISON_PAPER_MAX_TOKENS` | `6000` | 每篇论文进入比较 profile 的最大估算 token 预算 |
| `COMPARISON_FINAL_MAX_TOKENS` | `12000` | 最终综合内容预算 |
| `COMPARISON_EVIDENCE_PER_PAPER` | `12` | 每篇论文最多保留的比较 Evidence 数量 |

真实模型调用依赖网络、账号权限、模型可用性和服务配额，可能产生费用。网络超时、鉴权失败和限流与结构化输出校验属于不同类型的错误。

---

## 🔌 API 概览

### 创建后台分析任务

```http
POST /api/tasks/analyze
Content-Type: multipart/form-data
```

表单字段：

- `file`：PDF 文件。
- `query`：分析要求。
- `language`：`zh` 或 `en`。
- `analysis_depth`：`quick`、`standard` 或 `deep`。
- `target_audience`：`general`、`researcher` 或 `reviewer`。
- `report_template`：`standard`、`review` 或 `reproducibility`。
- `custom_sections`：可选 JSON 字符串数组，最多 20 项。

上传必须使用 `.pdf` 扩展名，MIME 为 `application/pdf` 或 `application/octet-stream`，且内容以 `%PDF-` 开头。默认最大 50 MiB。系统以“文件 SHA-256 + 标准化 query + language”识别重复任务；相同任务仍处于 `pending/running` 时会返回原 `task_id`，并设置 `deduplicated: true`。

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/analyze \
  -F 'file=@backend/data/raw/example.pdf;type=application/pdf' \
  -F 'query=分析论文的主要贡献、实验设计和局限' \
  -F 'language=zh' \
  -F 'analysis_depth=deep' \
  -F 'target_audience=reviewer' \
  -F 'report_template=review' \
  -F 'custom_sections=["消融实验","伦理风险"]'
```

### 查询任务状态

```http
GET /api/tasks/{task_id}
```

状态包括 `pending`、`running`、`completed`、`failed` 和 `canceled`。

### 取消与重试

```http
POST /api/tasks/{task_id}/cancel
POST /api/tasks/{task_id}/retry
```

取消采用协作式阶段边界检查：不会强杀正在执行的单次模型 HTTP 请求，但不会继续启动后续阶段。重复取消是幂等操作。只有 `failed` 和 `canceled` 任务可以重试；重试会复制仍存在的源 PDF、创建新 `task_id` 并通过 `retry_of` 保留关联。源文件已经清理或缺失时返回 `409`，需要重新上传。

### 获取报告

```http
GET /api/tasks/{task_id}/report
```

任务完成后返回 `report_markdown`。任务未完成时返回 `409`。

结构化阅读和任务级证据接口供前端按需加载：

```http
GET /api/tasks/{task_id}/report/structured
GET /api/tasks/{task_id}/evidence/{evidence_id}
```

结构化产物不可用时返回 `404`，前端继续展示已有 Markdown；未完成任务返回 `409`。证据正文最多返回 2000 字，且只能在所属任务内访问。

### 搜索已完成论文

```http
POST /api/tasks/{task_id}/search
Content-Type: application/json
```

```json
{
  "query": "消融实验如何验证各模块贡献？",
  "mode": "auto",
  "section": "Experiments",
  "page_start": 5,
  "page_end": 9,
  "top_k": 6
}
```

`query` 去除首尾空白后为 1–8000 字符；`mode` 为 `auto | bm25`；页码必须成对提供且使用 1-based 闭区间；`top_k` 为 1–20。`mode_used` 只会是 `hybrid`、`bm25` 或 `degraded_to_bm25`。每个命中返回最多 1200 字符的片段、chunk/章节/页码、BM25/vector/hybrid 分数、命中来源，以及最多两个不跨章节和请求页码边界的直接相邻 chunk。搜索同步只读，不保存历史、不调用 LLM 或 reranker，也不生成 Evidence ID。

### 查询历史与详情

```http
GET /api/tasks?limit=20&offset=0
GET /api/tasks/{task_id}/detail
```

历史列表按创建时间倒序分页。详情包含安全筛选后的工作流步骤摘要，并在文件仍存在时返回 Markdown 报告。任务元数据存储在 SQLite 中，因此服务重启后仍可查询；重启时未完成的任务会标记为失败。已有磁盘报告不会自动导入数据库。

详情的安全 metadata 包含 Prompt Set 版本、模板短哈希和结构化输出统计。统计包括总调用数、首轮成功数、发生重试数、最终失败数，以及按 Schema 汇总的调用、尝试和结果；不包含模型原始响应或论文正文。

### 多论文对比 API

比较只接受 2–5 个唯一、状态为 `completed` 且 state/report 文件可用的任务，并按请求顺序快照论文标题、作者、年份和 paper ID：

```http
POST   /api/comparisons
GET    /api/comparisons?limit=20&offset=0&search=&status=
GET    /api/comparisons/{comparison_id}
PATCH  /api/comparisons/{comparison_id}
DELETE /api/comparisons/{comparison_id}
POST   /api/comparisons/{comparison_id}/cancel
POST   /api/comparisons/{comparison_id}/retry
GET    /api/comparisons/{comparison_id}/events?after={sequence}
GET    /api/comparisons/{comparison_id}/report
GET    /api/comparisons/{comparison_id}/report/structured
GET    /api/comparisons/{comparison_id}/evidence/{evidence_id}
GET    /api/comparisons/{comparison_id}/artifacts/{markdown|json|html|pdf|docx}
```

创建请求示例：

```json
{
  "task_ids": ["task_a", "task_b"],
  "title": "方法与实验对比",
  "focus": "比较数据集、方法、主要结果与局限",
  "language": "zh"
}
```

活动比较会阻止源任务删除。比较进入 `completed`、`failed` 或 `canceled` 后，源任务可正常删除；已完成比较依靠结构化产物与 Evidence 快照继续独立读取。Retry 只接受失败或取消的比较，并创建带 `retry_of` 的新 comparison ID。首版不支持新文件上传、arXiv/DOI/URL 输入、分享权限或跨论文多轮问答。

### 请求 ID 与错误响应

服务会透传格式安全的 `X-Request-ID`，否则自动生成，并始终写入响应头。错误 JSON 保留兼容字段 `detail`，同时返回 `code` 和 `request_id`：

```json
{
  "detail": "Task not found.",
  "code": "not_found",
  "request_id": "7adba44d02ea4fbfa22d0e73640e653f"
}
```

常见错误分类包括 `validation`、`not_found`、`conflict`、`network_timeout`、`rate_limit`、`upstream`、`json_parse`、`schema_validation`、`workflow` 和 `canceled`。客户端只应展示 `detail`，排查问题时使用 `request_id` 关联服务端日志。

### 文件生命周期

应用启动时清理超过 `FILE_RETENTION_DAYS` 的终态任务上传 PDF、报告和 state JSON，并保留 SQLite 历史及清理标记。清理仅处理系统命名的 `task_*` / `api_*` 文件，不会删除示例文件或其他用户文件。同步接口的临时上传在请求结束后立即清理。

### 同步上传分析

```http
POST /api/analyze/upload
```

同步接口会阻塞直到整个分析完成，主要用于简单调试。Web 前端使用后台任务接口。

完整请求与响应 Schema 请查看 Swagger UI。

---

## 💻 CLI 使用

无需启动 FastAPI，也可以直接通过 CLI 运行分析：

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
  --state-json backend/outputs/logs/state_en.json \
  --language en
```

查看所有 CLI 参数：

```bash
uv run python -m backend.app.cli --help
```

---

## 📁 项目结构

```text
Multi-Agent_Paper_Reader_System_Design/
├── backend/
│   ├── agents/                 # Planner、Reader、Critic、Writer
│   ├── api/                    # FastAPI 应用、路由、任务状态和响应 Schema
│   ├── app/                    # CLI 与预留 Streamlit 入口
│   ├── core/                   # 配置、工作流编排和 AnalysisState
│   ├── exporters/              # Markdown、报告 JSON、状态 JSON 导出
│   ├── llm/                    # LLM Client、JSON 解析和 Prompt Loader
│   ├── prompts/                # 四类 Agent 的 Markdown Prompt 模板
│   ├── schemas/                # 论文、Agent I/O 和报告 Schema
│   ├── tools/                  # PDF、分块、Embedding、检索和向量存储
│   ├── tests/                  # 单元、集成和真实模型 smoke tests
│   ├── data/raw/               # 本地输入和可选示例 PDF
│   └── outputs/                # 上传、报告和运行状态文件
├── frontend/
│   ├── src/api/                # 前端 API Client
│   ├── src/components/         # 上传、任务状态和报告组件
│   ├── src/types/              # API TypeScript 类型
│   └── src/App.tsx             # 单页 Web 工作区
├── pyproject.toml              # Python 项目与依赖
├── uv.lock                     # Python 锁文件
└── README.md
```

---

## 🧪 测试与构建

### 后端测试

```bash
uv run pytest backend/tests -q -rs
```

普通测试使用 Mock 客户端，不访问外部模型。真实模型测试默认跳过。

Document Search 测试覆盖 BM25/Hybrid 排序、稳定 tie-break、Top-K、片段截取、相邻边界、索引复用、embedding 降级、API 状态/state/范围校验和 OpenAPI Schema。Ask Paper API 测试使用隔离 SQLite、显式生命周期准备、临时 state 文件和确定性 Worker fake，覆盖任务/会话隔离、页码与章节交集、预算化上下文、字面搜索、事务级删除、Markdown/JSON 引用归档、Evidence 归属、取消/重试及 SSE 恢复协议。Comparison 测试覆盖 2–5 篇约束、顺序快照、状态与事件、活动源删除保护、终态独立性、Evidence 命名空间、预算、引用白名单、五种导出和级联删除。

前端使用 Vitest、Testing Library 和 jsdom 覆盖交互报告、Search Document 的校验/结果/drawer/Ask handoff、Task History 快捷入口、Ask Paper SSE 消费器和 Compare Papers 选择/矩阵；Playwright route fixtures 覆盖文档搜索、相邻上下文、完整问答、比较创建、跨论文 Evidence、五种导出入口、移动端布局、刷新恢复和生命周期操作。浏览器回归同时运行桌面 Chromium 与 Pixel 7 项目：

```bash
cd frontend
npm run test:e2e
```

显式运行真实模型 smoke test：

```bash
RUN_REAL_LLM_TESTS=1 uv run pytest backend/tests/test_planner_agent_real.py -v -s
RUN_REAL_LLM_TESTS=1 uv run pytest backend/tests/test_orchestrator_real.py -v -s
```

执行静态检查：

```bash
uv run ruff check backend
```

### 前端构建

```bash
cd frontend
npm install
npm run build
```

当前 Vite 版本要求 Node.js 20.19+ 或 22.12+，推荐使用 Node.js 22 LTS。

---

## 📝 报告内容

最终 Markdown 报告通常包含：

- 论文基本信息与 TL;DR
- 研究问题与背景
- 主要贡献
- 方法与技术路线
- 实验设置和主要结果
- 优点、局限与缺失实验
- 可靠性与可复现性分析
- 创新性和综合评价
- 与论文分块对应的 evidence IDs

仓库中的[示例报告](backend/outputs/reports/example_report.md)用于展示输出结构。实际质量取决于 PDF 文本质量、Prompt、检索证据、用户 query 和所选模型。

---

## 🗺️ 后续规划

Roadmap 以项目的完整性为优先：先完成用户看得见、可以端到端演示并有自动化测试的功能，再把正式评测、大规模基础设施和生产运维作为加分项。`[x]` 表示已完成，`[ ]` 表示后续工作。

文档检索增强 MVP 已完成：已完成任务拥有独立搜索入口，复用现有持久索引，并把章节/页码、相邻上下文、检索来源和降级状态完整呈现给用户。

### Phase A：工程稳定性优化

- [x] 为 LLM 和 Embedding 请求增加可配置的连接、读取和总超时，以及针对超时、连接错误、429 和 5xx 的指数退避重试。
- [x] 统一错误类型、请求 ID、结构化日志和前后端脱敏错误信息，区分网络、解析、Schema 和工作流错误。
- [x] 增加任务取消、手动重试、失败清理、临时文件生命周期和重复任务保护。
- [x] 增加上传大小、MIME 类型和 PDF 内容校验。
- [x] Agent 输入输出使用 Schema 校验，Prompt 已外置，并支持结构化输出失败反馈重试。
- [x] 完善 Prompt 版本管理、回归样例和结构化输出成功率统计。

### Phase B：可靠任务执行与历史管理

- [x] 使用 SQLite 保存任务、论文元数据、阶段状态、错误、报告路径和创建/完成时间。
- [x] 提供分页历史列表、安全详情接口和服务重启后的中断任务处理。
- [x] 使用 PostgreSQL、Redis 和独立 Celery Worker 替换进程内 `BackgroundTasks`。
- [x] 增加任务删除、重新运行、失败恢复、工作流 checkpoint 和审计事件。
- [x] 为上传 PDF、state 和报告实现可配置保留期及安全清理；向量索引配额随持久化向量库阶段继续完善。

### Phase C：前端体验增强

- [x] 提供任务历史、运行详情、论文信息、可折叠阶段时间线和响应式布局。
- [x] 支持报告复制和 Markdown 下载。
- [x] 使用可持久化 SSE 推送 Agent、检索和报告生成进度，并支持断线重连。
- [x] 增加失败/取消任务重试、运行任务取消及操作失败反馈。
- [x] 增加网络断线恢复、拖拽上传、参数预设和深色模式。
- [x] 支持报告目录导航、关键词搜索、证据引用跳转及 Markdown/JSON/HTML/PDF/DOCX 下载。
- [ ] 完善键盘操作、加载骨架和超长报告渲染性能。

### Phase D：报告质量优化

- [ ] 建立覆盖准确性、完整性、忠实度、引用有效性和批判深度的完整离线评估集与指标。
- [x] 增加引用校验和证据覆盖检查，确保关键结论能够回溯到论文页码和分块。
- [x] 识别长论文并保留章节结构，为分章节层次化分析提供上下文边界。
- [x] 引入确定性 Verifier，对无效引用、位置错误和无证据 claim 进行检查，最多自动修订一次。
- [x] 支持报告模板、分析深度、目标读者、自定义章节和 Markdown、HTML、PDF、DOCX、JSON 导出。

创建任务时可提交 `analysis_depth`、`target_audience`、`report_template` 和 JSON 字符串数组 `custom_sections`。完成后使用 `GET /api/tasks/{task_id}/artifacts/{format}` 下载产物，其中 `format` 为 `markdown | json | html | pdf | docx`。HTML/PDF/DOCX 首次请求生成并缓存。

真实 LLM 模式会默认让 Metadata Extractor 裁决标题、作者和 venue 候选，并补充其他缺失或低置信字段。该调用只包含首页文本块及其字号、坐标、旋转方向、摘要候选和章节标题候选，不发送整篇论文。模型只能选择或组合候选，结果还必须通过离线一致性检查；DOI、arXiv ID 和年份仍以确定性解析为主。Writer 完成后，Verifier 使用结构化报告和当前论文的证据片段检查事实支持、遗漏、矛盾和引用；不通过时 Writer 最多修订一次并再次验证。Mock 模式不会触发这两个模型调用。

### Phase E：RAG 检索增强

- [x] 使用原子写入的 `ask-retrieval-index-v1` 持久化单论文归一化向量，支持重启加载、摘要失效、范围切片与生命周期清理（规模化外部向量数据库仍留待后续）。
- [x] 为 Ask Paper 增加 BM25 与向量双路召回、RRF 融合、原始分数诊断、可选 Cross-encoder Rerank 及服务失败降级。
- [x] 为 Ask Paper 引入预算化最近上下文的 query rewrite，以及章节与页码范围过滤；改写失败时确定性降级（HyDE 与通用 metadata filtering 留待后续）。
- [x] 增加 candidate Recall@20、Precision@6、Recall@6、MRR、证据覆盖率、分组指标和检索延迟评估框架。
- [x] 提供真实私有评估集 Schema、候选生成/校验/校准 CLI、论文级拆分、test 内容冻结与单次冻结质量门。
- [x] 新增 `POST /api/tasks/{task_id}/search` 任务内搜索 API，支持章节、页码、模式和 Top-K，并返回安全片段、页码、章节、分数与命中来源。
- [x] 增加顶层 Search Document 工作区和 Task History 快捷入口，并可把同一任务与查询预填到 Ask Paper。
- [x] 增加直接相邻 chunk 上下文、结果去重和稳定排序，避免命中片段因分块边界缺少上下文。
- [x] 增加离线 BM25 演示、API/前端回归和检索解释视图，确保无真实 API Key 也能完成演示。
- [ ] 可选加分：将当前 pilot-only 样本扩展为正式人工 gold 并继续 Pilot；它不阻塞文档搜索 MVP，test 继续不访问，reranker 保持 `disabled`。
- [x] 增加按任务、state 内容、chunk、Embedding 配置与 Schema 失效的 Worker LRU/磁盘索引，并单列构建、加载、命中与冷构建失败统计。

### Phase F：Ask-the-Paper 问答模式

- [x] 在单篇论文分析完成后提供多轮问答，复用已解析 state、文档分块和有界 Worker 内检索索引缓存。
- [x] 每个回答返回正文答案、消息命名空间 evidence IDs、页码和相关原文片段；证据不足时明确拒绝推断。
- [x] 支持最近对话上下文、追问改写、指定章节和自动/中文/英文回答。
- [x] 持久化会话、消息、Evidence 和流事件，支持刷新/断线续传、取消、失败重试和会话标题编辑。
- [x] 回答 Evidence 复用报告证据抽屉，并可返回所属任务报告。
- [x] 支持当前论文内会话搜索、单会话事务级永久删除，以及仅含实际引用 Evidence 的 Markdown/JSON 归档。
- [x] 支持可与章节组合的页码范围筛选，并按最近消息数、历史 token 和 Evidence token 预算管理上下文窗口。
- [x] 完善引用白名单、Evidence 快照分离、上下文边界、无答案和 prompt injection 后端专项测试。
- [ ] 扩展答案忠实度评测规模；浏览器 SSE 恢复和移动端交互专项回归已建立。

### Phase G：多论文对比模式

- [ ] 支持批量上传、arXiv ID、DOI、论文 URL 和远程 PDF，并为每篇论文建立独立索引与分析状态。
- [x] 从历史中选择 2–5 个唯一的已完成任务，统一抽取研究问题、数据集、方法、基线、指标、结果和局限，生成版本化 profile。
- [x] 提供持久对比矩阵、共同点与差异、结果冲突、方法演进和适用场景分析。
- [x] 支持跨论文 Evidence 快照，所有比较引用携带来源任务、论文、chunk、页码和章节，并经过比较命名空间白名单清理。
- [x] 支持持久历史、SSE 恢复、取消、失败重试、删除、活动源任务保护和 Markdown/JSON/HTML/PDF/DOCX 导出。
- [ ] 增加跨论文多轮问答、相关工作/研究空白生成、协作编辑、分享权限和成本看板。

### Phase H：文档解析增强

- [ ] 增加 OCR 和扫描件支持，处理多栏排版、页眉页脚、断词、乱码和阅读顺序问题。
- [ ] 可靠抽取标题、作者、摘要、章节、参考文献、脚注、页码和 DOI/arXiv 等标识符。
- [ ] 增加表格、公式、图像与图注理解，并保留其在原始 PDF 中的位置和引用关系。
- [ ] 使用版面感知分块和章节层次结构替代纯字符窗口，改善证据边界和上下文完整性。
- [ ] 建立不同出版社、语言、排版和扫描质量的解析测试集，量化文本、结构与元数据准确率。

### Phase I：部署与工程化

- [x] 提供前后端、Worker、PostgreSQL 和 Redis 的 Dockerfile 与 Docker Compose 一键部署方案。
- [x] 建立 CI 流程，执行后端测试、Ruff、前端 lint/build、Compose 校验和镜像构建。
- [ ] 增加开发、测试、生产环境配置，使用 Secret 管理 API Key，并提供数据库迁移和备份方案。
- [ ] 增加认证、权限隔离、速率限制、用户配额、审计日志、CORS 和安全响应头配置。
- [ ] 接入健康探针、结构化日志、Metrics、Tracing、错误告警及 token、延迟和成本看板。
- [ ] 完善反向代理、HTTPS、水平扩容、Worker 并发、优雅停机、数据保留和部署运维文档。

---

## ⚠️ 已知限制

- 当前仅支持本地上传的文本型 PDF；扫描件和复杂排版可能无法正确解析。
- 元数据依次使用 PDF metadata、首页版面和文本规则抽取，并记录来源与置信度；复杂排版仍可能显示“未识别”。
- Orchestrator 串行执行，尚未实现并行 Agent 或可恢复任务图。
- 分析工作流内部的 `NumpyVectorStore` 仍是进程内组件；Ask Paper 和 Search Document 使用独立的 `ask-retrieval-index-v1` 持久索引。当前索引按单任务 JSON 文件保存，适合项目演示，不面向大规模语料库。
- 后台任务通过独立 Celery Worker 执行，任务元数据保存在 PostgreSQL；当前仍未实现 Worker 水平扩容和完整的任务图恢复。
- 服务重启后历史任务仍可查询，兼容检查点的失败任务可恢复；已写入磁盘但未登记到数据库的旧报告和 state 文件不会自动导入。
- 同步接口仍会阻塞到分析结束，并消耗 API 进程计算资源。
- 默认上传上限为 50 MiB，终态任务文件保留 30 天；任务支持阶段边界取消、失败/取消后重试和活动任务去重。
- 当前仍没有用户认证、权限控制或限流；执行中的单次模型 HTTP 请求不会被强制中断。
- Mock Embedding 不具备真实语义检索能力，Mock Agent 输出也不代表真实论文内容。
- Prompt 模板变量必须与对应 Agent 的渲染参数匹配，否则模板加载会失败。
- 结构化重试只解决 JSON 格式或 Schema 校验问题，不能代替网络超时和服务限流重试。
- 真实模型可能遇到网络超时、鉴权失败、地域不匹配、模型不可用、配额不足和调用费用。
- 示例 PDF 和生成报告公开前必须确认版权、隐私及再分发许可。

---

## 📄 License

仓库当前未声明开源许可证。在添加明确的 License 文件前，请勿默认将代码、示例论文或生成报告用于公开再分发。
# Reliable task runtime (Phase B/C)

The production runtime uses PostgreSQL 16, Redis 7, FastAPI, and a separate Celery worker. Copy `.env.example` to `.env`, then start the backend services with:

```bash
docker compose up --build
```

The Compose stack includes the Nginx frontend at `http://localhost:3000`; Vite can still run on the host with `cd frontend && npm install && npm run dev`. Tasks stream durable progress from `GET /api/tasks/{id}/events`; failed tasks with compatible checkpoints can be resumed, while retry and rerun create new task IDs. SQLite remains supported for tests and lightweight development.

To explicitly import legacy SQLite rows (files are not copied):

```bash
uv run python -m backend.scripts.import_sqlite_tasks --database-url postgresql+psycopg://paper_reader:paper_reader@localhost:5432/paper_reader
```

# 文档检索增强 MVP

## 1. 定位

本阶段目标是补齐“上传论文 → 建立索引 → 主动检索 → 查看来源 → 转入问答”的端到端产品闭环，而不是建设生产级搜索平台。该 MVP 已于 2026-07-14 完成。

Search Document 已复用 Ask Paper 的 BM25、向量召回、RRF、章节/页码过滤和持久索引，把底层能力变成用户可直接操作、可观察、可测试的文档搜索功能。

## 2. MVP 用户流程

1. 用户打开一个已完成的论文任务。
2. 在 **Search Document** 面板输入关键词或自然语言问题。
3. 可选指定章节、页码范围、检索模式和返回数量。
4. 系统返回带页码、章节、摘要片段、检索来源和分数的结果。
5. 用户可打开本地 passage drawer、复制片段和相邻上下文，或把当前查询带入 Ask Paper 继续提问；搜索结果不会伪装成 Evidence。
6. 向量服务不可用时自动回退 BM25，并在界面显示降级状态。

## 3. 实现范围

### 后端

- 已新增任务内搜索接口 `POST /api/tasks/{task_id}/search`。
- 请求字段：`query`、可选 `section`、可选 `page_start/page_end`、`mode`、`top_k`。
- 响应字段：`chunk_id`、`section`、`page_start/page_end`、受限长度的 `text`、BM25/vector/hybrid 分数、命中来源，以及脱敏检索诊断。
- 复用 `AskPaperRetrievalService` 和 `ask-retrieval-index-v1`，不另建第二套索引。
- 每个命中补充文档顺序中直接相邻的前后 chunk；上下文不得跨章节或请求页码边界，也不参与排名。
- 保持稳定排序；搜索路径始终禁用 reranker，向量索引构建或查询失败时返回 BM25 结果和安全降级类别。
- 搜索接口只接受已完成且 state 可用的任务，不修改会话或报告数据。

### 前端

- 新增顶层 **Search Document** 工作区，并在 Task History 的完成任务中提供快捷入口；搜索不依赖 LLM 回答生成。
- 支持查询、章节、页码、Top-K 和 Auto/BM25 模式。
- 结果卡展示页码、章节、片段、来源标签和可折叠分数说明。
- 提供本地 passage drawer、复制和“在 Ask Paper 中继续提问”操作。
- 明确展示 `hybrid`、`bm25` 和 `degraded-to-bm25` 状态。

### 可观察性与演示

- 只展示 BM25/向量/RRF 的数值、缓存状态、耗时和降级类别，不展示向量、密钥或完整上游错误。
- 默认 Mock 配置可对已完成论文执行离线 BM25 搜索，确保没有真实 API Key 也能展示检索流程。
- README 增加一段从上传论文到搜索、问答和多论文对比的演示路径。

## 4. 完成标准

- 文本型 PDF 可以完成上传、分析、索引、搜索、passage 查看和 Ask Paper 预填。
- 章节与页码组合过滤正确，相邻 chunk 扩展不会越界或产生重复结果。
- 进程重启后复用持久索引；state、chunk、模型或 Schema 改变时自动重建。
- Embedding 不可用时搜索仍可使用 BM25，并向用户显示降级状态。
- 后端覆盖检索排序、过滤、相邻扩展、索引命中、损坏恢复、API 校验和降级测试。
- 前端覆盖正常结果、空结果、降级、过滤和移动端布局。
- pytest、Ruff、前端 test/lint/build、Compose 配置和镜像构建通过。

## 5. 实现结果

1. 提取稳定的搜索请求/响应 Schema，并为现有检索结果增加安全展示映射。
2. 实现任务内搜索 API 和相邻 chunk 扩展。
3. 增加 API、索引复用和降级测试。
4. 实现前端搜索面板、passage drawer、复制和 Ask Paper handoff。
5. 增加离线测试、组件/浏览器回归和 README 演示步骤。

真实 embedding smoke test 仍是可选后续项；真实服务不可用不阻塞离线 MVP。

## 6. 本阶段不做

- OCR、扫描 PDF、复杂表格/公式/图片理解。
- Qdrant、Milvus、Elasticsearch 等外部检索基础设施。
- 多租户、权限、配额、计费和大规模并发优化。
- 扩充约 200 条正式人工 gold 或冻结 test。
- 自动启用生产 reranker。
- 复杂查询规划、HyDE、Graph RAG 或跨库联网检索。

这些能力可作为后续演进方向，但不应影响当前项目形成完整、稳定、可演示的交付。

## 7. 验证结果

- 后端：`196 passed, 6 skipped`；Ruff 全量通过。
- 前端：Vitest `17 passed`；Oxlint 与生产构建通过。
- 浏览器：Playwright 桌面/移动项目 `10 passed, 2 skipped`，Search Document 两端均通过。
- 部署：`docker compose config --quiet` 通过；API、Worker、Frontend 三个镜像构建成功。
- 仓库：`git diff --check` 通过。

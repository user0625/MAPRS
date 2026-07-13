# Multi-Agent Paper Reader Frontend

React + TypeScript + Vite 前端提供三个工作区：

- **New Analysis**：上传 PDF，配置 query、语言、分析深度、目标读者、报告预设和可选自定义章节，轮询任务状态并展示 Markdown 报告。活动任务支持取消。
- **Task History**：分页查看持久化历史和安全详情；展示论文信息、Prompt/结构化输出摘要、可折叠工作流时间线和报告。`failed`、`canceled` 任务支持重试。
- **Ask Paper**：选择已完成论文和持久化会话，按全篇、章节和可选页码范围提问；支持会话搜索、删除、Markdown/JSON 引用归档、自动/中文/英文回答、断线续流、Evidence 抽屉、取消、失败重试和重命名。

两个页面共享同一交互报告组件。报告支持一键复制和 Markdown、JSON、HTML、PDF、DOCX 下载；结构化产物可用时提供完整章节目录、大小写不敏感搜索、上一项/下一项定位，以及按需加载并缓存的 Evidence 详情。搜索不会隐藏目录项，匹配项仅作标记；`Reset` 或 `Overview` 会清空定位状态并返回报告顶部。旧任务或结构化接口不可用时自动保留完整 Markdown 阅读体验。

桌面端报告正文使用独立滚动区域，证据显示为右侧抽屉；移动端使用自然页面高度、折叠目录和底部证据面板。证据面板支持 Escape 关闭、错误重试和焦点返回。Task History 桌面端继续保持 560px 双栏约束，移动端自动切换为单栏。

Ask Paper 左栏中的论文和会话标题在关闭状态下受栏宽约束，展开选择或悬停时可查看完整标题。搜索由服务端限定在当前论文内；无匹配时显示明确空状态。提问区可填写 1-based 起止页，并与章节筛选取交集；范围会显示在消息上并随重试、刷新和归档保留。标题区可下载 Markdown/JSON 归档或确认后永久删除会话，生成期间这些操作禁用。回答流使用持久化事件序号去重；刷新、切换页面或短暂断网后会重新加载消息并从最后事件继续。回答中的 Evidence 标签复用报告证据抽屉，并可返回对应任务报告。

## 本地开发

```bash
cd frontend
npm install
npm run dev
```

开发服务器默认将 `/api` 代理到 `http://127.0.0.1:8000`。如需覆盖，创建 `frontend/.env.local`：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

修改环境变量后需要重新启动开发服务器。

## Docker 运行

在仓库根目录执行 `docker compose up -d --build`，等待五个服务启动后访问 <http://localhost:3000>。生产镜像使用 Node 20 构建静态资源，并由 Nginx 托管；Nginx 同时代理 API、上传下载与无缓冲 SSE 请求。

```bash
docker compose ps
curl http://localhost:3000/api/health
docker compose logs -f frontend
```

FastAPI 仍暴露在 <http://localhost:8000>，便于绕过代理直接调试。桌面端 Task History 的 Archive 与 Analysis Detail 固定为 560px，详情在模块内部滚动；移动端使用自然高度和页面滚动。

## 任务操作

- `pending/running`：New Analysis 状态卡和历史详情提供 **Cancel**。
- `failed/canceled`：历史详情提供 **Retry**。重试成功后自动选中新任务。
- 操作期间按钮进入禁用状态；失败信息显示在当前工作区。
- `canceled` 为终态，不再继续轮询；完成任务仍可复制或下载报告。

取消是后端阶段边界协作式取消，因此点击后可能短暂显示 “Canceling…”。如果模型请求正在执行，需要等待该请求返回或达到超时后才会停止工作流。

## 构建验证

```bash
npm test
npm run build
npm run lint
npm run test:e2e
```

Vitest、Testing Library 和 jsdom 覆盖交互报告与 Ask Paper 流式 hook。流测试验证 SSE CRLF 解码、重复事件去重、按最后 cursor 和 `Last-Event-ID` 重连，以及切换会话时中止旧消费器。

Playwright 使用 route interception 提供确定性的任务、会话、Evidence 和 SSE 数据，不依赖真实后端、LLM、Redis 或 Celery。浏览器用例覆盖页码范围、搜索与无结果、两种下载、删除确认与重选、生成期禁用，以及桌面/移动端布局。`npm run test:e2e` 会自动启动独立 Vite 服务，并运行桌面 Chromium 与 Pixel 7 移动端项目；失败时保留截图、视频和 trace。fixture 仅拦截 URL 根路径下的 `/api/`，避免误拦截 Vite 的 `/src/api/` 源码模块。首次运行前如缺少浏览器或系统依赖，可执行：

```bash
npx playwright install chromium
sudo npx playwright install-deps chromium
```

构建会先执行 TypeScript 项目检查，再由 Vite 生成 `dist/` 生产资源。完整交付检查还包括仓库根目录的 `docker compose config --quiet` 和 `git diff --check`。
# Browser behavior

The UI consumes task SSE with replay and exponential reconnect, falling back to low-frequency status synchronization. Upload supports drag/drop and keyboard activation, built-in analysis presets, task filtering and lifecycle actions, and `light | dark | system` themes. Theme choice is stored in `localStorage`; task data remains server-side. EventSource requires a modern evergreen browser.

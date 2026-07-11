# Multi-Agent Paper Reader Frontend

React + TypeScript + Vite 前端提供两个工作区：

- **New Analysis**：上传 PDF，配置 query、语言、分析深度、目标读者、报告预设和可选自定义章节，轮询任务状态并展示 Markdown 报告。活动任务支持取消。
- **Task History**：分页查看持久化历史和安全详情；展示论文信息、Prompt/结构化输出摘要、可折叠工作流时间线和报告。`failed`、`canceled` 任务支持重试。

报告支持一键复制与下载 `.md`；历史详情还展示元数据来源/置信度、章节目录、五维质量分和修订次数，并提供 JSON、HTML、PDF、DOCX 下载。桌面端采用 Archive + Analysis Detail 双栏布局，移动端自动切换为单栏。

## 本地开发

```bash
cd frontend
npm install
npm run dev
```

默认 API 地址为 `http://127.0.0.1:8000`。如需覆盖，创建 `frontend/.env.local`：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

修改环境变量后需要重新启动开发服务器。

## 任务操作

- `pending/running`：New Analysis 状态卡和历史详情提供 **Cancel**。
- `failed/canceled`：历史详情提供 **Retry**。重试成功后自动选中新任务。
- 操作期间按钮进入禁用状态；失败信息显示在当前工作区。
- `canceled` 为终态，不再继续轮询；完成任务仍可复制或下载报告。

取消是后端阶段边界协作式取消，因此点击后可能短暂显示 “Canceling…”。如果模型请求正在执行，需要等待该请求返回或达到超时后才会停止工作流。

## 构建验证

```bash
npm run build
```

构建会先执行 TypeScript 项目检查，再由 Vite 生成 `dist/` 生产资源。

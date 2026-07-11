"""
  schemas: 定义Api的返回结构，不能把完整的AnalysisState全量返回给前端，因为里面可能包含大量chunks和evidence

  routes/health: 健康路由检查，提供一个 /api/health端点用于返回服务健康状态
  routes/analysis: 接收上传的PDF，保存到临时/上传目录，调用Orchestrator，导出markdown报告，返回轻量respose
                                    👇
  当前 /api/analyze/upload 是同步接口，如果论文较长，前端会一直等待。FastAPI 的 BackgroundTasks 可以在响应
  返回后继续执行后台任务，适合当前这种 MVP 级“提交任务后异步分析”的场景
  :
      前端上传 PDF
        ↓
      后端立即返回 task_id
        ↓
      后台运行 Orchestrator
        ↓
      前端轮询任务状态
        ↓
      完成后获取 report
"""
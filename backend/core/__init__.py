"""
  schema 目前还是散的。后面的PDF Loader、Chunker、Retriever、Agent、Orchestrator需要一个统一的地方来管理

  config.py：
    - 从.env中读取配置
    - 管理LLM/Embedding 模型名称
    - 管理项目路径
    - 提供全局配置对象
    - 避免在业务代码中读取环境变量

  state.py：
    - 一次论文分析任务从开始到结束的完整状态
    - 它要保存：
      - 用户输入
      - 论文解析结果
      - chunks，
      - 检索证据，
      - planner输出，
      - reader输出，
      - critic输出，
      - writer输出
      - 当前执行阶段
      - 错误信息
      - 时间戳
  
  orchestrator.py:
    - 把 PDFLoader, chunker, retriever, planner, reader, critic, writer串成一条完整流程
    - 输入一篇PDF -> 自动生成Final Report对象

"""
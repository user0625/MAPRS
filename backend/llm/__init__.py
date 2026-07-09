"""
  Phase 4: LLm 层设计 （统一封装 qwen/deepseek/openai/mock模型调用，让后面agent不直接依赖具体厂商SDK

  client.py ：
          ✅ 封装 LLM 调用
          ✅ 支持 mock 模式
          ✅ 支持 OpenAI-compatible API
          ✅ 支持 system prompt / user prompt
          ✅ 支持 temperature / max_tokens
          ✅ 返回统一 LLMResponse
          ✅ 提供 JSON 输出解析能力
          ✅ 屏蔽 Qwen / DeepSeek / OpenAI 的调用差异

"""
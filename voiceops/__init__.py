"""voiceops-lab — 语音操控的智能硬件工作台。

架构：六边形（Ports & Adapters）
- voiceops.ports    ：全部接口（Protocol），核心域唯一依赖
- voiceops.core     ：核心域（编排/安全/记忆），零外部 SDK 依赖
- voiceops.adapters ：外部能力适配器（LLM/语音/硬件/存储），可替换
- voiceops.mcp_server：MCP 协议暴露工具集
"""

__version__ = "0.1.0"

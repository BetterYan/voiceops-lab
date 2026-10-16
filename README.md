# VoiceOps Lab — 语音操控的智能硬件工作台

连接大模型智能与实体硬件交互的 Agent 系统 PoC（面试作品）。
**架构方案文档：[docs/design.html](docs/design.html)（浏览器打开）**

## 运行（uv 管理）

```bash
# 依赖锁定 + 环境同步（自动创建 .venv，Python ≥3.11）
uv sync

# 测试：13 passed（全离线：无 API Key / 无网络 / 无真实硬件）
uv run pytest tests/ -q

# 三个演示场景（离线）
uv run voiceops --demo 1   # 条件分支 + HITL（放行 / 拒绝两条路径）
uv run voiceops --demo 2   # 故障自愈：Reflector 修订计划
uv run voiceops --demo 3   # 记忆闭环：情节召回 + 程序记忆自动提炼

# MCP Server（stdio）
uv run voiceops --mcp

# 网页 Demo：浏览器麦克风语音操控方块（默认 8080 端口）
uv run voiceops --web --port 8080
# 打开 http://127.0.0.1:8080 → 连接并授权麦克风 → 说「向左移动两格」
# 无 VOLC_API_KEY 时自动降级为文字指令模式（同一编排/安全/记忆链路）
# 有 AZURE_OPENAI_ENDPOINT 时自动用真 LLM 解析任意自然语言，否则用离线 RuleLLM

# 依赖变更：uv add <pkg>；升级：uv lock --upgrade && uv sync
```

## 架构速览（六边形：Ports & Adapters）

```
core/      编排(LangGraph) · SafetyGate · MemoryService · ToolRegistry   ← 零 SDK 依赖
ports/     LLMProvider · VoiceGateway · HardwareTransport · MemoryStore
           UserInteraction(HITL) · EventPublisher · Clock                ← 核心域唯一依赖
adapters/  ScriptedLLM · SimulatedHardware(故障注入) · SqliteVectorMemory
           ConsoleVoicePort(文本) · VolcanoVoiceAdapter(火山引擎STT/TTS,已实现)
mcp_server/ MCP stdio 服务：工具契约一处定义，双端一致
```

**核心论点**：LLM / 语音 / 硬件 / 存储全部可替换——升级 ChromaDB、接入真实串口，
核心域与安全层零改动；编排/安全/记忆内核确定性可测（20 测试离线全绿，
火山语音协议用本地假服务器验证，无需凭据）。

## 模块深度

| 模块 | 深度 |
|---|---|
| MCP + Skills / 编排 / 推理三段 / 安全门禁+回滚 | ✅ 完整实现 |
| 长期记忆 | ⚙️ v1（衰减评分与漂移仲裁为深入研究项） |
| 语音网关 | ✅ 协议层已实现（火山引擎 doubao-seed-asr/tts-2.0；凭据见下方 .env） |

## 火山引擎凭据（.env）

```bash
cp .env.example .env    # 填入 VOLC_API_KEY（方舟控制台开通个人语音模型后获取）
```

加载优先级：**已有环境变量 > 当前目录/.env > 项目根/.env**（不覆盖已有变量；
`.env` 已被 gitignore，模板见 `.env.example`）。

```python
from voiceops.adapters.volcano_voice import VolcanoVoiceAdapter, VolcanoConfig
adapter = VolcanoVoiceAdapter(VolcanoConfig.from_env())
```

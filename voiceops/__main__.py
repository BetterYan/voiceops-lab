"""CLI 入口：python -m voiceops --demo 1|2|3 | --chat | --mcp

演示模式完全离线（FakeLLM + 仿真硬件 + AutoConfirm）；
--chat 为真实 LLM 交互模式，需配置 Azure OpenAI 环境变量（可选扩展）。
"""
from __future__ import annotations

import argparse
import json
import sys

from voiceops.adapters.event_bus import AutoConfirm, ConsoleInteraction
from voiceops.app import build_container
from voiceops.scenarios import demo1_branch_hitl, demo2_failure_recovery, demo3_memory


def _print_trace(bus) -> None:
    """订阅全部事件做彩色摘要打印 —— 事件总线的可观测性红利。"""
    def handler(event: dict) -> None:
        name = event["name"]
        payload = event["payload"]
        if name in ("plan.created",):
            print(f"  [plan] {payload.get('tasks')}")
        elif name in ("task.started", "task.ok", "task.failed", "task.retry"):
            print(f"  [{name.split('.')[-1]}] {payload.get('task', '')}")
        elif name == "plan.revised":
            print(f"  [reflect→修订计划] 插入任务: {payload.get('insert')}")
        elif name == "safety.confirm_required":
            print(f"  [安全门禁] R2 确认请求: {payload.get('tool')} {payload.get('args')}")
        elif name in ("safety.approved", "safety.denied"):
            print(f"  [安全门禁] {name.split('.')[-1]}")
        elif name == "tool.result":
            print(f"  [tool] {payload.get('tool')} ok={payload.get('ok')}")
        elif name == "loop.guard":
            print(f"  [loop guard] {payload.get('tool')} 重复调用，强制终止")
        elif name == "memory.saved":
            print(f"  [memory] 已保存 {payload.get('kind')}")
    for evt_name in ("plan.created", "plan.revised", "task.started", "task.ok",
                     "task.failed", "task.retry", "safety.confirm_required",
                     "safety.approved", "safety.denied", "tool.result",
                     "loop.guard", "memory.saved"):
        bus.subscribe(evt_name, handler)


def run_demo(which: str) -> None:
    if which == "1":
        c = build_container(llm=demo1_branch_hitl(approved=True)[0],
                            interaction=AutoConfirm(True))
        llm, text = demo1_branch_hitl(approved=True)
        print("== Demo 1: 条件分支 + HITL（自动放行） ==")
        _print_trace(c.bus)
        result = c.orchestrator.run(text, thread_id="demo1")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\n== Demo 1b: 同场景、用户拒绝 ==")
        c2 = build_container(llm=demo1_branch_hitl(approved=False)[0],
                             interaction=AutoConfirm(False))
        _print_trace(c2.bus)
        result = c2.orchestrator.run(text, thread_id="demo1b")
        denied = c2.events("safety.denied")
        print(f"门禁拒绝事件: {len(denied)} 条；actuate 未执行: "
              f"{all(h['action'] != 'power_off' for h in c2.hardware.history)}")
    elif which == "2":
        llm, text = demo2_failure_recovery()
        c = build_container(llm=llm)
        print("== Demo 2: 故障自愈（注入 1 次通信故障） ==")
        _print_trace(c.bus)
        c.hardware.inject_fault("drop", times=1)
        result = c.orchestrator.run(text, thread_id="demo2")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        revised = c.events("plan.revised")
        print(f"Reflector 修订计划: {len(revised)} 次")
    elif which == "3":
        (llm1, text1), (llm1b, text1b), (llm2, text2) = demo3_memory()
        c = build_container(llm=llm1)
        print("== Demo 3: 记忆闭环 ==")
        _print_trace(c.bus)
        r1 = c.orchestrator.run(text1, thread_id="demo3")
        print(f"会话1: {r1['final']}")
        # 相同成功工具序列的第二次会话 → 程序记忆自动提炼
        c.orchestrator.deps.llm = llm1b
        r1b = c.orchestrator.run(text1b, thread_id="demo3")
        print(f"会话2: {r1b['final']}")
        # 会话 3 换上追问脚本（同一容器：硬件/记忆状态延续）
        c.orchestrator.deps.llm = llm2
        r2 = c.orchestrator.run(text2, thread_id="demo3")
        print(f"会话3（追问）: {r2['final']}")
        episodic = c.events("memory.saved")
        procedures = [r for r in c.store.all_records() if r.kind == "procedural"]
        print(f"记忆写入事件: {len(episodic)} 条；程序记忆: "
              f"{[p.text for p in procedures] or '无'}")
    else:
        print("未知 demo；可选 1|2|3")
        sys.exit(1)


def run_chat() -> None:
    """真实 LLM 交互（需要 Azure OpenAI 环境变量；语音接入火山引擎后的聊天入口）。"""
    import os
    if not os.environ.get("AZURE_OPENAI_ENDPOINT"):
        print("请配置 AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY 等环境变量，"
              "或使用 --demo 体验离线演示。")
        sys.exit(1)
    from voiceops.adapters.azure_llm import AzureOpenAIAdapter
    c = build_container(llm=AzureOpenAIAdapter(), interaction=ConsoleInteraction())
    print("voiceops chat（文本模式；exit 退出）")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in ("exit", "quit", ""):
            continue
        result = c.orchestrator.run(text, thread_id="chat")
        print(result["final"])


def run_web(port: int) -> None:
    """网页 Demo：浏览器麦克风语音操控方块（uvicorn 承载 FastAPI）。"""
    import uvicorn

    from voiceops.logging_setup import setup_logging
    from voiceops.webapp import build_web_container, create_app

    setup_logging()
    container, volc_config = build_web_container()
    app = create_app(container, volc_config)
    print("=" * 56)
    print("  VoiceOps Web Demo: http://127.0.0.1:" + str(port))
    print("  指令示例：向左移动两格 / 移到右上角 / 回到中心")
    print("  语音：需 .env 配置 VOLC_API_KEY；无凭据时用文字输入体验")
    print("=" * 56)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(prog="voiceops")
    parser.add_argument("--demo", choices=["1", "2", "3"], help="离线演示场景")
    parser.add_argument("--chat", action="store_true", help="真实 LLM 交互（需 Azure 凭据）")
    parser.add_argument("--web", action="store_true", help="启动网页 Demo（麦克风语音操控方块）")
    parser.add_argument("--port", type=int, default=8080, help="Web Demo 端口")
    parser.add_argument("--mcp", action="store_true", help="以 stdio 启动 MCP Server")
    args = parser.parse_args()
    if args.mcp:
        from voiceops.mcp_server.server import main as mcp_main
        mcp_main()
    elif args.web:
        run_web(args.port)
    elif args.demo:
        run_demo(args.demo)
    elif args.chat:
        run_chat()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

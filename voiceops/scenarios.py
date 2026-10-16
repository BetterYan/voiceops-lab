"""演示/测试共用场景：FakeLLM 脚本 = "LLM 在该场景下的确定性化身"。

一份脚本同时服务 CLI 演示与 pytest 断言 —— 演示与测试同源，
避免"演示能跑、测试另写"的双轨漂移。
"""
from __future__ import annotations

from voiceops.adapters.fake_llm import ScriptedLLM, call_tool, finish, fail, plan, reflect


def demo1_branch_hitl(approved: bool):
    """场景 1：条件分支 + HITL。「温度超 50 度就关电源」"""
    llm = ScriptedLLM([
        plan([
            {"id": "t1", "description": "读取 env_sensor 温度", "tool_hint": "hardware.read_state"},
            {"id": "t2", "description": "若温度超过 50 度则关闭 instr_power 输出",
             "tool_hint": "hardware.actuate"},
        ]),
        # t1：读温度，报告后结束本任务
        call_tool("hardware.read_state", device_id="env_sensor"),
        finish("环境温度 55 度。"),
        # t2：LLM 依据 t1 观察做条件分支（55 > 50）→ 触发 R2 关机
        call_tool("hardware.actuate", device_id="instr_power", action="power_off"),
        finish("温度 55 度超过阈值 50 度，已执行关机。") if approved
        else finish("温度超标，但用户拒绝关机操作。"),
    ])
    return llm, "看一下环境传感器温度，如果超过 50 度就把电源输出关掉"


def demo2_failure_recovery():
    """场景 2：故障自愈。读数遇通信故障 → Reflector 插入补救任务 → 成功。"""
    llm = ScriptedLLM([
        plan([{"id": "t1", "description": "读取 instr_power 状态",
               "tool_hint": "hardware.read_state"}]),
        call_tool("hardware.read_state", device_id="instr_power"),   # 第 1 次：故障
        fail("hardware.read_state 通信超时"),
        reflect("revise", reason="通信故障，先重置链路再重试",
                new_task={"id": "t_fix", "description": "重置与 instr_power 的连接",
                          "tool_hint": "hardware.reset_connection"}),
        call_tool("hardware.reset_connection", device_id="instr_power"),
        call_tool("hardware.read_state", device_id="instr_power"),   # 第 2 次：成功
        finish("通信恢复，instr_power 状态读取成功：输出 on，电压 3.3V。"),
    ])
    return llm, "读取电源当前状态"


def demo3_memory():
    """场景 3：记忆闭环。两次相同校准会话 → 程序记忆自动提炼；追问 → 情节记忆召回。"""
    def calibration_script():
        return ScriptedLLM([
            plan([{"id": "t1", "description": "校准：把电源电压设到 5V 并确认输出",
                   "tool_hint": "hardware.actuate"}]),
            call_tool("hardware.actuate", device_id="instr_power",
                      action="set_voltage", params={"voltage": 5.0}),
            call_tool("hardware.read_state", device_id="instr_power"),
            finish("校准完成：电压已设为 5.0V，输出正常。"),
        ])

    llm_run3 = ScriptedLLM([
        plan([{"id": "t1", "description": "从长期记忆中查找上次校准的参数",
               "tool_hint": "memory.search"}]),
        call_tool("memory.search", query="校准 电压 参数"),
        finish("根据记忆：上次校准把电压设为 5.0V，输出正常。"),
    ])
    return (calibration_script(), "把电源校准到 5V 并确认输出"), \
           (calibration_script(), "再次把电源校准到 5V 并确认输出"), \
           (llm_run3, "上次校准用的什么参数？")


def loop_scenario():
    """循环防护：LLM 陷入重复调用 → loop guard 强制终止（不依赖 LLM 自醒）。"""
    llm = ScriptedLLM([
        plan([{"id": "t1", "description": "读取 instr_power 状态"}]),
        call_tool("hardware.read_state", device_id="instr_power"),
        call_tool("hardware.read_state", device_id="instr_power"),
        call_tool("hardware.read_state", device_id="instr_power"),
        # 第 3 次重复触发 loop guard；此脚本若被继续调用即测试失败
        call_tool("hardware.read_state", device_id="instr_power"),
    ])
    return llm, "读取电源状态"

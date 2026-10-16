"""真实硬件总线适配器 —— 接口预留桩（本项目硬件采用仿真）。

与 SimulatedHardware 实现同一 HardwareTransport 契约。
类结构与协议映射写在 docstring，证明扩展路径清晰；实现为后续迭代。
"""
from __future__ import annotations

from typing import Any, Callable

from voiceops.ports import DeviceState
from voiceops.ports.hardware import ActuationResult


class SerialTransportAdapter:
    """串口适配器（预留）。

    协议映射设计：
      read_state(device_id)  -> 发送 "GET:{device_id}\n" → 解析 "K=V;K=V" 应答帧
      actuate(device_id,...) -> 发送 "SET:{device_id}:{action}:{params}\n" → ACK/NAK
      reset_connection       -> 关闭重开串口 + DTR/RTS 复位序列
      帧完整性：CRC16 校验；超时重试 2 次（幂等读）；
      R2 动作（power_off）在 SafetyGate 层已被门禁，串口层只负责可靠传输。
    """

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        raise NotImplementedError("真实串口对接为后续迭代（当前使用 SimulatedHardware）")

    def read_state(self, device_id: str) -> DeviceState: ...
    def actuate(self, device_id: str, action: str, params: dict[str, Any]) -> ActuationResult: ...
    def reset_connection(self, device_id: str) -> None: ...
    def subscribe_events(self, handler: Callable[[dict], None]) -> None: ...


class MqttTransportAdapter:
    """MQTT 适配器（预留）—— 对应简历中「MQTT 汇报机器人移动轨迹与状态」的经验。

    主题约定：
      voiceops/{site}/{device_id}/state   遥测（retain）
      voiceops/{site}/{device_id}/cmd     指令下行
      voiceops/{site}/{device_id}/evt     事件上行
    read_state 依赖 retain 的最新遥测 + 请求-响应（correlation_id）补齐。
    """

    def __init__(self, broker: str, site: str) -> None:
        raise NotImplementedError("真实 MQTT 对接为后续迭代（当前使用 SimulatedHardware）")

    def read_state(self, device_id: str) -> DeviceState: ...
    def actuate(self, device_id: str, action: str, params: dict[str, Any]) -> ActuationResult: ...
    def reset_connection(self, device_id: str) -> None: ...
    def subscribe_events(self, handler: Callable[[dict], None]) -> None: ...

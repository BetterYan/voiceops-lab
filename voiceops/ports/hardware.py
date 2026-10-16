"""硬件传输 Port。

设计理念：
- 硬件交互收敛为极小的读写事件面（read_state / actuate / subscribe_events /
  reset_connection）。仿真实现与真实总线（串口/MQTT/GPIB）实现同一契约，
  编排与安全层对"硬件是真是假"零感知。
- actuate 返回执行前的状态快照（previous_state），供 journal 构建逆向操作 ——
  回滚能力从接口契约层就得到保证，而不是事后补救。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


class HardwareError(Exception):
    """硬件通信/执行失败。模拟故障与真实故障共用同一异常类型。"""


@dataclass
class DeviceState:
    device_id: str
    online: bool
    properties: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


@dataclass
class ActuationResult:
    ok: bool
    message: str
    device_id: str
    action: str
    previous_state: dict[str, Any] | None = None  # 执行前属性快照（用于回滚）


@runtime_checkable
class HardwareTransport(Protocol):
    def read_state(self, device_id: str) -> DeviceState: ...

    def actuate(self, device_id: str, action: str, params: dict[str, Any]) -> ActuationResult: ...

    def reset_connection(self, device_id: str) -> None:
        """重置与设备的通信链路（故障自愈的标配动作）。"""
        ...

    def subscribe_events(self, handler: Callable[[dict[str, Any]], None]) -> None: ...

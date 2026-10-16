"""SimulatedHardware：虚拟测试仪器工作台（默认硬件适配器）。

设计理念：
- 与真实总线适配器实现同一 HardwareTransport 契约 ——
  编排/安全/记忆对"硬件是真是假"零感知。
- **故障注入是头等公民**：inject_fault("drop", n) 让接下来 n 次调用抛
  HardwareError —— 故障自愈（Reflector）与告警路径因此可被确定性测试。
- actuate 返回执行前属性快照（previous_state），支撑 journal 回滚。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from voiceops.ports import Clock, DeviceState
from voiceops.ports.hardware import ActuationResult, HardwareError


class SimulatedHardware:
    """设备：instr_power（可编程电源）、env_sensor（温度）、robot_arm。"""

    def __init__(self, clock: Clock, default_devices: dict[str, dict] | None = None) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._devices: dict[str, dict[str, Any]] = default_devices or {
            "instr_power": {"output": "on", "voltage": 3.3, "temperature": 42.0},
            "env_sensor": {"temperature": 46.0, "humidity": 55},
            "robot_arm": {"position": "home", "gripper": "open"},
            "block": {"x": 5, "y": 5},   # 网页 Demo：语音操控方块（虚拟执行器）
        }
        self._online: set[str] = set(self._devices)
        self._subscribers: list[Callable[[dict], None]] = []
        self._fault: dict[str, Any] = {}      # {"mode":"drop","remaining":n}
        self.history: list[dict] = []

    # ---- 故障注入（测试/演示专用） -----------------------------------------

    def inject_fault(self, mode: str = "drop", times: int = 1) -> None:
        """接下来 times 次任意调用抛 HardwareError，之后自动恢复。"""
        self._fault = {"mode": mode, "remaining": times}

    # ---- HardwareTransport ------------------------------------------------

    def _maybe_fault(self) -> None:
        if self._fault.get("remaining", 0) > 0:
            self._fault["remaining"] -= 1
            raise HardwareError("communication timeout (simulated fault)")

    def read_state(self, device_id: str) -> DeviceState:
        self._maybe_fault()
        with self._lock:
            if device_id not in self._devices:
                raise HardwareError(f"unknown device: {device_id}")
            return DeviceState(device_id=device_id, online=device_id in self._online,
                               properties=dict(self._devices[device_id]),
                               updated_at=self._clock.now())

    def actuate(self, device_id: str, action: str, params: dict[str, Any]) -> ActuationResult:
        self._maybe_fault()
        with self._lock:
            if device_id not in self._devices:
                raise HardwareError(f"unknown device: {device_id}")
            previous = dict(self._devices[device_id])
            if action == "set_voltage":
                self._devices[device_id]["voltage"] = float(params["voltage"])
                msg = f"voltage -> {params['voltage']}V"
            elif action == "power_off":
                self._devices[device_id]["output"] = "off"
                msg = "output off"
            elif action == "set_property":
                self._devices[device_id][params["key"]] = params["value"]
                msg = f"{params['key']} -> {params['value']}"
            elif action == "move":
                # 方块相对移动（0..9 网格边界钳位）
                dx, dy = int(params.get("dx", 0)), int(params.get("dy", 0))
                self._devices[device_id]["x"] = max(0, min(9, self._devices[device_id]["x"] + dx))
                self._devices[device_id]["y"] = max(0, min(9, self._devices[device_id]["y"] + dy))
                msg = f"moved to ({self._devices[device_id]['x']}, {self._devices[device_id]['y']})"
            elif action == "move_to":
                self._devices[device_id]["x"] = max(0, min(9, int(params["x"])))
                self._devices[device_id]["y"] = max(0, min(9, int(params["y"])))
                msg = f"moved to ({self._devices[device_id]['x']}, {self._devices[device_id]['y']})"
            else:
                self._devices[device_id][action] = params.get("value", True)
                msg = f"{action} ok"
            self.history.append({"device": device_id, "action": action,
                                 "params": params, "ts": self._clock.now()})
        self._publish({"type": "state_changed", "device": device_id,
                       "properties": dict(self._devices[device_id])})
        return ActuationResult(ok=True, message=msg, device_id=device_id,
                               action=action, previous_state=previous)

    def reset_connection(self, device_id: str) -> None:
        with self._lock:
            self._fault = {}          # 重连即清除故障（自愈路径的物理基础）
            self._online.add(device_id)

    def subscribe_events(self, handler: Callable[[dict], None]) -> None:
        self._subscribers.append(handler)

    # ---- 内部 -------------------------------------------------------------

    def _publish(self, event: dict) -> None:
        for handler in self._subscribers:
            handler(event)

"""记忆存储 Port。

设计理念：
- MemoryStore 只负责"存取与检索"，衰减评分/遗忘策略/程序记忆提炼
  属于核心域 MemoryService 的策略逻辑 —— 存储可换（SQLite→ChromaDB→图数据库），
  策略留在域内持续演进。
- 检索返回 (record, 相似度) 二元组，衰减融合由核心域完成，存储层保持无策略。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class MemoryRecord:
    id: str
    kind: str                       # "episodic" | "procedural"
    text: str
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    importance: float = 0.5         # 0~1，重要性不随时间衰减（智能衰减的锚点）
    use_count: int = 0              # 被检索/复用次数（频率信号）
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    def save(self, record: MemoryRecord) -> str: ...

    def get(self, record_id: str) -> MemoryRecord | None: ...

    def all_records(self) -> list[MemoryRecord]: ...

    def touch(self, record_id: str) -> None:
        """命中后调用：use_count += 1（频率信号）。"""
        ...

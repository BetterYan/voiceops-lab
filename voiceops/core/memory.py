"""MemoryService：情节/程序记忆的动态融合 + 智能衰减（v1）。

设计理念（JD：攻克记忆漂移与上下文污染）：
- 融合：情节（发生过什么）与程序（怎么做）同库存储、统一检索，
  按 kind 过滤实现场景化注入。
- 智能衰减：score = 0.5×相似度 + 0.3×新近度 + 0.2×频率
    - 新近度 = 2^(-age/半衰期)，重要记录（importance 高）半衰期加倍
      —— "重要的事忘得慢"是可调策略而非一刀切。
    - 遗忘线：recency < 0.15 且 importance < 0.6 的记录不参与检索
      （软遗忘：不删数据，只不注入上下文 —— 可审计、可恢复）。
- 防漂移三原则（v1 已落地前两条）：
    1) 来源标注：检索结果携带 created_at/id 注入上下文；
    2) 软遗忘：过期记忆不进入上下文，但数据仍在；
    3) 【研究项】写入门禁：新记忆与高置信旧记忆冲突时需仲裁 —— 见 docs 深入研究清单。
- 程序记忆提炼：连续两次会话出现相同成功工具序列 → 自动沉淀 playbook。
"""
from __future__ import annotations

import math

from voiceops.ports import Clock, EventPublisher, MemoryRecord, MemoryStore, make_event

HALF_LIFE_DAYS = 7.0
FORGET_RECENCY = 0.15
FORGET_IMPORTANCE = 0.6


def _tokens(text: str) -> dict[str, int]:
    """极简分词：ASCII 词累积、CJK 单字切分。零依赖、确定性，便于测试。"""
    out: dict[str, int] = {}
    buf: list[str] = []

    def flush() -> None:
        if buf:
            word = "".join(buf)
            out[word] = out.get(word, 0) + 1
            buf.clear()

    for ch in text.lower():
        if "\u4e00" <= ch <= "\u9fff":      # CJK：单字成词（PoC 级检索足够）
            flush()
            out[ch] = out.get(ch, 0) + 1
        elif ch.isalnum():
            buf.append(ch)
        else:
            flush()
    flush()
    return out


def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(n * b.get(w, 0) for w, n in a.items())
    na = math.sqrt(sum(n * n for n in a.values()))
    nb = math.sqrt(sum(n * n for n in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class MemoryService:
    def __init__(self, store: MemoryStore, clock: Clock, bus: EventPublisher) -> None:
        self._store = store
        self._clock = clock
        self._bus = bus
        self._recent_sequences: list[list[str]] = []   # 成功工具序列（程序记忆提炼原料）

    # ---- 写入 ------------------------------------------------------------

    def record_episode(self, text: str, tags: list[str] | None = None,
                       importance: float = 0.5) -> MemoryRecord:
        rec = MemoryRecord(id="", kind="episodic", text=text, tags=tags or [],
                           created_at=self._clock.now(), importance=importance)
        rec.id = self._store.save(rec)
        self._bus.publish(make_event("memory.saved", {"kind": "episodic", "id": rec.id}))
        return rec

    def record_procedure(self, name: str, steps: list[str]) -> MemoryRecord:
        rec = MemoryRecord(id="", kind="procedural", text=name, tags=list(steps),
                           created_at=self._clock.now(), importance=0.8,
                           meta={"steps": steps})
        rec.id = self._store.save(rec)
        self._bus.publish(make_event("memory.saved", {"kind": "procedural", "id": rec.id}))
        return rec

    # ---- 检索（衰减融合在打分里） ----------------------------------------

    def search(self, query: str, kind: str | None = None,
               limit: int = 5) -> list[tuple[MemoryRecord, float]]:
        now = self._clock.now()
        q = _tokens(query)
        scored: list[tuple[MemoryRecord, float]] = []
        for rec in self._store.all_records():
            if kind and rec.kind != kind:
                continue
            age_days = max(0.0, (now - rec.created_at) / 86400.0)
            half_life = HALF_LIFE_DAYS * (2.0 if rec.importance >= 0.8 else 1.0)
            recency = 2.0 ** (-age_days / half_life)
            if recency < FORGET_RECENCY and rec.importance < FORGET_IMPORTANCE:
                continue  # 软遗忘
            sim = _cosine(q, _tokens(rec.text + " " + " ".join(rec.tags)))
            if sim <= 0.0:
                continue
            frequency = rec.use_count / (1.0 + rec.use_count)
            score = 0.5 * sim + 0.3 * recency + 0.2 * frequency
            scored.append((rec, score))
        scored.sort(key=lambda t: t[1], reverse=True)
        hits = scored[:limit]
        for rec, _ in hits:
            self._store.touch(rec.id)   # 命中即强化（频率信号闭环）
        return hits

    # ---- 程序记忆自动提炼 -------------------------------------------------

    def observe_tool_sequence(self, sequence: list[str]) -> dict | None:
        """每次会话成功结束后调用：相同序列出现 ≥2 次 → 自动沉淀程序记忆。"""
        if len(sequence) < 2:
            return None
        if sequence in self._recent_sequences:
            name = "auto_playbook:" + "+".join(sequence)
            rec = self.record_procedure(name, sequence)
            return {"extracted": True, "id": rec.id, "name": name}
        self._recent_sequences.append(sequence)
        return None

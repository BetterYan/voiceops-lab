"""SqliteVectorMemory：MemoryStore 的默认实现。

设计理念：
- 当前实现用 SQLite + 纯 Python 词袋余弦 —— 零重量级依赖、结果确定可复现；
  接口与向量数据库（ChromaDB/pgvector）同构，切换存储不影响核心域策略。
- 这本身是可扩展性论证的一部分：存储升级 = 换适配器。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid

from voiceops.ports import MemoryRecord


class SqliteVectorMemory:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS records (
                   id TEXT PRIMARY KEY, kind TEXT, text TEXT, tags TEXT,
                   created_at REAL, importance REAL, use_count INTEGER, meta TEXT)""")

    def save(self, record: MemoryRecord) -> str:
        record.id = record.id or uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?)",
                (record.id, record.kind, record.text, json.dumps(record.tags),
                 record.created_at, record.importance, record.use_count,
                 json.dumps(record.meta)))
            self._conn.commit()
        return record.id

    def get(self, record_id: str) -> MemoryRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def all_records(self) -> list[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM records").fetchall()
        return [self._row_to_record(r) for r in rows]

    def touch(self, record_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE records SET use_count = use_count + 1 WHERE id=?", (record_id,))
            self._conn.commit()

    @staticmethod
    def _row_to_record(row) -> MemoryRecord:
        return MemoryRecord(id=row[0], kind=row[1], text=row[2], tags=json.loads(row[3]),
                            created_at=row[4], importance=row[5], use_count=row[6],
                            meta=json.loads(row[7]))

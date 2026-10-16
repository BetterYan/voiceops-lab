"""pytest 公共夹具：离线容器（FakeClock + AutoConfirm + 全量事件记录）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voiceops.adapters.event_bus import AutoConfirm, FakeClock  # noqa: E402
from voiceops.app import build_container  # noqa: E402


@pytest.fixture()
def make_container():
    """容器工厂：注入被测脚本 LLM 与确认策略。"""

    def _make(llm, approved: bool = True):
        clock = FakeClock()
        interaction = AutoConfirm(approved)
        return build_container(llm=llm, interaction=interaction, clock=clock)

    return _make

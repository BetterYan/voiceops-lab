"""Agent Skills：Markdown 技能包加载器（JD：Agent Skills 开发与优化）。

技能包格式（skills_md/*.md）：
    ---
    name: calibration
    when: 需要对仪器进行校准时
    tools: [hardware.read_state, hardware.actuate]
    ---
    1. 读取仪器当前温度，确认 < 60 度
    2. 设置电压到 5.0V 并等待稳定
    3. 记录基线测量值

设计理念：Skills 是"程序性知识的声明式外置"——
真实 LLM 场景下 Planner 依据 when 字段挑选技能注入提示；
加载器与格式是稳定的，技能内容可由非工程师持续贡献。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    name: str
    when: str
    tools: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


def load_skills(directory: str | Path) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    for path in sorted(Path(directory).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
        if not m:
            continue
        head, body = m.group(1), m.group(2)
        meta: dict = {}
        for line in head.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        steps = [ln.strip()[2:] for ln in body.splitlines() if ln.strip().startswith(("1.", "2.", "3.", "4.", "5.", "-", "*"))]
        skill = Skill(name=meta.get("name", path.stem), when=meta.get("when", ""),
                      tools=[t.strip() for t in meta.get("tools", "").strip("[]").split(",") if t.strip()],
                      steps=steps)
        skills[skill.name] = skill
    return skills

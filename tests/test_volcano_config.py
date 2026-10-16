"""VolcanoConfig .env 加载策略测试。"""
from __future__ import annotations

from pathlib import Path

from voiceops.adapters.volcano_voice import VolcanoConfig


def _clean_volc_env(monkeypatch):
    for key in ("VOLC_API_KEY", "VOLC_STT_RESOURCE_ID", "VOLC_TTS_RESOURCE_ID",
                "VOLC_TTS_SPEAKER", "VOLC_UID"):
        monkeypatch.delenv(key, raising=False)


def test_from_env_reads_explicit_dotenv_file(tmp_path, monkeypatch):
    _clean_volc_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VOLC_API_KEY=dotenv-key-123\n"
        "VOLC_TTS_SPEAKER=zh_female_test_voice\n",
        encoding="utf-8")

    cfg = VolcanoConfig.from_env(env_file)

    assert cfg.api_key == "dotenv-key-123"
    assert cfg.speaker == "zh_female_test_voice"
    # 未配置项保持协议默认值
    assert cfg.stt_resource_id == "volc.seedasr.sauc.duration"


def test_real_env_takes_priority_over_dotenv(tmp_path, monkeypatch):
    """优先级：已有环境变量 > .env（凭据注入不应被文件静默覆盖）。"""
    _clean_volc_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("VOLC_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("VOLC_API_KEY", "env-key")

    cfg = VolcanoConfig.from_env(env_file)

    assert cfg.api_key == "env-key"


def test_missing_dotenv_is_silent(tmp_path, monkeypatch):
    """没有任何 .env / 环境变量：from_env 返回空 key 配置（由 start_session 报错）。"""
    _clean_volc_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    # 指向不存在的显式路径，避免 from_env 的项目根回退找到真实 .env（测试隔离）
    cfg = VolcanoConfig.from_env(tmp_path / "nonexistent.env")

    assert cfg.api_key == ""
    assert cfg.stt_url.startswith("wss://openspeech.bytedance.com")

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

from xhsmovieassistant import XhsMovieAssistant


ROOT = Path(__file__).resolve().parents[2]


def test_market_metadata_matches_plugin_class() -> None:
    metadata_path = ROOT / "package.v2.json"
    assert metadata_path.is_file(), "MoviePilot V2 marketplace metadata is missing"

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    item = metadata["XhsMovieAssistant"]

    assert item["name"] == XhsMovieAssistant.plugin_name
    assert item["description"] == "读取授权账号的评论艾特，复用 MoviePilot AI 识别影视并安全创建订阅。"
    assert item["labels"] == "订阅,AI,小红书,RedNote"
    assert item["version"] == XhsMovieAssistant.plugin_version
    assert item["icon"] == XhsMovieAssistant.plugin_icon
    assert item["author"] == XhsMovieAssistant.plugin_author
    assert item["system_version"] == ">=2.15.6"
    assert item["v2"] is True
    assert item["v3"] is False
    assert item["history"][f"v{XhsMovieAssistant.plugin_version}"]


def test_market_icon_is_local_png_with_supported_dimensions() -> None:
    icon_path = ROOT / "icons" / XhsMovieAssistant.plugin_icon
    assert icon_path.is_file(), "Marketplace icon is missing"

    signature = b"\x89PNG\r\n\x1a\n"
    header = icon_path.read_bytes()[:24]
    assert header[:8] == signature
    assert header[12:16] == b"IHDR"
    width, height = struct.unpack(">II", header[16:24])
    assert width >= 128
    assert height >= 128


def test_readme_documents_safe_moviepilot_operator_flow() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    required_phrases = (
        "MoviePilot >= 2.15.6",
        "自定义插件市场",
        "本仓库实际发布后的公开 HTTPS Git URL",
        "Chromium",
        "RedNote",
        "授权用户 ID",
        "dry-run",
        "真实订阅",
        "300012",
        "验证码",
        "登录过期",
        "AI",
        "歧义",
        "公开回复",
        "风险更高",
    )
    assert all(phrase in readme for phrase in required_phrases)
    assert readme.index("dry-run") < readme.index("真实订阅")
    assert "Cookie" not in readme
    assert "xsec_token" not in readme
    assert "MP token" not in readme
    assert "LLM key" not in readme
    assert "原始通知" not in readme


def test_readme_documents_browser_and_public_reply_recovery() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    required_statuses = ("`ERROR`", "`PENDING`", "`SENT`", "`FAILED`")
    assert all(status in readme for status in required_statuses)
    assert "已成功发出" in readme
    assert "`POSTED`" not in readme
    assert "不会自动重试" in readme
    assert "人工处理" in readme


def test_repository_does_not_track_runtime_secrets() -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    forbidden_names = {"app.db", "Cookies", ".env"}

    assert not any(Path(path).name in forbidden_names for path in tracked)
    assert not any("/browser/" in f"/{path}/" for path in tracked)

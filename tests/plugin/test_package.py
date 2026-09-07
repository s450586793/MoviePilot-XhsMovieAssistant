from __future__ import annotations

import json
import re
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
    assert item["level"] == 1
    assert item["system_version"] == ">=2.15.6"
    assert item["v2"] is True
    assert item["v3"] is False
    assert item["history"] == {
        f"v{XhsMovieAssistant.plugin_version}": (
            "首个可测试版本，支持扫码、授权艾特、AI 识别、去重和订阅。"
        )
    }


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


def test_vue_federation_package_and_tracked_build_are_installable() -> None:
    plugin_root = ROOT / "plugins.v2" / "xhsmovieassistant"
    package = json.loads((plugin_root / "package.json").read_text(encoding="utf-8"))
    tracked = subprocess.check_output(
        ["git", "ls-files", "plugins.v2/xhsmovieassistant/dist/assets"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    remote_entry = (plugin_root / "dist" / "assets" / "remoteEntry.js").read_text(
        encoding="utf-8"
    )

    assert XhsMovieAssistant().get_render_mode() == ("vue", "dist/assets")
    assert package["scripts"]["build"] == "vite build"
    assert package["scripts"]["test"] == "vitest run"
    assert {
        "vue",
        "vite",
        "@vitejs/plugin-vue",
        "@originjs/vite-plugin-federation",
        "vitest",
        "@vue/test-utils",
        "jsdom",
    } <= (
        set(package["dependencies"]) | set(package["devDependencies"])
    )
    assert (plugin_root / "package-lock.json").is_file()
    assert (plugin_root / "vite.config.js").is_file()
    assert (plugin_root / "src" / "main.js").is_file()
    assert (plugin_root / "src" / "components" / "Config.vue").is_file()
    assert (plugin_root / "src" / "components" / "Page.vue").is_file()
    assert (plugin_root / "dist" / "assets" / "remoteEntry.js").is_file()
    config_assets = list((plugin_root / "dist" / "assets").glob("__federation_expose_Config-*.js"))
    page_assets = list((plugin_root / "dist" / "assets").glob("__federation_expose_Page-*.js"))
    assert len(config_assets) == 1
    assert len(page_assets) == 1
    assert remote_entry.count('"./Config"') == 1
    assert remote_entry.count('"./Page"') == 1
    assert f"./{config_assets[0].name}" in remote_entry
    assert f"./{page_assets[0].name}" in remote_entry
    config_asset = config_assets[0].read_text(encoding="utf-8")
    page_asset = page_assets[0].read_text(encoding="utf-8")
    assert "小红书影视助手配置" in config_asset
    assert "notifications_enabled: true" in config_asset
    assert "poll_interval_minutes: 2" in config_asset
    assert "confidence_threshold: 0.85" in config_asset
    assert "template_SUBSCRIBED" in config_asset
    assert "template_FAILED" in config_asset
    assert "小红书影视助手运行面板" in page_asset
    assert "影视类型必须是电影或电视剧" in page_asset
    assert "function actionFeedback" in page_asset
    assert "status === 'FAILED'" in page_asset
    assert "status === 'NEED_CONFIRMATION'" in page_asset
    assert "min-width: 44px" in "\n".join(
        path.read_text(encoding="utf-8")
        for path in (plugin_root / "dist" / "assets").glob("*.css")
    )
    assert {
        "plugins.v2/xhsmovieassistant/src/components/Page.spec.js",
        "plugins.v2/xhsmovieassistant/src/components/Config.spec.js",
    } <= set(
        subprocess.check_output(
            ["git", "ls-files", "plugins.v2/xhsmovieassistant/src/components"],
            cwd=ROOT,
            text=True,
        ).splitlines()
    )
    assert {str(path.relative_to(ROOT)) for path in config_assets + page_assets} <= set(
        tracked
    )


def test_vue_page_uses_host_api_and_edited_manual_values_without_secrets() -> None:
    plugin_root = ROOT / "plugins.v2" / "xhsmovieassistant"
    page = (plugin_root / "src" / "components" / "Page.vue").read_text(
        encoding="utf-8"
    )
    built_assets = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (plugin_root / "dist" / "assets").glob("*")
        if path.is_file()
    )

    assert "props.api.get('plugin/XhsMovieAssistant/state')" in page
    assert "props.api.post(" in page
    assert "plugin/XhsMovieAssistant/requests/${row.id}/manual" in page
    assert "title: draft.title.trim()" in page
    assert "original_title: draft.original_title.trim()" in page
    assert "media_type: draft.media_type" in page
    assert "year: optionalInteger(draft.year)" in page
    assert "season: optionalInteger(draft.season)" in page
    assert "标题不能为空" in page
    assert "影视类型必须是电影或电视剧" in page
    assert "await loadState()" in page
    assert "fetch(" not in page
    assert "authorization" not in page.casefold()
    assert "token" not in page.casefold()
    assert "api_key" not in built_assets.casefold()
    assert "xsec_token" not in built_assets.casefold()
    assert "authorization" not in built_assets.casefold()
    assert not re.search(r"[?&](?:token|apikey|api_key)=", built_assets, re.IGNORECASE)

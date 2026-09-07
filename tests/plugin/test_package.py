from __future__ import annotations

import json
import re
import struct
import subprocess
from pathlib import Path

import pytest

from xhsmovieassistant import XhsMovieAssistant


ROOT = Path(__file__).resolve().parents[2]


def _single_dist_asset(asset_dir: Path, pattern: str) -> Path:
    matches = sorted(asset_dir.rglob(pattern))
    assert len(matches) == 1, f"expected one {pattern} asset, found {matches}"
    return matches[0]


def _dist_asset_relative_paths(asset_dir: Path) -> set[str]:
    return {
        path.relative_to(asset_dir).as_posix()
        for path in asset_dir.rglob("*")
        if path.is_file()
    }


def _tracked_dist_asset_relative_paths(asset_dir: Path) -> set[str]:
    tracked_root = asset_dir.relative_to(ROOT)
    tracked = subprocess.check_output(
        ["git", "ls-files", tracked_root.as_posix()],
        cwd=ROOT,
        text=True,
    ).splitlines()
    return {
        Path(path).relative_to(tracked_root).as_posix() for path in tracked
    }


def _assert_delivery_assets_are_complete_and_tracked(
    delivery_assets: set[str], expected_assets: set[str], tracked_assets: set[str]
) -> None:
    assert delivery_assets == expected_assets, "dist/assets delivery files differ"
    assert delivery_assets == tracked_assets, "dist/assets delivery files are not tracked"


def _remote_entry_expose_block(remote_entry: str, expose: str) -> str:
    module_map = re.search(
        r"let moduleMap\s*=\s*\{(?P<entries>.*?)\};\s*const seen",
        remote_entry,
        re.DOTALL,
    )
    assert module_map, "remoteEntry moduleMap is missing"
    expose_block = re.search(
        rf'"{re.escape(expose)}"\s*:\s*\(\)\s*=>\s*\{{(?P<body>.*?)\}}(?=,|$)',
        module_map.group("entries"),
        re.DOTALL,
    )
    assert expose_block, f"remoteEntry {expose} expose is missing"
    return expose_block.group("body")


def _assert_remote_entry_expose_binding(
    remote_entry: str, expose: str, javascript_asset: Path, css_asset: Path
) -> None:
    expose_block = _remote_entry_expose_block(remote_entry, expose)
    css_references = re.findall(
        r'dynamicLoadingCss\(\["(?P<asset>[^"]+)"\]', expose_block
    )
    javascript_references = re.findall(
        r"__federation_import\('./(?P<asset>[^']+)'\)", expose_block
    )

    assert css_references == [css_asset.name], f"{expose} CSS mapping differs"
    assert javascript_references == [javascript_asset.name], (
        f"{expose} JavaScript mapping differs"
    )


def _manual_draft_media_type_initializer(page_code: str) -> str:
    match = re.search(
        r"media_type:\s*(?P<initializer>.+?),\s*\n\s*year:\s*",
        page_code,
    )
    assert match, "manual draft media_type initializer is missing"
    return re.sub(r"\s+", "", match.group("initializer"))


def _normalize_text_return_expression(page_code: str) -> str:
    match = re.search(
        r"function normalizeText\(value\)\s*\{\s*return\s+(?P<expression>.+?);?\s*\}",
        page_code,
    )
    assert match, "normalizeText return expression is missing"
    return re.sub(r"\s+", "", match.group("expression"))


def _assert_unknown_media_type_semantics_match(
    page_source: str, page_asset: str
) -> None:
    source_initializer = _manual_draft_media_type_initializer(page_source)
    asset_initializer = _manual_draft_media_type_initializer(page_asset)
    source_normalizer = _normalize_text_return_expression(page_source)
    asset_normalizer = _normalize_text_return_expression(page_asset)

    assert source_initializer == "normalizeText(row.media_type)"
    assert asset_initializer == source_initializer, (
        "built Page chunk does not preserve source manual draft media type semantics"
    )
    assert source_normalizer == "value==='-'||value==null?'':String(value)"
    assert asset_normalizer == source_normalizer, (
        "built Page chunk does not preserve source normalizeText semantics"
    )


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
        "https://github.com/s450586793/MoviePilot-XhsMovieAssistant/",
        "无需额外 Docker",
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
    assert "本仓库实际发布后的公开 HTTPS Git URL" not in readme
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
    asset_dir = plugin_root / "dist" / "assets"
    package = json.loads((plugin_root / "package.json").read_text(encoding="utf-8"))
    tracked_assets = _tracked_dist_asset_relative_paths(asset_dir)
    delivery_assets = _dist_asset_relative_paths(asset_dir)
    remote_entry_asset = asset_dir / "remoteEntry.js"
    config_asset = _single_dist_asset(
        asset_dir, "__federation_expose_Config-*.js"
    )
    config_css_asset = _single_dist_asset(
        asset_dir, "__federation_expose_Config-*.css"
    )
    page_asset = _single_dist_asset(asset_dir, "__federation_expose_Page-*.js")
    page_css_asset = _single_dist_asset(
        asset_dir, "__federation_expose_Page-*.css"
    )
    entry_asset = _single_dist_asset(asset_dir, "index-*.js")
    federation_import_asset = _single_dist_asset(
        asset_dir, "__federation_fn_import-*.js"
    )
    vue_export_helper_asset = _single_dist_asset(
        asset_dir, "_plugin-vue_export-helper-*.js"
    )
    expected_delivery_assets = {
        path.relative_to(asset_dir).as_posix()
        for path in (
            remote_entry_asset,
            config_asset,
            config_css_asset,
            page_asset,
            page_css_asset,
            entry_asset,
            federation_import_asset,
            vue_export_helper_asset,
        )
    }
    remote_entry = remote_entry_asset.read_text(encoding="utf-8")

    assert XhsMovieAssistant().get_render_mode() == ("vue", "dist/assets")
    assert package["scripts"]["build"] == "vite build && node normalize-dist.mjs"
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
    assert (plugin_root / "normalize-dist.mjs").is_file()
    assert (plugin_root / "src" / "main.js").is_file()
    assert (plugin_root / "src" / "components" / "Config.vue").is_file()
    assert (plugin_root / "src" / "components" / "Page.vue").is_file()
    _assert_delivery_assets_are_complete_and_tracked(
        delivery_assets, expected_delivery_assets, tracked_assets
    )
    assert remote_entry.count('"./Config"') == 1
    assert remote_entry.count('"./Page"') == 1
    _assert_remote_entry_expose_binding(
        remote_entry, "./Config", config_asset, config_css_asset
    )
    _assert_remote_entry_expose_binding(
        remote_entry, "./Page", page_asset, page_css_asset
    )
    config_code = config_asset.read_text(encoding="utf-8")
    page_code = page_asset.read_text(encoding="utf-8")
    assert "小红书影视助手配置" in config_code
    assert "notifications_enabled: true" in config_code
    assert "poll_interval_minutes: 2" in config_code
    assert "confidence_threshold: 0.85" in config_code
    assert "template_SUBSCRIBED" in config_code
    assert "template_FAILED" in config_code
    assert "小红书影视助手运行面板" in page_code
    assert "影视类型必须是电影或电视剧" in page_code
    assert "function actionFeedback" in page_code
    assert "status === 'FAILED'" in page_code
    assert "status === 'NEED_CONFIRMATION'" in page_code
    assert all(
        re.search(r"[ \t]+$", path.read_text(encoding="utf-8"), re.MULTILINE)
        is None
        for path in asset_dir.rglob("*")
        if path.is_file()
    ), "dist/assets contains trailing whitespace"
    assert "min-width: 44px" in "\n".join(
        path.read_text(encoding="utf-8")
        for path in (config_css_asset, page_css_asset)
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


def test_vue_federation_delivery_rejects_nested_untracked_asset(tmp_path) -> None:
    asset_dir = tmp_path / "assets"
    stale_asset = asset_dir / "stale" / "old-page.js"
    stale_asset.parent.mkdir(parents=True)
    (asset_dir / "remoteEntry.js").write_text("", encoding="utf-8")
    stale_asset.write_text("", encoding="utf-8")
    expected_assets = {"remoteEntry.js"}
    delivery_assets = _dist_asset_relative_paths(asset_dir)

    assert delivery_assets == {"remoteEntry.js", "stale/old-page.js"}

    with pytest.raises(AssertionError, match="delivery files differ"):
        _assert_delivery_assets_are_complete_and_tracked(
            delivery_assets, expected_assets, expected_assets
        )


def test_vue_remote_entry_rejects_wrong_config_asset_mapping() -> None:
    asset_dir = ROOT / "plugins.v2" / "xhsmovieassistant" / "dist" / "assets"
    remote_entry = (asset_dir / "remoteEntry.js").read_text(encoding="utf-8")
    config_asset = _single_dist_asset(
        asset_dir, "__federation_expose_Config-*.js"
    )
    config_css_asset = _single_dist_asset(
        asset_dir, "__federation_expose_Config-*.css"
    )
    page_asset = _single_dist_asset(asset_dir, "__federation_expose_Page-*.js")
    page_css_asset = _single_dist_asset(
        asset_dir, "__federation_expose_Page-*.css"
    )

    wrong_css_remote_entry = remote_entry.replace(
        config_css_asset.name, page_css_asset.name, 1
    )
    assert wrong_css_remote_entry != remote_entry
    with pytest.raises(AssertionError, match=r"\./Config CSS mapping differs"):
        _assert_remote_entry_expose_binding(
            wrong_css_remote_entry, "./Config", config_asset, config_css_asset
        )

    wrong_javascript_remote_entry = remote_entry.replace(
        config_asset.name, page_asset.name, 1
    )
    assert wrong_javascript_remote_entry != remote_entry
    with pytest.raises(
        AssertionError, match=r"\./Config JavaScript mapping differs"
    ):
        _assert_remote_entry_expose_binding(
            wrong_javascript_remote_entry, "./Config", config_asset, config_css_asset
        )


def test_vue_page_uses_host_api_and_edited_manual_values_without_secrets() -> None:
    plugin_root = ROOT / "plugins.v2" / "xhsmovieassistant"
    page = (plugin_root / "src" / "components" / "Page.vue").read_text(
        encoding="utf-8"
    )
    built_assets = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (plugin_root / "dist" / "assets").rglob("*")
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


def test_vue_page_build_preserves_unknown_media_type_semantics() -> None:
    plugin_root = ROOT / "plugins.v2" / "xhsmovieassistant"
    page_source = (plugin_root / "src" / "components" / "Page.vue").read_text(
        encoding="utf-8"
    )
    page_asset = _single_dist_asset(
        plugin_root / "dist" / "assets", "__federation_expose_Page-*.js"
    ).read_text(encoding="utf-8")

    _assert_unknown_media_type_semantics_match(page_source, page_asset)

    stale_page_asset = page_asset.replace(
        "media_type: normalizeText(row.media_type),",
        "media_type: ['movie', 'tv'].includes(row.media_type) ? row.media_type : 'movie',",
        1,
    )
    assert stale_page_asset != page_asset
    with pytest.raises(AssertionError, match="built Page chunk"):
        _assert_unknown_media_type_semantics_match(page_source, stale_page_asset)

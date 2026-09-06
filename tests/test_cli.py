import asyncio
import inspect
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from xhs_probe import cli


@dataclass
class FakePage:
    url: str


@dataclass
class FakeContext:
    pages: list[FakePage]


def test_select_xhs_page_ignores_unrelated_browser_tabs() -> None:
    selector = getattr(cli, "select_xhs_page", None)
    assert selector is not None, "select_xhs_page is not implemented"
    unrelated = FakePage("https://example.com")
    xhs_page = FakePage("https://www.xiaohongshu.com/explore")

    selected = selector([FakeContext([unrelated]), FakeContext([xhs_page])])

    assert selected is xhs_page


def test_select_xhs_page_uses_configured_rednote_host() -> None:
    xhs_login = FakePage("https://www.xiaohongshu.com/login")
    rednote_notifications = FakePage("https://www.rednote.com/notification")

    selected = cli.select_xhs_page(
        [FakeContext([xhs_login, rednote_notifications])],
        notification_url="https://www.rednote.com/notification",
    )

    assert selected is rednote_notifications


class ProbeResponse:
    url = "https://www.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20"

    async def json(self) -> dict[str, Any]:
        return {"success": True, "data": {"message_list": []}}


class ProbePage(FakePage):
    def __init__(
        self,
        url: str = "https://www.xiaohongshu.com/notification",
    ) -> None:
        super().__init__(url)
        self._listeners: list[Callable[[ProbeResponse], Any]] = []
        self.goto_count = 0
        self.reload_count = 0

    def on(self, _: str, listener: Callable[[ProbeResponse], Any]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, _: str, listener: Callable[[ProbeResponse], Any]) -> None:
        self._listeners.remove(listener)

    async def reload(self, **_: Any) -> None:
        self.reload_count += 1
        await self._emit_response()

    async def goto(self, url: str, **_: Any) -> None:
        self.goto_count += 1
        self.url = url
        await self._emit_response()

    async def _emit_response(self) -> None:
        for listener in list(self._listeners):
            result = listener(ProbeResponse())
            if inspect.isawaitable(result):
                await result


class ProbeBrowserType:
    def __init__(self, page: ProbePage) -> None:
        self.browser = type("Browser", (), {"contexts": [FakeContext([page])]})()
        self.endpoint_url: str | None = None
        self.connect_options: dict[str, Any] = {}

    async def connect_over_cdp(self, endpoint_url: str, **options: Any) -> Any:
        self.endpoint_url = endpoint_url
        self.connect_options = options
        return self.browser


def test_run_probe_captures_summary_and_raw_evidence(tmp_path: Path) -> None:
    runner = getattr(cli, "run_probe", None)
    settings_type = getattr(cli, "ProbeSettings", None)
    assert runner is not None, "run_probe is not implemented"
    assert settings_type is not None, "ProbeSettings is not implemented"
    browser_type = ProbeBrowserType(ProbePage())
    settings = settings_type(
        cdp_url="http://127.0.0.1:9222",
        output_dir=tmp_path,
        timeout_seconds=1,
    )

    result = asyncio.run(
        runner(
            browser_type,
            settings,
            captured_at=datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc),
        )
    )

    assert browser_type.endpoint_url == "http://127.0.0.1:9222"
    assert result["status"] == "captured"
    assert result["summary"]["message_count"] == 0
    assert Path(result["capture_path"]).is_file()


def test_run_probe_authenticates_cdp_with_bearer_token(tmp_path: Path) -> None:
    browser_type = ProbeBrowserType(ProbePage())
    settings = cli.ProbeSettings(
        cdp_url="http://cloakbrowser:8080/api/profiles/xhs/cdp",
        cdp_token="phase-1-secret",
        output_dir=tmp_path,
        timeout_seconds=1,
    )

    asyncio.run(cli.run_probe(browser_type, settings))

    assert browser_type.connect_options["headers"] == {
        "Authorization": "Bearer phase-1-secret"
    }


def test_run_probe_omits_authorization_header_without_token(tmp_path: Path) -> None:
    browser_type = ProbeBrowserType(ProbePage())
    settings = cli.ProbeSettings(
        cdp_url="http://127.0.0.1:9222",
        cdp_token=None,
        output_dir=tmp_path,
        timeout_seconds=1,
    )

    asyncio.run(cli.run_probe(browser_type, settings))

    assert "headers" not in browser_type.connect_options


def test_run_probe_uses_configured_rednote_notifications(tmp_path: Path) -> None:
    xhs_login = ProbePage("https://www.xiaohongshu.com/login")
    rednote_notifications = ProbePage("https://www.rednote.com/notification")
    browser_type = ProbeBrowserType(rednote_notifications)
    browser_type.browser.contexts[0].pages.insert(0, xhs_login)
    settings = cli.ProbeSettings(
        cdp_url="http://127.0.0.1:9222",
        output_dir=tmp_path,
        timeout_seconds=1,
        notification_url="https://www.rednote.com/notification",
    )

    asyncio.run(cli.run_probe(browser_type, settings))

    assert rednote_notifications.reload_count == 1
    assert xhs_login.goto_count == 0


def test_parser_reads_notification_url_from_environment(monkeypatch: Any) -> None:
    notification_url = "https://www.rednote.com/notification"
    monkeypatch.setenv("XHS_NOTIFICATION_URL", notification_url)

    args = cli._build_parser().parse_args([])

    assert args.notification_url == notification_url

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


class ProbeResponse:
    url = "https://www.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20"

    async def json(self) -> dict[str, Any]:
        return {"success": True, "data": {"message_list": []}}


class ProbePage(FakePage):
    def __init__(self) -> None:
        super().__init__("https://www.xiaohongshu.com/notification")
        self._listeners: list[Callable[[ProbeResponse], Any]] = []

    def on(self, _: str, listener: Callable[[ProbeResponse], Any]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, _: str, listener: Callable[[ProbeResponse], Any]) -> None:
        self._listeners.remove(listener)

    async def reload(self, **_: Any) -> None:
        for listener in list(self._listeners):
            result = listener(ProbeResponse())
            if inspect.isawaitable(result):
                await result


class ProbeBrowserType:
    def __init__(self, page: ProbePage) -> None:
        self.browser = type("Browser", (), {"contexts": [FakeContext([page])]})()
        self.endpoint_url: str | None = None

    async def connect_over_cdp(self, endpoint_url: str, **_: Any) -> Any:
        self.endpoint_url = endpoint_url
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

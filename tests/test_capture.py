import asyncio
import inspect
from typing import Any, Callable

import pytest

from xhs_probe import capture


class FakeResponse:
    def __init__(self, url: str, payload: dict[str, Any]) -> None:
        self.url = url
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


class FakePage:
    def __init__(self) -> None:
        self.url = "https://www.xiaohongshu.com/notification"
        self._listeners: list[Callable[[FakeResponse], Any]] = []
        self.listener_registered_before_reload = False
        self.reload_count = 0
        self.goto_count = 0

    def on(self, event: str, listener: Callable[[FakeResponse], Any]) -> None:
        assert event == "response"
        self._listeners.append(listener)

    def remove_listener(self, event: str, listener: Callable[[FakeResponse], Any]) -> None:
        assert event == "response"
        self._listeners.remove(listener)

    async def _emit_responses(self) -> None:
        responses = [
            FakeResponse("https://www.xiaohongshu.com/notification", {}),
            FakeResponse(
                "https://www.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20",
                {"success": True, "data": {"message_list": []}},
            ),
        ]
        for response in responses:
            for listener in list(self._listeners):
                result = listener(response)
                if inspect.isawaitable(result):
                    await result

    async def reload(self, **_: Any) -> None:
        self.listener_registered_before_reload = bool(self._listeners)
        self.reload_count += 1
        await self._emit_responses()

    async def goto(self, url: str, **_: Any) -> None:
        self.goto_count += 1
        self.url = url
        await self._emit_responses()


def test_capture_registers_before_reload_and_returns_mentions_payload() -> None:
    page = FakePage()
    capture_once = getattr(capture, "capture_mentions_response", None)
    assert capture_once is not None, "capture_mentions_response is not implemented"

    result = asyncio.run(capture_once(page, timeout_seconds=1))

    assert page.listener_registered_before_reload
    assert page.reload_count == 1
    assert result.url.endswith("/you/mentions?num=20")
    assert result.payload == {"success": True, "data": {"message_list": []}}


def test_capture_navigates_when_current_page_is_not_notifications() -> None:
    page = FakePage()
    page.url = "https://www.xiaohongshu.com/explore"

    asyncio.run(capture.capture_mentions_response(page, timeout_seconds=1))

    assert page.goto_count == 1
    assert page.reload_count == 0
    assert page.url == "https://www.xiaohongshu.com/notification"


def test_capture_navigates_to_configured_rednote_notifications() -> None:
    page = FakePage()
    page.url = "https://www.rednote.com/explore"

    asyncio.run(
        capture.capture_mentions_response(
            page,
            timeout_seconds=1,
            notification_url="https://www.rednote.com/notification",
        )
    )

    assert page.goto_count == 1
    assert page.url == "https://www.rednote.com/notification"


def test_capture_timeout_is_explicit_and_removes_listener() -> None:
    page = FakePage()

    async def emit_nothing() -> None:
        return None

    page._emit_responses = emit_nothing
    error_type = getattr(capture, "MentionsCaptureTimeout", RuntimeError)

    with pytest.raises(error_type, match="mentions API response"):
        asyncio.run(capture.capture_mentions_response(page, timeout_seconds=0.01))

    assert page._listeners == []

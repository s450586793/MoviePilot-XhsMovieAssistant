"""Browser response capture for the Phase 1 probe."""

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from xhs_probe.contracts import is_mentions_api_url


NOTIFICATION_URL = "https://www.xiaohongshu.com/notification"


@dataclass(frozen=True)
class CapturedResponse:
    url: str
    payload: dict[str, Any]


class MentionsCaptureTimeout(RuntimeError):
    """Raised when the browser does not emit the expected API response."""


async def capture_mentions_response(
    page: Any,
    *,
    timeout_seconds: float,
    notification_url: str = NOTIFICATION_URL,
) -> CapturedResponse:
    """Reload the notification page and capture its mentions API response."""
    loop = asyncio.get_running_loop()
    captured: asyncio.Future[CapturedResponse] = loop.create_future()

    async def on_response(response: Any) -> None:
        if captured.done() or not is_mentions_api_url(response.url):
            return
        payload = await response.json()
        if isinstance(payload, dict):
            captured.set_result(CapturedResponse(url=response.url, payload=payload))

    page.on("response", on_response)
    try:
        if urlsplit(page.url).path.rstrip("/") == "/notification":
            await page.reload(wait_until="domcontentloaded")
        else:
            await page.goto(notification_url, wait_until="domcontentloaded")
        try:
            return await asyncio.wait_for(captured, timeout=timeout_seconds)
        except TimeoutError as error:
            raise MentionsCaptureTimeout(
                f"mentions API response was not observed within {timeout_seconds:g} seconds"
            ) from error
    finally:
        page.remove_listener("response", on_response)

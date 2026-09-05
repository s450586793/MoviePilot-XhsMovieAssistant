"""Command-line entry point for the Phase 1 probe."""

import argparse
import asyncio
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from xhs_probe.capture import MentionsCaptureTimeout, capture_mentions_response
from xhs_probe.contracts import MentionsPayloadError, summarize_mentions_payload
from xhs_probe.storage import write_raw_capture


@dataclass(frozen=True)
class ProbeSettings:
    cdp_url: str
    output_dir: Path
    timeout_seconds: float


def select_xhs_page(contexts: Iterable[Any]) -> Any | None:
    """Return the first open Xiaohongshu page from browser contexts."""
    for context in contexts:
        for page in context.pages:
            hostname = urlsplit(page.url).hostname or ""
            if hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com"):
                return page
    return None


async def run_probe(
    browser_type: Any,
    settings: ProbeSettings,
    *,
    captured_at: datetime | None = None,
) -> dict[str, object]:
    """Connect to external Chromium and persist one mentions response."""
    browser = await browser_type.connect_over_cdp(
        settings.cdp_url,
        timeout=10_000,
    )
    if not browser.contexts:
        raise RuntimeError("Chromium has no persistent browser context")

    page = select_xhs_page(browser.contexts)
    if page is None:
        page = await browser.contexts[0].new_page()

    captured = await capture_mentions_response(
        page,
        timeout_seconds=settings.timeout_seconds,
    )
    capture_time = captured_at or datetime.now(timezone.utc)
    capture_path = write_raw_capture(
        output_dir=settings.output_dir,
        response_url=captured.url,
        payload=captured.payload,
        captured_at=capture_time,
    )
    return {
        "status": "captured",
        "capture_path": str(capture_path),
        "summary": summarize_mentions_payload(captured.payload),
    }


def _positive_float(raw_value: str) -> float:
    value = float(raw_value)
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one Xiaohongshu mentions API response from persistent Chromium."
    )
    parser.add_argument(
        "--cdp-url",
        default=os.getenv("XHS_CDP_URL", "http://127.0.0.1:9222"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.getenv("XHS_PROBE_OUTPUT_DIR", "/data/probe")),
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=_positive_float(os.getenv("XHS_PROBE_TIMEOUT_SECONDS", "30")),
    )
    return parser


def _print_result(result: dict[str, object]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = ProbeSettings(
        cdp_url=args.cdp_url,
        output_dir=args.output_dir,
        timeout_seconds=args.timeout,
    )

    async def execute() -> dict[str, object]:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            return await run_probe(playwright.chromium, settings)

    try:
        _print_result(asyncio.run(execute()))
        return 0
    except MentionsCaptureTimeout as error:
        _print_result(
            {
                "status": "timeout",
                "error": str(error),
                "action": "Check the remote browser login, then run the probe again.",
            }
        )
        return 2
    except MentionsPayloadError as error:
        _print_result({"status": "contract_error", "error": str(error)})
        return 3
    except Exception as error:
        _print_result(
            {
                "status": "browser_error",
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

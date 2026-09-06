"""Playwright lifecycle, login, and risk handling for the plugin Profile."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


_SITE_ORIGINS = {
    "xiaohongshu": "https://www.xiaohongshu.com",
    "rednote": "https://www.rednote.com",
}
_QR_SELECTOR = ".login-container .qrcode-img, .qrcode-container img, [class*='qrcode'] img"
_LOGIN_SELECTOR = ".login-btn, .login-container"
_INSTALL_TIMEOUT_SECONDS = 600
_MAX_RESULT_MESSAGE = 8192
_PROFILE_LOCK = threading.Lock()
_LOCK_OWNER = threading.local()
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?:^|[?&])[^=&]*(token|key|secret|password|passwd|authorization|credential)[^=&]*=",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OperationResult:
    """A sanitized browser operation outcome suitable for API or persistence use."""

    success: bool
    code: str | None = None
    message: str = ""
    data: Any = None
    should_pause: bool = False


class BrowserBusyError(RuntimeError):
    """Raised when the current thread tries to re-enter a browser operation."""


class BrowserManager:
    """Own one-operation persistent contexts backed by a shared Profile."""

    def __init__(
        self,
        data_path: str | Path,
        site: str,
        proxy: Mapping[str, Any] | None,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        if site not in _SITE_ORIGINS:
            raise ValueError("unsupported site")
        self.data_path = Path(data_path)
        self.profile_path = self.data_path / "browser"
        self.browser_path = self.data_path / "ms-playwright"
        self.base_url = _SITE_ORIGINS[site]
        self.proxy = proxy
        self._playwright_factory = playwright_factory or _default_playwright
        self._active_context: Any = None
        self._configure_browser_path()

    @property
    def executable_path(self) -> Path | None:
        """Return the installed Chromium executable in the plugin-private cache."""
        return _find_chromium_executable(self.browser_path)

    def chromium_status(self) -> OperationResult:
        """Report whether a Chromium executable exists in the private cache."""
        executable = self.executable_path
        if executable is None:
            return OperationResult(
                success=False,
                code="BROWSER_UNAVAILABLE",
                message="Chromium is not installed",
            )
        return OperationResult(success=True, data=executable)

    def install_chromium(self) -> OperationResult:
        """Install Playwright Chromium with fixed arguments and a bounded timeout."""
        with _profile_operation():
            return self._install_chromium()

    def _install_chromium(self) -> OperationResult:
        self.browser_path.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(self.browser_path)
        argv = [sys.executable, "-m", "playwright", "install", "chromium"]
        try:
            completed = subprocess.run(
                argv,
                timeout=_INSTALL_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as error:
            return OperationResult(
                success=False,
                code="TEMPORARY_FAILURE",
                message=_bounded_output("Chromium install timed out", error.stdout, error.stderr),
            )
        except OSError:
            return OperationResult(
                success=False,
                code="BROWSER_UNAVAILABLE",
                message="Chromium installer could not be started",
            )

        message = _bounded_output(completed.stdout, completed.stderr)
        if completed.returncode != 0:
            return OperationResult(
                success=False,
                code="BROWSER_UNAVAILABLE",
                message=message or "Chromium installation failed",
            )
        return OperationResult(success=True, message=message)

    @contextmanager
    def session(self) -> Iterator[Any]:
        """Yield one page while holding the process-wide Profile operation lock."""
        with _profile_operation():
            playwright = None
            context = None
            try:
                self.profile_path.mkdir(parents=True, exist_ok=True)
                self.browser_path.mkdir(parents=True, exist_ok=True)
                self._configure_browser_path()
                playwright = self._playwright_factory()
                chromium = playwright.chromium
                executable = self.executable_path
                if executable is None:
                    executable = Path(chromium.executable_path)
                context = chromium.launch_persistent_context(
                    user_data_dir=self.profile_path,
                    executable_path=executable,
                    headless=True,
                    proxy=self.proxy,
                    viewport={"width": 1280, "height": 900},
                    locale="zh-CN",
                )
                self._active_context = context
                page = context.pages[0] if context.pages else context.new_page()
                yield page
            finally:
                try:
                    if context is not None:
                        context.close()
                finally:
                    self._active_context = None
                    if playwright is not None:
                        playwright.stop()

    def capture_login_qrcode(self) -> OperationResult:
        """Open the configured site and return the visible login QR as PNG bytes."""
        try:
            with self.session() as page:
                page.goto(self.base_url, wait_until="domcontentloaded")
                risk = self.detect_risk(page)
                if not risk.success:
                    return risk
                locator = page.locator(_QR_SELECTOR).first
                locator.wait_for(state="visible", timeout=10_000)
                png = locator.screenshot(type="png")
                if not isinstance(png, bytes):
                    return OperationResult(
                        success=False,
                        code="UPSTREAM_ERROR",
                        message="QR code image was invalid",
                    )
                return OperationResult(success=True, data=png)
        except Exception as error:
            if _is_timeout(error):
                return OperationResult(
                    success=False,
                    code="LOGIN_REQUIRED",
                    message="Login QR code was not found",
                )
            if isinstance(error, BrowserBusyError):
                raise
            return _browser_failure()

    def check_login(self) -> OperationResult:
        """Check initial state first and use login DOM as a bounded fallback."""
        try:
            return self._check_login()
        except BrowserBusyError:
            raise
        except Exception:
            return _browser_failure()

    def _check_login(self) -> OperationResult:
        with self.session() as page:
            page.goto(self.base_url, wait_until="domcontentloaded")
            risk = self.detect_risk(page)
            if not risk.success:
                return risk
            try:
                logged_in = page.evaluate(
                    "() => window.__INITIAL_STATE__?.user?.loggedIn ?? null"
                )
            except Exception:
                logged_in = None
            if logged_in is True:
                return OperationResult(success=True)
            try:
                login_locator = page.locator(_LOGIN_SELECTOR)
                login_visible = login_locator.first.is_visible(timeout=3_000)
            except Exception:
                return OperationResult(
                    success=False,
                    code="TEMPORARY_FAILURE",
                    message="Login status could not be determined",
                )
            if logged_in is False or login_visible:
                return OperationResult(
                    success=False,
                    code="LOGIN_REQUIRED",
                    message="Login is required",
                    should_pause=True,
                )
            return OperationResult(success=True)

    def logout(self) -> OperationResult:
        """Clear cookies and web storage from the persistent Profile."""
        try:
            with self.session() as page:
                page.goto(self.base_url, wait_until="domcontentloaded")
                self._active_context.clear_cookies()
                page.evaluate("window.localStorage.clear(); window.sessionStorage.clear();")
            return OperationResult(success=True)
        except BrowserBusyError:
            raise
        except Exception:
            return _browser_failure()

    def detect_risk(self, page: Any) -> OperationResult:
        """Classify login and anti-abuse pages into stable persistence-safe codes."""
        try:
            text = str(page.locator("body").inner_text(timeout=3_000) or "")
        except Exception:
            return OperationResult(
                success=False,
                code="TEMPORARY_FAILURE",
                message="Page risk state could not be determined",
            )

        status = _page_status(page)
        normalized = text.casefold()
        if status == 429 or re.search(r"\b429\b", normalized) or "too many requests" in normalized:
            return _pause_result("RATE_LIMITED", "Request rate was limited")
        if status == 403 or re.search(r"\b403\b", normalized) or "forbidden" in normalized:
            return _pause_result("AUTH_REQUIRED", "Access was forbidden")
        if "登录状态已过期" in text or "session expired" in normalized:
            return _pause_result("SESSION_EXPIRED", "Session expired")
        risk_markers = (
            "300012",
            "访问存在异常",
            "人机验证",
            "请完成验证",
            "验证码",
            "captcha",
            "security verification",
        )
        if any(marker in normalized for marker in risk_markers):
            return _pause_result("XHS_RISK_CONTROL", "Risk control verification is required")
        return OperationResult(success=True)

    def _configure_browser_path(self) -> None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(self.browser_path)


def _default_playwright() -> Any:
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


@contextmanager
def _profile_operation() -> Iterator[None]:
    if getattr(_LOCK_OWNER, "active", False):
        raise BrowserBusyError("browser operation is already active on this thread")
    _PROFILE_LOCK.acquire()
    _LOCK_OWNER.active = True
    try:
        yield
    finally:
        _LOCK_OWNER.active = False
        _PROFILE_LOCK.release()


def _find_chromium_executable(browser_path: Path) -> Path | None:
    patterns = (
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-linux64/chrome",
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-win/chrome.exe",
    )
    for pattern in patterns:
        for candidate in sorted(browser_path.glob(pattern), reverse=True):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _pause_result(code: str, message: str) -> OperationResult:
    return OperationResult(success=False, code=code, message=message, should_pause=True)


def _browser_failure() -> OperationResult:
    return OperationResult(
        success=False,
        code="BROWSER_UNAVAILABLE",
        message="Browser operation failed",
    )


def _page_status(page: Any) -> int | None:
    for name in ("response_status", "last_response_status", "status"):
        value = getattr(page, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _is_timeout(error: Exception) -> bool:
    return isinstance(error, TimeoutError) or error.__class__.__name__ == "TimeoutError"


def _bounded_output(*parts: object) -> str:
    text_parts = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, bytes):
            part = part.decode("utf-8", errors="replace")
        rendered = str(part).strip()
        if rendered:
            text_parts.append(rendered)
    sanitized = _sanitize_urls("\n".join(text_parts))
    return sanitized[:_MAX_RESULT_MESSAGE]


def _sanitize_urls(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        url = match.group(0)
        authority = url.split("/", 3)[2]
        if "@" in authority or _SENSITIVE_QUERY_PATTERN.search(url):
            return "[REDACTED_URL]"
        return url

    return _URL_PATTERN.sub(replace, text)

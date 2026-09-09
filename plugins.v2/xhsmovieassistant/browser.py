"""Playwright lifecycle, imported login state, and risk handling."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit


_SITE_ORIGINS = {
    "xiaohongshu": "https://www.xiaohongshu.com",
    "rednote": "https://www.rednote.com",
}
_LOGIN_SELECTOR = ".login-btn:visible, .login-container:visible"
_AUTHENTICATED_PROFILE_SELECTOR = (
    'a[title="我"][href^="/user/profile/"]:visible'
)
_INSTALL_TIMEOUT_SECONDS = 600
_MAX_RESULT_MESSAGE = 8192
_PROFILE_LOCK = threading.Lock()
_LOCK_OWNER = threading.local()
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?:^|[_-])"
    r"(?:cookie|token|key|secret|password|passwd|authorization|credential)"
    r"(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?:^|[\s{,;])[\"']?(?:[A-Z0-9]+[_-])*"
    r"(?:cookie|token|key|secret|password|passwd|authorization|credential)"
    r"[\"']?\s*[:=]",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
_USERINFO_URL_PATTERN = re.compile(r"https?://[^\s/]*@", re.IGNORECASE)
_COOKIE_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_MAX_CREDENTIAL_BYTES = 1_048_576


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


class BrowserUnavailableError(RuntimeError):
    """Raised when the plugin's private Chromium process cannot be started."""


class SessionStore:
    """Persist one site's normalized Playwright Storage State privately."""

    def __init__(self, data_path: str | Path, base_url: str) -> None:
        self.data_path = Path(data_path)
        self.path = self.data_path / "xhs-storage-state.json"
        hostname = urlsplit(base_url).hostname
        if not hostname:
            raise ValueError("invalid site origin")
        self.cookie_domain = "." + hostname.removeprefix("www.")

    def import_cookie_header(self, cookie_header: str) -> OperationResult:
        """Convert a browser Cookie header into Playwright Storage State."""
        if not isinstance(cookie_header, str):
            return _invalid_credentials()
        try:
            encoded = cookie_header.encode("utf-8")
        except UnicodeEncodeError:
            return _invalid_credentials()
        if (
            not encoded
            or len(encoded) > _MAX_CREDENTIAL_BYTES
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in cookie_header
            )
        ):
            return _invalid_credentials()
        cookie_header = re.sub(
            r"^\s*cookie\s*:\s*",
            "",
            cookie_header,
            count=1,
            flags=re.IGNORECASE,
        )

        cookies: list[dict[str, Any]] = []
        for item in cookie_header.split(";"):
            item = item.strip()
            if not item:
                continue
            name, separator, value = item.partition("=")
            name = name.strip()
            if not separator or not _COOKIE_NAME_PATTERN.fullmatch(name):
                return _invalid_credentials()
            cookies.append(
                {
                    "name": name,
                    "value": value.strip(),
                    "domain": self.cookie_domain,
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
        if not cookies:
            return _invalid_credentials()

        self._write({"cookies": cookies, "origins": []})
        return OperationResult(
            success=True,
            data={"credential_type": "cookie", "cookie_count": len(cookies)},
        )

    def status(self) -> str:
        """Return whether a private Storage State file is available."""
        return "PRESENT" if self.path.is_file() else "MISSING"

    def clear(self) -> None:
        """Remove the imported login state without exposing its contents."""
        self.path.unlink(missing_ok=True)

    def import_storage_state(self, state: Mapping[str, Any]) -> OperationResult:
        """Filter a Playwright Storage State document to the configured site."""
        if not isinstance(state, Mapping):
            return _invalid_credentials()
        try:
            encoded = json.dumps(state, ensure_ascii=True).encode("utf-8")
        except (TypeError, ValueError):
            return _invalid_credentials()
        if len(encoded) > _MAX_CREDENTIAL_BYTES:
            return _invalid_credentials()

        raw_cookies = state.get("cookies", [])
        raw_origins = state.get("origins", [])
        if not isinstance(raw_cookies, list) or not isinstance(raw_origins, list):
            return _invalid_credentials()
        cookies: list[dict[str, Any]] = []
        for cookie in raw_cookies:
            if not isinstance(cookie, Mapping):
                return _invalid_credentials()
            if not self._allows_hostname(str(cookie.get("domain") or "")):
                continue
            normalized = _normalize_storage_cookie(cookie)
            if normalized is None:
                return _invalid_credentials()
            cookies.append(normalized)
        origins: list[dict[str, Any]] = []
        for origin in raw_origins:
            if not isinstance(origin, Mapping):
                return _invalid_credentials()
            if not self._allows_origin(str(origin.get("origin") or "")):
                continue
            normalized_origin = _normalize_storage_origin(origin)
            if normalized_origin is None:
                return _invalid_credentials()
            origins.append(normalized_origin)
        if not cookies:
            return _invalid_credentials()

        self._write({"cookies": cookies, "origins": origins})
        return OperationResult(
            success=True,
            data={"credential_type": "storage_state", "cookie_count": len(cookies)},
        )

    def _allows_hostname(self, value: str) -> bool:
        hostname = value.strip().casefold().lstrip(".")
        root = self.cookie_domain.lstrip(".").casefold()
        return bool(hostname == root or hostname.endswith(f".{root}"))

    def _allows_origin(self, value: str) -> bool:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        return bool(
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and self._allows_hostname(parsed.hostname or "")
        )

    def _write(self, state: Mapping[str, Any]) -> None:
        self.data_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=True, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise


class BrowserManager:
    """Own isolated Chromium contexts backed by imported Storage State."""

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
        self.browser_path = self.data_path / "ms-playwright"
        self.base_url = _SITE_ORIGINS[site]
        self.session_store = SessionStore(self.data_path, self.base_url)
        self.proxy = proxy
        self._playwright_factory = playwright_factory or _default_playwright
        self._active_context: Any = None

    @property
    def executable_path(self) -> Path | None:
        """Return the installed Chromium executable in the plugin-private cache."""
        return _find_chromium_executable(self.browser_path)

    def close_active_context(self) -> None:
        """Interrupt only a browser context owned by this plugin instance."""
        context = self._active_context
        if context is not None:
            self._active_context = None
            context.close()

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
        proxy_url = _proxy_environment_url(self.proxy)
        if proxy_url:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[name] = proxy_url
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
        """Yield one page loaded from the private Storage State file."""
        with _profile_operation():
            playwright = None
            browser = None
            context = None
            persist_session = self.session_store.status() == "PRESENT"
            try:
                self.browser_path.mkdir(parents=True, exist_ok=True)
                executable = self.executable_path
                if executable is None:
                    raise BrowserUnavailableError("Chromium is not installed")
                try:
                    playwright = self._playwright_factory()
                except Exception as error:
                    raise BrowserUnavailableError(
                        "Playwright could not be started"
                    ) from error
                chromium = playwright.chromium
                try:
                    browser = chromium.launch(
                        executable_path=executable,
                        headless=True,
                        proxy=self.proxy,
                    )
                except Exception as error:
                    raise BrowserUnavailableError(
                        "Chromium could not be started"
                    ) from error
                context_options: dict[str, Any] = {
                    "viewport": {"width": 1280, "height": 900},
                    "locale": "zh-CN",
                }
                if persist_session:
                    context_options["storage_state"] = self.session_store.path
                context = browser.new_context(**context_options)
                self._active_context = context
                yield context.new_page()
            finally:
                try:
                    if context is not None:
                        try:
                            if persist_session and self._active_context is context:
                                self.session_store.import_storage_state(
                                    context.storage_state()
                                )
                        finally:
                            context.close()
                finally:
                    self._active_context = None
                    try:
                        if browser is not None:
                            browser.close()
                    finally:
                        if playwright is not None:
                            playwright.stop()

    def check_login(self) -> OperationResult:
        """Check initial state first and use login DOM as a bounded fallback."""
        try:
            return self._check_login()
        except BrowserBusyError:
            raise
        except BrowserUnavailableError:
            return _browser_failure()
        except Exception:
            return OperationResult(
                success=False,
                code="TEMPORARY_FAILURE",
                message="Browser operation temporarily failed",
            )

    def import_credentials(
        self,
        *,
        cookie_header: str | None = None,
        storage_state: Mapping[str, Any] | None = None,
    ) -> OperationResult:
        """Import one credential format and immediately validate it on the site."""
        if (cookie_header is None) == (storage_state is None):
            return _invalid_credentials()
        imported = (
            self.session_store.import_cookie_header(cookie_header)
            if cookie_header is not None
            else self.session_store.import_storage_state(storage_state)
        )
        if not imported.success:
            return imported
        validated = self.check_login()
        if not validated.success:
            return validated
        return OperationResult(success=True, data=imported.data)

    def _check_login(self) -> OperationResult:
        with self.session() as page:
            response = page.goto(self.base_url, wait_until="domcontentloaded")
            risk = self.detect_risk(page, _response_status(response))
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
            if logged_in is False:
                return OperationResult(
                    success=False,
                    code="LOGIN_REQUIRED",
                    message="Login is required",
                    should_pause=True,
                )
            try:
                page.locator(
                    _AUTHENTICATED_PROFILE_SELECTOR
                ).first.wait_for(state="visible", timeout=3_000)
            except Exception:
                profile_visible = False
            else:
                profile_visible = True
            if profile_visible:
                return OperationResult(success=True)
            login = self.detect_login_page(page)
            if not login.success:
                return login
            return OperationResult(
                success=False,
                code="TEMPORARY_FAILURE",
                message="Login status could not be determined",
            )

    def logout(self) -> OperationResult:
        """Clear cookies, web storage, and the imported Storage State file."""
        try:
            with self.session() as page:
                response = page.goto(self.base_url, wait_until="domcontentloaded")
                risk = self.detect_risk(page, _response_status(response))
                self._active_context.clear_cookies()
                page.evaluate("window.localStorage.clear(); window.sessionStorage.clear();")
        except BrowserBusyError:
            raise
        except Exception:
            self.session_store.clear()
            return _browser_failure()
        self.session_store.clear()
        return risk if not risk.success else OperationResult(success=True)

    def detect_risk(
        self, page: Any, response_status: int | None = None
    ) -> OperationResult:
        """Classify anti-abuse pages into stable persistence-safe codes."""
        if response_status == 429:
            return _pause_result("RATE_LIMITED", "Request rate was limited")
        if response_status == 403:
            return _pause_result("AUTH_REQUIRED", "Access was forbidden")
        page_path = urlsplit(str(getattr(page, "url", "") or "")).path.casefold()
        if page_path.rstrip("/") == "/website-login/error":
            return _pause_result(
                "XHS_RISK_CONTROL", "Risk control verification is required"
            )
        try:
            text = page.evaluate("() => document.body?.innerText ?? ''")
        except Exception:
            return OperationResult(
                success=False,
                code="TEMPORARY_FAILURE",
                message="Page risk state could not be determined",
            )
        if not isinstance(text, str):
            return OperationResult(
                success=False,
                code="TEMPORARY_FAILURE",
                message="Page risk state could not be determined",
            )

        normalized = text.casefold()
        if "too many requests" in normalized:
            return _pause_result("RATE_LIMITED", "Request rate was limited")
        forbidden_markers = ("403 forbidden", "access forbidden", "access denied")
        if any(marker in normalized for marker in forbidden_markers):
            return _pause_result("AUTH_REQUIRED", "Access was forbidden")
        if "登录状态已过期" in text or "session expired" in normalized:
            return _pause_result("SESSION_EXPIRED", "Session expired")
        risk_markers = (
            "访问存在异常",
            "人机验证",
            "请完成验证",
            "captcha",
            "security verification",
        )
        structured_risk_code = re.search(
            r"(?:error_code|code)\s*[:=]\s*[\"']?300012\b", normalized
        )
        if structured_risk_code or any(marker in normalized for marker in risk_markers):
            return _pause_result("XHS_RISK_CONTROL", "Risk control verification is required")
        return OperationResult(success=True)

    def detect_login_page(self, page: Any) -> OperationResult:
        """Classify a visible login surface without relying on page copy."""
        try:
            login_visible = page.locator(_LOGIN_SELECTOR).first.is_visible(timeout=3_000)
        except Exception:
            return OperationResult(
                success=False,
                code="TEMPORARY_FAILURE",
                message="Login status could not be determined",
            )
        if login_visible:
            return _pause_result("LOGIN_REQUIRED", "Login is required")
        return OperationResult(success=True)


def _default_playwright() -> Any:
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def _proxy_environment_url(proxy: Mapping[str, Any] | None) -> str | None:
    """Convert Playwright proxy settings into a downloader proxy URL."""
    if not isinstance(proxy, Mapping):
        return None
    server = proxy.get("server")
    if not isinstance(server, str) or not server.strip():
        return None
    server = server.strip()
    username = proxy.get("username")
    if not isinstance(username, str) or not username:
        return server

    candidate = server if "://" in server else f"http://{server}"
    parsed = urlsplit(candidate)
    if not parsed.hostname:
        return server
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    credentials = quote(username, safe="")
    password = proxy.get("password")
    if isinstance(password, str):
        credentials = f"{credentials}:{quote(password, safe='')}"
    return urlunsplit(
        (
            parsed.scheme or "http",
            f"{credentials}@{host}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


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


def _invalid_credentials() -> OperationResult:
    return OperationResult(
        success=False,
        code="INVALID_CREDENTIALS",
        message="Login credentials are invalid",
    )


def _normalize_storage_cookie(cookie: Mapping[str, Any]) -> dict[str, Any] | None:
    name = cookie.get("name")
    value = cookie.get("value")
    domain = cookie.get("domain")
    path = cookie.get("path")
    expires = cookie.get("expires")
    http_only = cookie.get("httpOnly")
    secure = cookie.get("secure")
    same_site = cookie.get("sameSite")
    if (
        not isinstance(name, str)
        or not _COOKIE_NAME_PATTERN.fullmatch(name)
        or not isinstance(value, str)
        or not isinstance(domain, str)
        or not isinstance(path, str)
        or not path.startswith("/")
        or isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not isinstance(http_only, bool)
        or not isinstance(secure, bool)
        or same_site not in {"Strict", "Lax", "None"}
    ):
        return None
    normalized = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "expires": expires,
        "httpOnly": http_only,
        "secure": secure,
        "sameSite": same_site,
    }
    partition_key = cookie.get("partitionKey")
    if partition_key is not None:
        if not isinstance(partition_key, str):
            return None
        normalized["partitionKey"] = partition_key
    return normalized


def _normalize_storage_origin(origin: Mapping[str, Any]) -> dict[str, Any] | None:
    value = origin.get("origin")
    local_storage = origin.get("localStorage", [])
    if not isinstance(value, str) or not isinstance(local_storage, list):
        return None
    normalized_items: list[dict[str, str]] = []
    for item in local_storage:
        if not isinstance(item, Mapping):
            return None
        name = item.get("name")
        stored_value = item.get("value")
        if not isinstance(name, str) or not isinstance(stored_value, str):
            return None
        normalized_items.append({"name": name, "value": stored_value})
    return {"origin": value, "localStorage": normalized_items}


def _browser_failure() -> OperationResult:
    return OperationResult(
        success=False,
        code="BROWSER_UNAVAILABLE",
        message="Browser operation failed",
    )


def _response_status(response: Any) -> int | None:
    value = getattr(response, "status", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


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
    return "\n".join(_sanitize_line(line) for line in text.splitlines())


def _sanitize_line(line: str) -> str:
    value = line
    for _ in range(3):
        if (
            _SENSITIVE_TEXT_PATTERN.search(value)
            or _has_sensitive_query(value)
            or _BEARER_PATTERN.search(value)
            or _JWT_PATTERN.search(value)
            or _USERINFO_URL_PATTERN.search(value)
        ):
            return "[REDACTED]"
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded

    def replace(match: re.Match[str]) -> str:
        url = match.group(0)
        authority = url.split("/", 3)[2]
        if "@" in authority or _has_sensitive_query(url):
            return "[REDACTED_URL]"
        return url

    return _URL_PATTERN.sub(replace, line)


def _has_sensitive_query(value: str) -> bool:
    for match in _URL_PATTERN.finditer(value):
        try:
            query = urlsplit(match.group(0)).query
        except ValueError:
            return True
        if any(
            _SENSITIVE_QUERY_PATTERN.search(name)
            for name, _ in parse_qsl(query, keep_blank_values=True)
        ):
            return True
    return False

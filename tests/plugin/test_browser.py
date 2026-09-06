import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from xhsmovieassistant.browser import BrowserBusyError, BrowserManager, OperationResult


class FakeLocator:
    def __init__(self, page, selector: str):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def count(self) -> int:
        return int(
            self.selector in self.page.visible_selectors
            or self.selector in self.page.present_selectors
        )

    def is_visible(self, **kwargs) -> bool:
        return self.selector in self.page.visible_selectors

    def wait_for(self, **kwargs) -> None:
        self.page.wait_args = kwargs
        if self.selector not in self.page.visible_selectors:
            raise TimeoutError("locator was not visible")

    def screenshot(self, **kwargs) -> bytes:
        self.page.screenshot_args = kwargs
        return self.page.qr_png

    def inner_text(self, **kwargs) -> str:
        return self.page.text


class FakePage:
    def __init__(self):
        self.initial_logged_in = None
        self.visible_selectors: set[str] = set()
        self.present_selectors: set[str] = set()
        self.text = ""
        self.qr_png = b"\x89PNG\r\n\x1a\nqr"
        self.goto_args = None
        self.wait_args = None
        self.screenshot_args = None
        self.evaluate_calls = []

    def goto(self, *args, **kwargs) -> None:
        self.goto_args = (args, kwargs)

    def evaluate(self, expression: str):
        self.evaluate_calls.append(expression)
        if "__INITIAL_STATE__" in expression:
            return self.initial_logged_in
        return None

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


class FakeContext:
    def __init__(self, runtime):
        self.runtime = runtime
        self.pages = [runtime.page]

    def close(self) -> None:
        self.runtime.context_closed = True

    def clear_cookies(self) -> None:
        self.runtime.cookies_cleared = True


class FakeChromium:
    def __init__(self, runtime):
        self.runtime = runtime
        self.executable_path = str(runtime.executable_path)

    def launch_persistent_context(self, **kwargs) -> FakeContext:
        self.runtime.launch_args = kwargs
        self.runtime.context = FakeContext(self.runtime)
        return self.runtime.context


class FakePlaywright:
    def __init__(self, executable_path: Path):
        self.page = FakePage()
        self.executable_path = executable_path
        self.chromium = FakeChromium(self)
        self.launch_args = None
        self.context = None
        self.context_closed = False
        self.cookies_cleared = False
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_playwright(tmp_path) -> FakePlaywright:
    executable = tmp_path / "ms-playwright" / "chromium-1187" / "chrome-linux" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o700)
    return FakePlaywright(executable)


@pytest.fixture
def manager(tmp_path, fake_playwright) -> BrowserManager:
    return BrowserManager(
        tmp_path,
        site="xiaohongshu",
        proxy=None,
        playwright_factory=lambda: fake_playwright,
    )


def test_constructor_rejects_unsupported_site_without_echoing_it(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported site") as error:
        BrowserManager(tmp_path, site="https://user:secret@invalid.example", proxy=None)

    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    ("site", "expected_url"),
    [
        ("xiaohongshu", "https://www.xiaohongshu.com"),
        ("rednote", "https://www.rednote.com"),
    ],
)
def test_supported_site_resolves_to_fixed_origin(tmp_path, site: str, expected_url: str) -> None:
    browser = BrowserManager(tmp_path, site=site, proxy=None, playwright_factory=lambda: None)

    assert browser.base_url == expected_url


def test_session_uses_persistent_profile_proxy_and_expected_options(
    tmp_path, fake_playwright
) -> None:
    proxy = {"server": "http://proxy:7890", "username": "proxy-user", "password": "proxy-pass"}
    browser = BrowserManager(
        tmp_path,
        site="rednote",
        proxy=proxy,
        playwright_factory=lambda: fake_playwright,
    )

    with browser.session() as page:
        assert page is fake_playwright.page

    assert fake_playwright.launch_args == {
        "user_data_dir": tmp_path / "browser",
        "executable_path": fake_playwright.executable_path,
        "headless": True,
        "proxy": proxy,
        "viewport": {"width": 1280, "height": 900},
        "locale": "zh-CN",
    }
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "ms-playwright")
    assert fake_playwright.context_closed is True
    assert fake_playwright.stopped is True


def test_session_closes_context_and_playwright_and_releases_lock_after_exception(
    manager, fake_playwright
) -> None:
    with pytest.raises(RuntimeError, match="operation failed"):
        with manager.session():
            raise RuntimeError("operation failed")

    assert fake_playwright.context_closed is True
    assert fake_playwright.stopped is True
    with manager.session():
        pass


def test_session_is_not_reentrant_on_the_same_thread(manager) -> None:
    with manager.session():
        with pytest.raises(BrowserBusyError, match="already active"):
            with manager.session():
                pass


def test_sessions_are_serialized_between_threads(tmp_path) -> None:
    first_runtime = FakePlaywright(tmp_path / "first-chrome")
    second_runtime = FakePlaywright(tmp_path / "second-chrome")
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    first = BrowserManager(tmp_path / "a", "rednote", None, lambda: first_runtime)
    second = BrowserManager(tmp_path / "b", "rednote", None, lambda: second_runtime)

    def run_first() -> None:
        with first.session():
            first_entered.set()
            assert release_first.wait(2)

    def run_second() -> None:
        assert first_entered.wait(2)
        with second.session():
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(run_first)
        second_future = executor.submit(run_second)
        assert first_entered.wait(2)
        assert second_entered.wait(0.05) is False
        release_first.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert second_entered.is_set()


def test_chromium_status_detects_executable_in_private_cache(manager, fake_playwright) -> None:
    status = manager.chromium_status()

    assert status == OperationResult(success=True, data=fake_playwright.executable_path)


def test_chromium_status_reports_missing_executable(tmp_path) -> None:
    browser = BrowserManager(tmp_path, "rednote", None, lambda: None)

    status = browser.chromium_status()

    assert status.success is False
    assert status.code == "BROWSER_UNAVAILABLE"
    assert status.should_pause is False


def test_chromium_status_rejects_non_executable_file(tmp_path) -> None:
    executable = tmp_path / "ms-playwright" / "chromium-1187" / "chrome-linux" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.touch(mode=0o600)

    status = BrowserManager(tmp_path, "rednote", None, lambda: None).chromium_status()

    assert status.success is False
    assert status.code == "BROWSER_UNAVAILABLE"


def test_install_chromium_uses_only_fixed_argv_and_private_cache(
    tmp_path, monkeypatch
) -> None:
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="installed", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    browser = BrowserManager(tmp_path, "rednote", None, lambda: None)

    result = browser.install_chromium()

    assert result.success is True
    assert observed["argv"] == [sys.executable, "-m", "playwright", "install", "chromium"]
    assert observed["kwargs"]["timeout"] == 600
    assert observed["kwargs"]["capture_output"] is True
    assert observed["kwargs"]["text"] is True
    assert observed["kwargs"]["check"] is False
    assert observed["kwargs"]["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(
        tmp_path / "ms-playwright"
    )
    assert "shell" not in observed["kwargs"]


def test_install_chromium_cannot_reenter_an_active_browser_operation(
    manager, monkeypatch
) -> None:
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(subprocess, "run", fake_run)

    with manager.session():
        with pytest.raises(BrowserBusyError, match="already active"):
            manager.install_chromium()

    assert called is False


def test_install_chromium_failure_bounds_and_redacts_output(tmp_path, monkeypatch) -> None:
    credential_url = "https://alice:secret@downloads.example/chromium"
    token_url = "https://downloads.example/chromium?xsec_token=secret-token"

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout=credential_url + "\n" + token_url + "\n" + "x" * 10_000,
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.success is False
    assert result.code == "BROWSER_UNAVAILABLE"
    assert len(result.message) <= 8192
    assert "alice" not in result.message
    assert "secret" not in result.message


def test_install_chromium_timeout_is_sanitized(tmp_path, monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            argv,
            kwargs["timeout"],
            output=b"x" * 10_000 + b"https://user:password@example.test/a",
            stderr=b"download stalled",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.success is False
    assert result.code == "TEMPORARY_FAILURE"
    assert "password" not in result.message
    assert "timed out" in result.message
    assert len(result.message) <= 8192


@pytest.mark.parametrize("error", [FileNotFoundError("missing"), OSError("cannot spawn")])
def test_install_chromium_spawn_failure_returns_stable_code(tmp_path, monkeypatch, error) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(error))

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.success is False
    assert result.code == "BROWSER_UNAVAILABLE"


def test_check_login_prefers_initial_state(manager, fake_playwright) -> None:
    fake_playwright.page.initial_logged_in = True
    fake_playwright.page.visible_selectors.add(".login-btn, .login-container")

    result = manager.check_login()

    assert result.success is True
    assert fake_playwright.page.goto_args[0] == ("https://www.xiaohongshu.com",)


@pytest.mark.parametrize("state", [False, None])
def test_check_login_uses_dom_fallback_for_logged_out_page(
    manager, fake_playwright, state
) -> None:
    fake_playwright.page.initial_logged_in = state
    fake_playwright.page.visible_selectors.add(".login-btn, .login-container")

    result = manager.check_login()

    assert result.success is False
    assert result.code == "LOGIN_REQUIRED"
    assert result.should_pause is True


def test_check_login_treats_missing_state_and_login_dom_as_authenticated(
    manager, fake_playwright
) -> None:
    fake_playwright.page.initial_logged_in = None

    assert manager.check_login().success is True


def test_check_login_ignores_hidden_login_dom(manager, fake_playwright) -> None:
    fake_playwright.page.initial_logged_in = None
    fake_playwright.page.present_selectors.add(".login-btn, .login-container")

    assert manager.check_login().success is True


def test_capture_login_qrcode_returns_png_bytes_without_writing_file(
    manager, fake_playwright, tmp_path
) -> None:
    selector = ".login-container .qrcode-img, .qrcode-container img, [class*='qrcode'] img"
    fake_playwright.page.visible_selectors.add(selector)

    result = manager.capture_login_qrcode()

    assert result.success is True
    assert result.data == fake_playwright.page.qr_png
    assert fake_playwright.page.screenshot_args == {"type": "png"}
    assert list(tmp_path.rglob("*.png")) == []


def test_capture_login_qrcode_reports_missing_locator(manager) -> None:
    result = manager.capture_login_qrcode()

    assert result.success is False
    assert result.code == "LOGIN_REQUIRED"
    assert result.data is None


@pytest.mark.parametrize("operation", ["capture_login_qrcode", "check_login", "logout"])
def test_browser_actions_sanitize_launch_failures(tmp_path, operation: str) -> None:
    def fail_factory():
        raise RuntimeError("failed at https://user:password@example.test/profile")

    browser = BrowserManager(tmp_path, "rednote", None, fail_factory)

    result = getattr(browser, operation)()

    assert result.success is False
    assert result.code == "BROWSER_UNAVAILABLE"
    assert "password" not in result.message


def test_session_releases_lock_when_playwright_factory_fails(tmp_path, fake_playwright) -> None:
    failing = BrowserManager(
        tmp_path / "failed",
        "rednote",
        None,
        lambda: (_ for _ in ()).throw(RuntimeError("launch failed")),
    )
    working = BrowserManager(tmp_path / "working", "rednote", None, lambda: fake_playwright)

    with pytest.raises(RuntimeError, match="launch failed"):
        with failing.session():
            pass
    with working.session():
        pass

    assert fake_playwright.context_closed is True


def test_logout_clears_only_persistent_context_storage(manager, fake_playwright) -> None:
    result = manager.logout()

    assert result.success is True
    assert fake_playwright.cookies_cleared is True
    assert fake_playwright.page.evaluate_calls == [
        "window.localStorage.clear(); window.sessionStorage.clear();"
    ]
    assert fake_playwright.context_closed is True


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("当前访问存在异常，请完成验证 error_code=300012", "XHS_RISK_CONTROL"),
        ("请完成验证码或人机验证", "XHS_RISK_CONTROL"),
        ("403 Forbidden", "AUTH_REQUIRED"),
        ("429 Too Many Requests", "RATE_LIMITED"),
        ("登录状态已过期", "SESSION_EXPIRED"),
    ],
)
def test_detect_risk_returns_stable_pause_code(manager, fake_playwright, text, code) -> None:
    fake_playwright.page.text = text

    risk = manager.detect_risk(fake_playwright.page)

    assert risk.success is False
    assert risk.code == code
    assert risk.should_pause is True


def test_detect_risk_returns_clear_result_for_normal_page(manager, fake_playwright) -> None:
    fake_playwright.page.text = "评论和 @"

    assert manager.detect_risk(fake_playwright.page) == OperationResult(success=True)

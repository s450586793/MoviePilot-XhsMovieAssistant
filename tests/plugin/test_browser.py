import os
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from xhsmovieassistant.browser import (
    BrowserBusyError,
    BrowserManager,
    BrowserUnavailableError,
    OperationResult,
)


class FakeLocator:
    def __init__(self, page, selector: str):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        matches = self._matches()
        return FakeLocator(self.page, matches[0]) if matches else self

    def count(self) -> int:
        return len(self._matches())

    def is_visible(self, **kwargs) -> bool:
        matches = self._matches()
        return bool(matches) and matches[0] in self.page.visible_selectors

    def wait_for(self, **kwargs) -> None:
        self.page.wait_args = kwargs
        for selector in self.selector.split(","):
            candidate = selector.strip().removesuffix(":visible")
            if candidate in self.page.delayed_visible_selectors:
                self.page.visible_selectors.add(candidate)
        if not self.is_visible():
            raise TimeoutError("locator was not visible")

    def inner_text(self, **kwargs) -> str:
        if self.selector == "body" and self.page.body_error is not None:
            raise self.page.body_error
        return self.page.text

    def _matches(self) -> list[str]:
        matches = []
        for selector in self.selector.split(","):
            selector = selector.strip()
            visible_only = selector.endswith(":visible")
            candidate = selector.removesuffix(":visible")
            present = (
                candidate in self.page.visible_selectors
                or candidate in self.page.present_selectors
            )
            if present and (not visible_only or candidate in self.page.visible_selectors):
                matches.append(candidate)
        return matches


class FakeResponse:
    def __init__(self, status: int):
        self.status = status


class FakePage:
    def __init__(self):
        self.url = "https://www.xiaohongshu.com"
        self.initial_logged_in = None
        self.visible_selectors: set[str] = set()
        self.present_selectors: set[str] = set()
        self.delayed_visible_selectors: set[str] = set()
        self.text = ""
        self.body_error = None
        self.goto_status = 200
        self.goto_args = None
        self.wait_args = None
        self.evaluate_calls = []

    def goto(self, *args, **kwargs) -> FakeResponse:
        self.goto_args = (args, kwargs)
        return FakeResponse(self.goto_status)

    def evaluate(self, expression: str):
        self.evaluate_calls.append(expression)
        if "__INITIAL_STATE__" in expression:
            return self.initial_logged_in
        if "document.body?.innerText" in expression:
            return self.text
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

    def new_page(self):
        return self.runtime.page

    def storage_state(self):
        return self.runtime.context_storage_state


class FakeBrowser:
    def __init__(self, runtime=None, contexts=None):
        self.runtime = runtime
        self.contexts = contexts or []

    def new_context(self, **kwargs) -> FakeContext:
        self.runtime.context_args = kwargs
        self.runtime.context = FakeContext(self.runtime)
        return self.runtime.context

    def close(self) -> None:
        self.runtime.browser_closed = True


class FakeChromium:
    def __init__(self, runtime):
        self.runtime = runtime
        self.executable_path = str(runtime.executable_path)

    def launch(self, **kwargs) -> FakeBrowser:
        self.runtime.browser_launch_args = kwargs
        return FakeBrowser(self.runtime)

class FakePlaywright:
    def __init__(self, executable_path: Path):
        self.page = FakePage()
        self.executable_path = executable_path
        self.chromium = FakeChromium(self)
        self.context = None
        self.browser_launch_args = None
        self.context_args = None
        self.context_storage_state = {"cookies": [], "origins": []}
        self.browser_closed = False
        self.context_closed = False
        self.cookies_cleared = False
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_playwright(tmp_path) -> FakePlaywright:
    executable = _make_executable(tmp_path)
    return FakePlaywright(executable)


def _make_executable(data_path: Path) -> Path:
    executable = data_path / "ms-playwright" / "chromium-1187" / "chrome-linux" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o700)
    return executable


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


def test_session_uses_imported_storage_state_in_an_isolated_context(
    tmp_path, fake_playwright, monkeypatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "parent-owned-cache")
    proxy = {"server": "http://proxy:7890", "username": "proxy-user", "password": "proxy-pass"}
    browser = BrowserManager(
        tmp_path,
        site="rednote",
        proxy=proxy,
        playwright_factory=lambda: fake_playwright,
    )
    browser.session_store.import_cookie_header("a1=secret")

    with browser.session() as page:
        assert page is fake_playwright.page

    assert fake_playwright.browser_launch_args == {
        "executable_path": fake_playwright.executable_path,
        "headless": True,
        "proxy": proxy,
    }
    assert fake_playwright.context_args == {
        "storage_state": browser.session_store.path,
        "viewport": {"width": 1280, "height": 900},
        "locale": "zh-CN",
    }
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "parent-owned-cache"
    assert fake_playwright.context_closed is True
    assert fake_playwright.browser_closed is True
    assert fake_playwright.stopped is True


def test_session_refreshes_imported_storage_state_before_closing(
    tmp_path,
    fake_playwright,
) -> None:
    browser = BrowserManager(
        tmp_path,
        site="xiaohongshu",
        proxy=None,
        playwright_factory=lambda: fake_playwright,
    )
    browser.session_store.import_cookie_header("a1=old-secret")
    fake_playwright.context_storage_state = {
        "cookies": [
            {
                "name": "a1",
                "value": "refreshed-secret",
                "domain": ".xiaohongshu.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }

    with browser.session():
        pass

    persisted = json.loads(browser.session_store.path.read_text(encoding="utf-8"))
    assert persisted["cookies"][0]["value"] == "refreshed-secret"
    assert fake_playwright.context_closed is True


def test_session_closes_all_browser_resources_when_state_refresh_fails(
    tmp_path,
    fake_playwright,
    monkeypatch,
) -> None:
    browser = BrowserManager(
        tmp_path,
        site="xiaohongshu",
        proxy=None,
        playwright_factory=lambda: fake_playwright,
    )
    browser.session_store.import_cookie_header("a1=secret")
    monkeypatch.setattr(
        browser.session_store,
        "import_storage_state",
        lambda state: (_ for _ in ()).throw(OSError("write failed")),
    )

    with pytest.raises(OSError, match="write failed"):
        with browser.session():
            pass

    assert fake_playwright.context_closed is True
    assert fake_playwright.browser_closed is True
    assert fake_playwright.stopped is True


def test_session_skips_state_refresh_after_active_context_is_interrupted(
    manager,
    fake_playwright,
) -> None:
    manager.session_store.import_cookie_header("a1=secret")

    with manager.session():
        fake_playwright.context.storage_state = lambda: (_ for _ in ()).throw(
            RuntimeError("context is closed")
        )
        manager.close_active_context()

    assert fake_playwright.context_closed is True
    assert fake_playwright.browser_closed is True
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
    first_data = tmp_path / "a"
    second_data = tmp_path / "b"
    first_runtime = FakePlaywright(_make_executable(first_data))
    second_runtime = FakePlaywright(_make_executable(second_data))
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    first = BrowserManager(first_data, "rednote", None, lambda: first_runtime)
    second = BrowserManager(second_data, "rednote", None, lambda: second_runtime)

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


def test_managers_never_mutate_browser_env_or_share_executables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "parent-owned-cache")
    first_data = tmp_path / "first"
    second_data = tmp_path / "second"
    first_runtime = FakePlaywright(_make_executable(first_data))
    second_runtime = FakePlaywright(_make_executable(second_data))

    first = BrowserManager(first_data, "rednote", None, lambda: first_runtime)
    second = BrowserManager(second_data, "rednote", None, lambda: second_runtime)
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "parent-owned-cache"
    with first.session():
        pass
    with second.session():
        pass

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "parent-owned-cache"
    assert first_runtime.browser_launch_args["executable_path"] == first_runtime.executable_path
    assert second_runtime.browser_launch_args["executable_path"] == second_runtime.executable_path


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
    assert result.message == "installed"
    assert observed["argv"] == [sys.executable, "-m", "playwright", "install", "chromium"]
    assert observed["kwargs"]["timeout"] == 600
    assert observed["kwargs"]["capture_output"] is True
    assert observed["kwargs"]["text"] is True
    assert observed["kwargs"]["check"] is False
    assert observed["kwargs"]["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(
        tmp_path / "ms-playwright"
    )
    assert "shell" not in observed["kwargs"]


def test_install_chromium_uses_configured_proxy_for_download(
    tmp_path, monkeypatch
) -> None:
    observed = {}

    def fake_run(argv, **kwargs):
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="installed", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    browser = BrowserManager(
        tmp_path,
        "rednote",
        {
            "server": "http://proxy.example:7890",
            "username": "user name",
            "password": "p@ss",
        },
        lambda: None,
    )

    result = browser.install_chromium()

    assert result.success is True
    expected = "http://user%20name:p%40ss@proxy.example:7890"
    assert observed["env"]["HTTP_PROXY"] == expected
    assert observed["env"]["HTTPS_PROXY"] == expected
    assert observed["env"]["http_proxy"] == expected
    assert observed["env"]["https_proxy"] == expected


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


@pytest.mark.parametrize(
    ("output", "secrets"),
    [
        ("xsec_token=topsecret", ("topsecret",)),
        (
            "api_key=sk-live-secret Authorization: Bearer "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
            ("sk-live-secret", "eyJhbGciOiJIUzI1NiJ9"),
        ),
        ("https://alice:password@example.test/archive", ("alice", "password")),
        (
            "https%253A%252F%252Fuser%253Apassword%2540example.test%252Farchive"
            "%253Fxsec_token%253Dtopsecret",
            ("password", "topsecret"),
        ),
    ],
)
def test_install_chromium_redaction_fails_closed_for_encoded_credentials(
    tmp_path, monkeypatch, output: str, secrets: tuple[str, ...]
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout=output, stderr=""
        ),
    )

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.success is False
    for secret in secrets:
        assert secret not in result.message


@pytest.mark.parametrize(
    "output",
    [
        "https://downloads.example/a?xsec_token=topsecret",
        "https%3A%2F%2Fdownloads.example%2Fa%3Fxsec_token%3Dtopsecret",
        "https%253A%252F%252Fdownloads.example%252Fa%253Fapi_token%253Dtopsecret",
        "https://downloads.example/a?access_token=topsecret",
        "https://downloads.example/a?client-secret=topsecret",
    ],
)
def test_install_chromium_redacts_sensitive_query_tokens_without_userinfo(
    tmp_path, monkeypatch, output: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout=output, stderr=""
        ),
    )

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.message == "[REDACTED]"
    assert "topsecret" not in result.message


@pytest.mark.parametrize(
    "output",
    [
        "https://[invalid/a?xsec_token=topsecret",
        "https%3A%2F%2F%5Binvalid%2Fa%3Fxsec_token%3Dtopsecret",
        "https%253A%252F%252F%255Binvalid%252Fa%253Fxsec_token%253Dtopsecret",
    ],
)
def test_install_chromium_redacts_malformed_sensitive_query_urls(
    tmp_path, monkeypatch, output: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout=output, stderr=""
        ),
    )

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.message == "[REDACTED]"
    assert "topsecret" not in result.message


def test_install_chromium_preserves_normal_encoded_url(tmp_path, monkeypatch) -> None:
    output = "https%253A%252F%252Fdownloads.example%252Fa%253Fbuild%253D123"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout=output, stderr=""
        ),
    )

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.message == output


@pytest.mark.parametrize(
    "output",
    [
        "https://tokenizer.example/a?build=123",
        "https%3A%2F%2Ftokenizer.example%2Fa%3Fbuild%3D123",
        "https%253A%252F%252Ftokenizer.example%252Fa%253Fbuild%253D123",
        "https://downloads.example/token-cache?build=123",
        "https%3A%2F%2Fdownloads.example%2Ftoken-cache%3Fbuild%3D123",
        "https%253A%252F%252Fdownloads.example%252Ftoken-cache%253Fbuild%253D123",
        "https://monkey.example/a?build=123",
        "https%3A%2F%2Fmonkey.example%2Fa%3Fbuild%3D123",
        "https%253A%252F%252Fmonkey.example%252Fa%253Fbuild%253D123",
    ],
)
def test_install_chromium_preserves_sensitive_substrings_outside_query_keys(
    tmp_path, monkeypatch, output: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout=output, stderr=""
        ),
    )

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.message == output


def test_install_chromium_preserves_normal_non_url_output(tmp_path, monkeypatch) -> None:
    output = "Chromium downloaded successfully"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, stdout=output, stderr=""
        ),
    )

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.success is True
    assert result.message == output


@pytest.mark.parametrize(
    "output",
    [
        "Cookie: session=topsecret",
        "Set-Cookie: session=topsecret; HttpOnly",
        "MOVIEPILOT_API_TOKEN=topsecret",
        "api_token=topsecret",
        "TOKEN=topsecret",
        "SERVICE_KEY=topsecret",
        "client-secret: topsecret",
        "password=topsecret",
        "authorization: Bearer topsecret",
        "credential=topsecret",
        '{"xsec_token":"topsecret"}',
    ],
)
def test_install_chromium_redacts_each_sensitive_assignment_line(
    tmp_path, monkeypatch, output: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout=output, stderr=""
        ),
    )

    result = BrowserManager(tmp_path, "rednote", None, lambda: None).install_chromium()

    assert result.success is False
    assert "topsecret" not in result.message
    assert result.message == "[REDACTED]"


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
    fake_playwright.page.visible_selectors.add(".login-btn")

    result = manager.check_login()

    assert result.success is True
    assert fake_playwright.page.goto_args[0] == ("https://www.xiaohongshu.com",)


def test_cookie_import_immediately_validates_the_real_browser_session(
    manager,
    fake_playwright,
) -> None:
    fake_playwright.page.initial_logged_in = True
    fake_playwright.context_storage_state = {
        "cookies": [
            {
                "name": "a1",
                "value": "refreshed-secret",
                "domain": ".xiaohongshu.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }

    result = manager.import_credentials(cookie_header="a1=secret")

    assert result == OperationResult(
        success=True,
        data={"credential_type": "cookie", "cookie_count": 1},
    )
    assert fake_playwright.page.goto_args[0] == ("https://www.xiaohongshu.com",)
    assert manager.session_store.status() == "PRESENT"


def test_check_login_waits_for_profile_link_when_rednote_state_is_absent(
    tmp_path, fake_playwright
) -> None:
    fake_playwright.page.url = "https://www.rednote.com"
    fake_playwright.page.initial_logged_in = None
    fake_playwright.page.delayed_visible_selectors.add(
        'a[title="我"][href^="/user/profile/"]'
    )
    browser = BrowserManager(
        tmp_path,
        site="rednote",
        proxy=None,
        playwright_factory=lambda: fake_playwright,
    )

    result = browser.check_login()

    assert result.success is True
    assert fake_playwright.page.goto_args[0] == ("https://www.rednote.com",)


@pytest.mark.parametrize("state", [False, None])
def test_check_login_uses_dom_fallback_for_logged_out_page(
    manager, fake_playwright, state
) -> None:
    fake_playwright.page.initial_logged_in = state
    fake_playwright.page.visible_selectors.add(".login-container")

    result = manager.check_login()

    assert result.success is False
    assert result.code == "LOGIN_REQUIRED"
    assert result.should_pause is True


def test_check_login_fails_closed_without_state_or_login_dom(
    manager, fake_playwright
) -> None:
    fake_playwright.page.initial_logged_in = None

    result = manager.check_login()

    assert result.success is False
    assert result.code == "TEMPORARY_FAILURE"
    assert result.should_pause is False


def test_check_login_fails_closed_with_only_hidden_login_dom(
    manager, fake_playwright
) -> None:
    fake_playwright.page.initial_logged_in = None
    fake_playwright.page.present_selectors.add(".login-btn")

    result = manager.check_login()

    assert result.success is False
    assert result.code == "TEMPORARY_FAILURE"
    assert result.should_pause is False


def test_check_login_finds_visible_second_selector(manager, fake_playwright) -> None:
    fake_playwright.page.initial_logged_in = None
    fake_playwright.page.present_selectors.add(".login-btn")
    fake_playwright.page.visible_selectors.add(".login-container")

    result = manager.check_login()

    assert result.code == "LOGIN_REQUIRED"
    assert result.should_pause is True


@pytest.mark.parametrize("operation", ["check_login", "logout"])
def test_browser_actions_sanitize_launch_failures(tmp_path, operation: str) -> None:
    def fail_factory():
        raise RuntimeError("failed at https://user:password@example.test/profile")

    _make_executable(tmp_path)
    browser = BrowserManager(tmp_path, "rednote", None, fail_factory)

    result = getattr(browser, operation)()

    assert result.success is False
    assert result.code == "BROWSER_UNAVAILABLE"
    assert "password" not in result.message


def test_session_releases_lock_when_playwright_factory_fails(tmp_path, fake_playwright) -> None:
    failed_data = tmp_path / "failed"
    _make_executable(failed_data)
    failing = BrowserManager(
        failed_data,
        "rednote",
        None,
        lambda: (_ for _ in ()).throw(RuntimeError("launch failed")),
    )
    working = BrowserManager(tmp_path, "rednote", None, lambda: fake_playwright)

    with pytest.raises(
        BrowserUnavailableError, match="Playwright could not be started"
    ):
        with failing.session():
            pass
    with working.session():
        pass

    assert fake_playwright.context_closed is True


def test_session_classifies_missing_chromium_as_browser_unavailable(tmp_path) -> None:
    browser = BrowserManager(tmp_path, "rednote", None, lambda: None)

    with pytest.raises(BrowserUnavailableError):
        with browser.session():
            pass


def test_logout_clears_context_and_imported_storage_state(manager, fake_playwright) -> None:
    manager.session_store.import_cookie_header("a1=secret")

    result = manager.logout()

    assert result.success is True
    assert fake_playwright.cookies_cleared is True
    storage_clear = "window.localStorage.clear(); window.sessionStorage.clear();"
    assert fake_playwright.page.evaluate_calls[-1] == storage_clear
    assert fake_playwright.page.evaluate_calls.count(storage_clear) == 1
    assert fake_playwright.context_closed is True
    assert manager.session_store.status() == "MISSING"


def test_logout_clears_profile_and_reports_navigation_risk(manager, fake_playwright) -> None:
    fake_playwright.page.goto_status = 403

    result = manager.logout()

    assert fake_playwright.cookies_cleared is True
    assert result.code == "AUTH_REQUIRED"
    assert result.should_pause is True


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("error_code=300012", "XHS_RISK_CONTROL"),
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


def test_detect_risk_uses_dom_inner_text_when_locator_read_is_unavailable(
    manager, fake_playwright
) -> None:
    fake_playwright.page.text = "请完成人机验证"
    fake_playwright.page.body_error = TimeoutError("DOM read timed out")

    risk = manager.detect_risk(fake_playwright.page)

    assert risk.code == "XHS_RISK_CONTROL"
    assert risk.should_pause is True


def test_detect_risk_pauses_on_xhs_security_limit_url(manager, fake_playwright) -> None:
    fake_playwright.page.url = "https://www.xiaohongshu.com/website-login/error"

    risk = manager.detect_risk(fake_playwright.page)

    assert risk.success is False
    assert risk.code == "XHS_RISK_CONTROL"
    assert risk.should_pause is True


def test_detect_risk_returns_clear_result_for_normal_page(manager, fake_playwright) -> None:
    fake_playwright.page.text = (
        "评论和 @，共有 429 条评论，403 位用户参与，编号 300012"
    )

    assert manager.detect_risk(fake_playwright.page) == OperationResult(success=True)


def test_ordinary_login_sms_copy_is_login_required_not_risk_control(
    manager, fake_playwright
) -> None:
    fake_playwright.page.text = "手机号登录 获取验证码"
    fake_playwright.page.visible_selectors.add(".login-container")

    assert manager.detect_risk(fake_playwright.page) == OperationResult(success=True)
    login = manager.detect_login_page(fake_playwright.page)
    assert login.code == "LOGIN_REQUIRED"
    assert login.should_pause is True


@pytest.mark.parametrize(
    ("operation", "status", "expected_code"),
    [
        ("check_login", 403, "AUTH_REQUIRED"),
        ("logout", 429, "RATE_LIMITED"),
    ],
)
def test_navigation_response_status_pauses_even_with_empty_body(
    manager, fake_playwright, operation: str, status: int, expected_code: str
) -> None:
    fake_playwright.page.goto_status = status
    fake_playwright.page.text = ""

    result = getattr(manager, operation)()

    assert result.code == expected_code
    assert result.should_pause is True


@pytest.mark.parametrize("operation", ["check_login", "logout"])
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(403, "AUTH_REQUIRED"), (429, "RATE_LIMITED")],
)
def test_navigation_status_takes_priority_over_body_read_failure(
    manager, fake_playwright, operation: str, status: int, expected_code: str
) -> None:
    fake_playwright.page.goto_status = status
    fake_playwright.page.body_error = RuntimeError("body unavailable")

    result = getattr(manager, operation)()

    assert result.code == expected_code
    assert result.should_pause is True
    if operation == "logout":
        assert fake_playwright.cookies_cleared is True

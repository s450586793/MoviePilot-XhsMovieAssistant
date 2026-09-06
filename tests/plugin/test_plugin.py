from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import xhsmovieassistant as entrypoint


def _plugin(tmp_path: Path) -> Any:
    plugin_class = getattr(entrypoint, "XhsMovieAssistant", None)
    assert plugin_class is not None, "MoviePilot plugin entrypoint is missing"
    instance = plugin_class()
    instance.get_data_path = lambda: tmp_path
    return instance


def _components(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node in nodes:
        found.append(node)
        content = node.get("content")
        if isinstance(content, list):
            found.extend(_components(content))
    return found


class _RuntimeRepository:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            browser_state=entrypoint.BrowserState.READY,
            pause_code=None,
            paused_at=None,
            pause_notified=False,
        )
        self.transitions = []

    def get_runtime_state(self):
        return self.state

    def set_runtime_state(self, browser_state, **kwargs):
        self.transitions.append((browser_state, kwargs))
        self.state = SimpleNamespace(browser_state=browser_state, **kwargs)
        return self.state


class _FakeWorker:
    def __init__(self, *, alive: bool, exits_on_join: bool = False) -> None:
        self.alive = alive
        self.exits_on_join = exits_on_join
        self.join_timeouts = []

    def join(self, timeout):
        self.join_timeouts.append(timeout)
        if self.exits_on_join:
            self.alive = False

    def is_alive(self):
        return self.alive


def test_init_plugin_stops_old_runtime_before_start(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = []
    monkeypatch.setattr(plugin, "stop_service", lambda: calls.append("stop"))
    monkeypatch.setattr(
        plugin,
        "_build_runtime",
        lambda generation, stop_event: calls.append("build"),
    )

    plugin.init_plugin({"enabled": True, "authorized_user_ids": "u1"})

    assert calls == ["stop", "build"]


def test_default_config_is_dry_run_and_public_reply_off(tmp_path):
    plugin = _plugin(tmp_path)

    _, defaults = plugin.get_form()

    assert defaults["enable_subscription"] is False
    assert defaults["reply_enabled"] is False
    assert defaults["poll_interval_minutes"] == 2
    assert defaults["confidence_threshold"] == 0.85


def test_init_plugin_clamps_config_and_requires_authorized_ids(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    built = []
    monkeypatch.setattr(
        plugin,
        "_build_runtime",
        lambda generation, stop_event: built.append(True),
    )

    plugin.init_plugin(
        {
            "enabled": True,
            "authorized_user_ids": "  ",
            "poll_interval_minutes": 99,
            "confidence_threshold": -2,
        }
    )

    assert plugin.get_state() is False
    assert plugin._poll_interval_minutes == 10
    assert plugin._confidence_threshold == 0.0
    assert built == []


def test_get_service_is_interval_and_hidden_while_paused(tmp_path):
    plugin = _plugin(tmp_path)
    runtime_state = SimpleNamespace(browser_state=entrypoint.BrowserState.READY)
    plugin._enabled = True
    plugin._repository = SimpleNamespace(get_runtime_state=lambda: runtime_state)

    services = plugin.get_service()

    assert len(services) == 1
    assert services[0]["trigger"] == "interval"
    assert services[0]["kwargs"] == {"minutes": 2}
    runtime_state.browser_state = entrypoint.BrowserState.PAUSED
    assert plugin.get_service() == []


def test_api_routes_are_post_only_and_explicitly_authenticated(tmp_path):
    plugin = _plugin(tmp_path)
    routes = plugin.get_api()

    assert [route["path"] for route in routes] == [
        "/chromium/install",
        "/login/start",
        "/logout",
        "/resume",
        "/poll",
        "/requests/{request_id}/reprocess",
        "/requests/{request_id}/ignore",
        "/requests/{request_id}/manual",
        "/test/ai",
        "/test/moviepilot",
        "/test/notification",
    ]
    assert all(route["methods"] == ["POST"] for route in routes)
    assert all(route["auth"] == "apikey" for route in routes)


def test_direct_endpoint_auth_fails_closed_without_calling_runtime(tmp_path):
    plugin = _plugin(tmp_path)
    called = []
    plugin._browser = SimpleNamespace(install_chromium=lambda: called.append(True))

    missing = plugin.install_chromium()
    wrong = plugin.install_chromium(apikey="wrong")

    assert missing.success is False
    assert wrong.success is False
    assert called == []
    assert "token" not in str(missing.data).casefold()


def test_detail_page_uses_cached_status_table_qr_and_icon_buttons(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._cached_status = {
        "browser": "READY",
        "login": "WAITING_FOR_SCAN",
        "qrcode": "data:image/png;base64,cXI=",
    }
    plugin._repository = SimpleNamespace(recent=lambda limit: [])
    plugin._browser = SimpleNamespace(
        check_login=lambda: (_ for _ in ()).throw(AssertionError("network access"))
    )

    page = plugin.get_page()
    nodes = _components(page)

    assert "VAlert" in {node["component"] for node in nodes}
    assert "VDataTable" in {node["component"] for node in nodes}
    qr = next(node for node in nodes if node["component"] == "VImg")
    assert qr["props"]["width"] == 240
    assert qr["props"]["height"] == 240
    buttons = [node for node in nodes if node["component"] == "VBtn"]
    assert buttons
    assert all(button["props"]["prepend-icon"].startswith("mdi-") for button in buttons)


def test_init_recovers_interrupted_before_building_runtime(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = []

    class FakeRepository:
        def __init__(self, path):
            assert path == tmp_path / "app.db"

        def recover_interrupted(self):
            calls.append("recover")

    monkeypatch.setattr(entrypoint, "RequestRepository", FakeRepository)
    monkeypatch.setattr(
        plugin,
        "_build_runtime",
        lambda generation, stop_event: calls.append("build"),
    )

    plugin.init_plugin({"enabled": True, "authorized_user_ids": "u1"})

    assert calls == ["recover", "build"]


def test_runtime_paths_stay_below_moviepilot_plugin_data_path(tmp_path):
    plugin = _plugin(tmp_path)

    plugin.init_plugin({"enabled": True, "authorized_user_ids": "u1"})

    assert plugin._repository._database_path == tmp_path / "app.db"
    assert plugin._browser.profile_path == tmp_path / "browser"
    assert plugin._browser.browser_path == tmp_path / "ms-playwright"
    plugin.stop_service()


def test_poll_once_is_non_blocking_and_rejects_reentry(tmp_path):
    plugin = _plugin(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def block():
        entered.set()
        release.wait(timeout=1)

    plugin._enabled = True
    plugin._service = SimpleNamespace(poll_once=block)

    assert plugin.poll_once() is True
    assert entered.wait(timeout=0.5)
    assert plugin.poll_once() is False
    release.set()
    plugin._worker.join(timeout=0.5)


def test_stop_service_uses_bounded_join_and_closes_active_context(tmp_path):
    plugin = _plugin(tmp_path)
    calls = []
    context = SimpleNamespace(close=lambda: calls.append("close"))
    plugin._browser = SimpleNamespace(_active_context=context)
    worker = _FakeWorker(alive=True, exits_on_join=True)
    plugin._worker = worker
    plugin._enabled = True

    plugin.stop_service()

    assert calls == ["close"]
    assert worker.join_timeouts == [2.0]
    assert plugin.get_state() is False
    assert plugin._worker is None
    assert plugin._browser is None


@pytest.mark.parametrize(
    ("path", "args", "kwargs"),
    [
        ("/chromium/install", (), {}),
        ("/login/start", (), {}),
        ("/logout", (), {}),
        ("/resume", (), {}),
        ("/poll", (), {}),
        ("/requests/{request_id}/reprocess", (1,), {}),
        ("/requests/{request_id}/ignore", (1,), {}),
        ("/requests/{request_id}/manual", (1,), {"body": {"title": "A"}}),
        ("/test/ai", (), {"body": {"title": "A"}}),
        ("/test/moviepilot", (), {"body": {"title": "A"}}),
        ("/test/notification", (), {}),
    ],
)
def test_every_direct_endpoint_fails_closed_without_credentials(
    tmp_path, path, args, kwargs
):
    plugin = _plugin(tmp_path)
    route = next(route for route in plugin.get_api() if route["path"] == path)

    response = route["endpoint"](*args, **kwargs)

    assert response.success is False
    assert "test-api-token" not in repr(response.__dict__)


def test_qr_endpoint_returns_authenticated_inline_payload(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._browser = SimpleNamespace(
        capture_login_qrcode=lambda: entrypoint.OperationResult(
            success=True, data=b"png"
        )
    )

    response = plugin.start_login(apikey="test-api-token")

    assert response.success is True
    assert response.data == {"qrcode": "data:image/png;base64,cG5n"}
    assert "test-api-token" not in repr(response.__dict__)


@pytest.mark.parametrize("secret", ["Cookie=session-secret", "xsec_token=secret"])
def test_browser_failure_response_redacts_external_secret(tmp_path, secret):
    plugin = _plugin(tmp_path)
    plugin._browser = SimpleNamespace(
        capture_login_qrcode=lambda: entrypoint.OperationResult(
            success=False,
            code="UPSTREAM_ERROR",
            message=secret,
        )
    )

    response = plugin.start_login(apikey="test-api-token")

    assert response.success is False
    assert secret not in repr(response.__dict__)


def test_service_actions_and_diagnostics_use_runtime_boundaries(tmp_path):
    plugin = _plugin(tmp_path)
    resolved = entrypoint.Resolution(
        status="resolved",
        title="Arrival",
        media_type="movie",
        year=2016,
        confidence=1.0,
    )
    matched = SimpleNamespace(
        reason_code="MATCHED",
        match=SimpleNamespace(model_dump=lambda mode: {"title": "Arrival"}),
    )
    plugin._service = SimpleNamespace(
        reprocess=lambda request_id: SimpleNamespace(
            model_dump=lambda mode: {"status": "DRY_RUN_MATCHED", "id": request_id}
        ),
        ignore=lambda request_id: SimpleNamespace(id=request_id, status="IGNORED"),
        manual_resolve=lambda request_id, resolution: SimpleNamespace(
            model_dump=lambda mode: {
                "status": "DRY_RUN_MATCHED",
                "title": resolution.title,
            }
        ),
    )
    plugin._resolver = SimpleNamespace(resolve=lambda request: resolved)
    plugin._moviepilot = SimpleNamespace(match=lambda resolution: matched)

    assert plugin.reprocess(1, apikey="test-api-token").success is True
    assert plugin.ignore(1, apikey="test-api-token").success is True
    assert plugin.manual_resolve(
        1,
        {"title": "Arrival", "media_type": "movie", "year": 2016},
        apikey="test-api-token",
    ).success is True
    assert plugin.test_ai(
        {"title": "Arrival"}, apikey="test-api-token"
    ).data["title"] == "Arrival"
    assert plugin.test_moviepilot(
        {"title": "Arrival", "media_type": "movie"},
        apikey="test-api-token",
    ).data == {"reason_code": "MATCHED", "match": {"title": "Arrival"}}


def test_notification_test_reuses_plugin_post_message(tmp_path):
    plugin = _plugin(tmp_path)

    response = plugin.test_notification(apikey="test-api-token")

    assert response.success is True
    assert plugin._posted_message["mtype"] is entrypoint.NotificationType.Plugin
    assert plugin._posted_message["title"] == "小红书影视助手"


def test_form_contains_required_native_vuetify_controls(tmp_path):
    plugin = _plugin(tmp_path)

    form, _ = plugin.get_form()
    nodes = _components(form)
    components = [node["component"] for node in nodes]
    models = {
        node.get("props", {}).get("model")
        for node in nodes
        if node["component"] in {"VSwitch", "VSelect", "VTextField", "VTextarea"}
    }

    assert components.count("VSwitch") == 4
    assert "VSelect" in components
    assert {"authorized_user_ids", "poll_interval_minutes", "confidence_threshold"} <= models
    assert {f"template_{status}" for status in entrypoint.DEFAULT_TEMPLATES} <= models


def test_detail_page_degrades_when_repository_read_fails(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._repository = SimpleNamespace(
        recent=lambda limit: (_ for _ in ()).throw(RuntimeError("database secret"))
    )

    nodes = _components(plugin.get_page())
    table = next(node for node in nodes if node["component"] == "VDataTable")

    assert table["props"]["items"] == []
    assert "database secret" not in repr(nodes)


def test_init_plugin_degrades_when_repository_cannot_open(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)

    def fail(_path):
        raise OSError("filesystem / secret")

    monkeypatch.setattr(entrypoint, "RequestRepository", fail)

    plugin.init_plugin({"enabled": True, "authorized_user_ids": "u1"})

    assert plugin.get_state() is False
    assert plugin._cached_status["activity"] == "START_FAILED"
    assert "filesystem / secret" not in repr(plugin._cached_status)


def test_install_endpoint_contains_worker_setup_failure(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)

    def fail():
        raise RuntimeError("Cookie=install-secret")

    monkeypatch.setattr(plugin, "_ensure_browser", fail)

    response = plugin.install_chromium(apikey="test-api-token")

    assert response.success is False
    assert "install-secret" not in repr(response.__dict__)


def test_poll_endpoint_contains_worker_setup_failure(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    monkeypatch.setattr(
        plugin,
        "poll_once",
        lambda: (_ for _ in ()).throw(RuntimeError("xsec_token=poll-secret")),
    )

    response = plugin.poll(apikey="test-api-token")

    assert response.success is False
    assert "poll-secret" not in repr(response.__dict__)


def test_detail_page_prefers_durable_pause_state_over_stale_cache(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._enabled = True
    plugin._cached_status["browser"] = entrypoint.BrowserState.READY.value
    plugin._repository = SimpleNamespace(
        recent=lambda limit: [],
        get_runtime_state=lambda: SimpleNamespace(
            browser_state=entrypoint.BrowserState.PAUSED,
            pause_code="SESSION_EXPIRED",
        ),
    )

    page = plugin.get_page()

    assert page[0]["props"]["type"] == "warning"
    assert "SESSION_EXPIRED" in page[0]["props"]["text"]


@pytest.mark.parametrize(
    "fake_request,body",
    [
        (SimpleNamespace(headers={"X-API-Key": "test-api-token"}, query_params={}), None),
        (
            SimpleNamespace(
                headers={"Authorization": "Bearer test-api-token"}, query_params={}
            ),
            None,
        ),
        (SimpleNamespace(headers={}, query_params={"apikey": "test-api-token"}), None),
        (None, {"api_key": "test-api-token", "title": "Arrival"}),
    ],
)
def test_direct_endpoint_accepts_supported_supplied_credentials(
    tmp_path, fake_request, body
):
    plugin = _plugin(tmp_path)
    plugin._resolver = SimpleNamespace(
        resolve=lambda media_request: entrypoint.Resolution(
            status="resolved",
            title="Arrival",
            media_type="movie",
            confidence=1.0,
        )
    )

    response = plugin.test_ai(
        body or {"title": "Arrival"},
        request=fake_request,
    )

    assert response.success is True


def test_diagnostic_response_redacts_runtime_secret_values(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._resolver = SimpleNamespace(
        resolve=lambda media_request: entrypoint.Resolution(
            status="resolved",
            title="test-api-token",
            media_type="movie",
            confidence=1.0,
            reason="Cookie=session-secret xsec_token=route-secret",
        )
    )

    response = plugin.test_ai(
        {"title": "Arrival"}, apikey="test-api-token"
    )

    rendered = repr(response.__dict__)
    assert response.success is True
    assert "test-api-token" not in rendered
    assert "session-secret" not in rendered
    assert "route-secret" not in rendered


@pytest.mark.parametrize(
    ("endpoint_name", "browser_method"),
    [("start_login", "capture_login_qrcode"), ("logout", "logout")],
)
def test_browser_pause_outcome_is_persisted_and_safely_notified_once(
    tmp_path, endpoint_name, browser_method
):
    plugin = _plugin(tmp_path)
    repository = _RuntimeRepository()
    outcome = entrypoint.OperationResult(
        success=False,
        code="SESSION_EXPIRED",
        message="Cookie=session-secret",
        should_pause=True,
    )
    plugin._repository = repository
    plugin._browser = SimpleNamespace(**{browser_method: lambda: outcome})
    plugin._notifications_enabled = False
    notifications = []
    plugin.post_message = lambda **kwargs: notifications.append(kwargs)

    endpoint = getattr(plugin, endpoint_name)
    first = endpoint(apikey="test-api-token")
    second = endpoint(apikey="test-api-token")

    assert first.success is False
    assert second.success is False
    assert repository.state.browser_state is entrypoint.BrowserState.PAUSED
    assert repository.state.pause_code == "SESSION_EXPIRED"
    assert repository.state.pause_notified is True
    assert len(repository.transitions) == 1
    assert len(notifications) == 1
    assert "session-secret" not in repr(notifications)


def test_ordinary_browser_failure_does_not_pause_or_notify(tmp_path):
    plugin = _plugin(tmp_path)
    repository = _RuntimeRepository()
    plugin._repository = repository
    plugin._browser = SimpleNamespace(
        capture_login_qrcode=lambda: entrypoint.OperationResult(
            success=False,
            code="UPSTREAM_ERROR",
            should_pause=False,
        )
    )
    notifications = []
    plugin.post_message = lambda **kwargs: notifications.append(kwargs)

    response = plugin.start_login(apikey="test-api-token")

    assert response.success is False
    assert repository.state.browser_state is entrypoint.BrowserState.READY
    assert repository.transitions == []
    assert notifications == []


def test_browser_outcome_response_replaces_unknown_code_with_public_code(tmp_path):
    plugin = _plugin(tmp_path)
    repository = _RuntimeRepository()
    plugin._repository = repository
    plugin._browser = SimpleNamespace(
        capture_login_qrcode=lambda: entrypoint.OperationResult(
            success=False,
            code="Cookie=session-secret",
            should_pause=True,
        )
    )
    plugin.post_message = lambda **kwargs: None

    response = plugin.start_login(apikey="test-api-token")

    assert response.success is False
    assert response.data == {"code": "UPSTREAM_ERROR"}
    assert repository.state.pause_code == "UPSTREAM_ERROR"
    assert "session-secret" not in repr(response)


def test_stop_retains_live_worker_after_join_timeout_and_reload_does_not_overlap(
    tmp_path, monkeypatch
):
    plugin = _plugin(tmp_path)
    worker = _FakeWorker(alive=True)
    plugin._worker = worker
    built = []
    monkeypatch.setattr(
        plugin,
        "_build_runtime",
        lambda generation, stop_event: built.append(True),
    )

    plugin.stop_service()
    plugin.init_plugin({"enabled": True, "authorized_user_ids": "u1"})

    assert worker.join_timeouts == [2.0, 2.0]
    assert plugin._worker is worker
    assert plugin.get_state() is False
    assert built == []


def test_reload_replaces_generation_stop_event_instead_of_clearing_old_event(tmp_path):
    plugin = _plugin(tmp_path)
    old_stop_event = plugin._stop_event
    plugin._worker = _FakeWorker(alive=True, exits_on_join=True)

    plugin.init_plugin({"enabled": True, "authorized_user_ids": "u1"})

    assert old_stop_event.is_set()
    assert plugin._stop_event is not old_stop_event
    assert plugin._stop_event.is_set() is False
    plugin.stop_service()


def test_stop_during_runtime_build_prevents_stale_runtime_publication(
    tmp_path, monkeypatch
):
    plugin = _plugin(tmp_path)
    build_started = threading.Event()
    finish_build = threading.Event()
    browser = SimpleNamespace(_active_context=None)

    def build_browser(*args, **kwargs):
        build_started.set()
        finish_build.wait(timeout=1)
        return browser

    monkeypatch.setattr(entrypoint, "BrowserManager", build_browser)
    monkeypatch.setattr(entrypoint, "MediaResolver", lambda: SimpleNamespace())
    monkeypatch.setattr(entrypoint, "MoviePilotGateway", lambda: SimpleNamespace())
    monkeypatch.setattr(entrypoint, "XhsGateway", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        entrypoint,
        "AssistantService",
        lambda **kwargs: SimpleNamespace(poll_once=lambda: None),
    )
    initializer = threading.Thread(
        target=plugin.init_plugin,
        args=({"enabled": True, "authorized_user_ids": "u1"},),
    )

    initializer.start()
    try:
        assert build_started.wait(timeout=1)
        plugin.stop_service()
    finally:
        finish_build.set()
        initializer.join(timeout=1)

    assert initializer.is_alive() is False
    assert plugin.get_state() is False
    assert plugin._stop_event.is_set() is True
    assert plugin._browser is None
    assert plugin._resolver is None
    assert plugin._moviepilot is None
    assert plugin._service is None
    calls = []
    assert plugin._start_worker("poll", lambda: calls.append("poll")) is False
    assert plugin._worker is None
    assert calls == []


def test_stop_boundary_rejects_all_workers_until_next_initialized_generation(
    tmp_path,
):
    plugin = _plugin(tmp_path)
    calls = []
    browser = SimpleNamespace(
        install_chromium=lambda: calls.append("install")
    )

    plugin.stop_service()
    plugin._browser = browser
    stopped = plugin.install_chromium(apikey="test-api-token")

    assert stopped.success is False
    assert calls == []

    plugin.init_plugin({"enabled": False})
    plugin._browser = browser
    initialized = plugin.install_chromium(apikey="test-api-token")
    plugin._worker.join(timeout=0.5)

    assert initialized.success is True
    assert calls == ["install"]


def test_runtime_payload_sanitizer_handles_keys_headers_and_token_count(tmp_path):
    plugin = _plugin(tmp_path)
    payload = {
        "authorization": "Bearer auth-key-secret",
        "Cookie": "first=cookie-one; second=cookie-two",
        "llm_api_key": "llm-secret",
        "xsecToken": "xsec-secret",
        "raw_notification": {"body": "notification-secret"},
        "token_count": 42,
        "reason": (
            "Authorization: Bearer header-secret\n"
            "Cookie: first=header-cookie-one; second=header-cookie-two"
        ),
    }
    plugin._resolver = SimpleNamespace(
        resolve=lambda media_request: SimpleNamespace(
            model_dump=lambda mode: payload
        )
    )

    response = plugin.test_ai(
        {"title": "Arrival"}, apikey="test-api-token"
    )

    rendered = repr(response.data)
    assert response.success is True
    assert response.data["token_count"] == 42
    for secret in (
        "auth-key-secret",
        "cookie-one",
        "cookie-two",
        "llm-secret",
        "xsec-secret",
        "notification-secret",
        "header-secret",
        "header-cookie-one",
        "header-cookie-two",
    ):
        assert secret not in rendered
    assert set(response.data) == {"token_count", "reason"}

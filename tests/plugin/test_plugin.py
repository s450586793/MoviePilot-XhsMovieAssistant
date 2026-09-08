from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import xhsmovieassistant as entrypoint
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from xhsmovieassistant.models import ProcessingResult, ReplyStatus, RequestStatus


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


def _moviepilot_route_client(plugin: Any, path: str) -> TestClient:
    """Register one plugin route with MoviePilot's bearer dependency contract."""
    route = next(route for route in plugin.get_api() if route["path"] == path)
    bearer = HTTPBearer(auto_error=False)

    def verify_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if credentials is None or credentials.credentials != "browser-jwt":
            raise HTTPException(status_code=401, detail="invalid browser session")

    dependencies = [Depends(verify_token)] if route["auth"] == "bear" else []
    app = FastAPI()
    app.add_api_route(
        path,
        route["endpoint"],
        methods=route["methods"],
        dependencies=dependencies,
        response_model=None,
    )
    return TestClient(app)


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
    assert {
        key: defaults[key]
        for key in (
            "reply_success_enabled",
            "reply_existing_enabled",
            "reply_confirmation_enabled",
            "reply_failure_enabled",
        )
    } == {
        "reply_success_enabled": False,
        "reply_existing_enabled": False,
        "reply_confirmation_enabled": False,
        "reply_failure_enabled": False,
    }
    assert defaults["poll_interval_minutes"] == 2
    assert defaults["confidence_threshold"] == 0.85
    assert defaults["browser_mode"] == "embedded"
    assert defaults["cdp_url"] == ""
    assert defaults["cdp_token"] == ""


def test_legacy_default_reply_text_is_migrated_without_overwriting_custom_text(
    tmp_path,
):
    plugin = _plugin(tmp_path)

    plugin._apply_config(
        {
            "template_SUBSCRIBED": "检测到{media_type}《{title}》{year_text}{season_text}，已推送订阅。",
            "template_NEED_CONFIRMATION": "暂时无法确定这篇笔记中的具体影视作品，请人工确认。",
            "template_FAILED": "我自己的失败文案",
        }
    )

    assert plugin._template_values["SUBSCRIBED"] == "收到，已安排订阅。"
    assert plugin._template_values["NEED_CONFIRMATION"] == "收到，影视不明确，请明示。"
    assert plugin._template_values["FAILED"] == "我自己的失败文案"


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


def test_confirmation_request_arms_moviepilot_input_for_wechat_admins(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    calls = []
    configs = {
        "primary": SimpleNamespace(
            name="primary",
            config={"WECHAT_ADMINS": "user-a, user-b"},
        )
    }
    module = SimpleNamespace(get_configs=lambda: configs)
    monkeypatch.setattr(
        entrypoint,
        "ModuleManager",
        lambda: SimpleNamespace(get_running_module=lambda module_id: module),
    )
    monkeypatch.setattr(
        entrypoint,
        "plugin_input_interaction_manager",
        SimpleNamespace(create_or_replace=lambda **kwargs: calls.append(kwargs)),
    )

    plugin._arm_wechat_confirmation(21)

    assert [(call["user_id"], call["source"]) for call in calls] == [
        ("user-a", "primary"),
        ("user-b", "primary"),
    ]
    assert all(call["plugin_id"] == "XhsMovieAssistant" for call in calls)
    assert all(call["payload"] == {"request_id": 21} for call in calls)
    assert all(call["channel"] is entrypoint.MessageChannel.Wechat for call in calls)


def test_wechat_input_is_routed_to_the_pending_confirmation(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = []
    plugin._enabled = True
    plugin._service = SimpleNamespace(
        confirm_from_text=lambda request_id, text: calls.append((request_id, text))
    )
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary")}),
    )
    event = SimpleNamespace(
        event_data={
            "plugin_id": "XhsMovieAssistant",
            "__mp_target_plugin_id": "XhsMovieAssistant",
            "text": "plugin_input|session-1",
            "input_text": "穿越时空的少女，2006，电影",
            "userid": "user-a",
            "channel": entrypoint.MessageChannel.Wechat,
            "source": "primary",
            "payload": {"request_id": 21},
        }
    )

    plugin.handle_confirmation_input(event)

    assert calls == [(21, "穿越时空的少女，2006，电影")]


def test_active_plugin_input_routes_slash_command_to_explicit_request_and_rearms_session(
    tmp_path,
    monkeypatch,
):
    """Mirror MoviePilot's plugin-input-before-command dispatch order."""
    plugin = _plugin(tmp_path)
    calls = []
    rearmed = []
    plugin._enabled = True
    plugin._service = SimpleNamespace(
        confirm_from_text=lambda request_id, text: calls.append((request_id, text))
    )
    plugin._repository = SimpleNamespace(
        get=lambda request_id: SimpleNamespace(
            id=request_id,
            status=RequestStatus.NEED_CONFIRMATION,
        )
    )
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary")}),
    )
    monkeypatch.setattr(
        entrypoint,
        "plugin_input_interaction_manager",
        SimpleNamespace(create_or_replace=lambda **kwargs: rearmed.append(kwargs)),
    )

    # MoviePilot has already consumed the active session for request 21 before
    # dispatching this MessageAction event to the plugin.
    plugin.handle_confirmation_input(
        SimpleNamespace(
            event_data={
                "plugin_id": "XhsMovieAssistant",
                "text": "plugin_input|session-21",
                "input_text": "/xhs_confirm 7 穿越时空的少女，2006，电影",
                "userid": "user-a",
                "channel": entrypoint.MessageChannel.Wechat,
                "source": "primary",
                "payload": {"request_id": 21},
            }
        )
    )

    assert calls == [(7, "穿越时空的少女，2006，电影")]
    assert [call["payload"] for call in rearmed] == [{"request_id": 21}]


def test_active_plugin_input_rearms_session_when_slash_command_is_invalid(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    calls = []
    rearmed = []
    plugin._enabled = True
    plugin._service = SimpleNamespace(
        confirm_from_text=lambda request_id, text: calls.append((request_id, text))
    )
    plugin._repository = SimpleNamespace(
        get=lambda request_id: SimpleNamespace(
            id=request_id,
            status=RequestStatus.NEED_CONFIRMATION,
        )
    )
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary")}),
    )
    monkeypatch.setattr(
        entrypoint,
        "plugin_input_interaction_manager",
        SimpleNamespace(create_or_replace=lambda **kwargs: rearmed.append(kwargs)),
    )

    plugin.handle_confirmation_input(
        SimpleNamespace(
            event_data={
                "plugin_id": "XhsMovieAssistant",
                "text": "plugin_input|session-21",
                "input_text": "/xhs_confirm not-an-id",
                "userid": "user-a",
                "channel": entrypoint.MessageChannel.Wechat,
                "source": "primary",
                "payload": {"request_id": 21},
            }
        )
    )

    assert calls == []
    assert [call["payload"] for call in rearmed] == [{"request_id": 21}]


@pytest.mark.parametrize("input_text", ["/xhs_confirm7 片名", "/version"])
def test_active_plugin_input_rearms_session_without_confirming_other_commands(
    tmp_path,
    monkeypatch,
    input_text,
):
    plugin = _plugin(tmp_path)
    calls = []
    rearmed = []
    plugin._enabled = True
    plugin._service = SimpleNamespace(
        confirm_from_text=lambda request_id, text: calls.append((request_id, text))
    )
    plugin._repository = SimpleNamespace(
        get=lambda request_id: SimpleNamespace(
            id=request_id,
            status=RequestStatus.NEED_CONFIRMATION,
        )
    )
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary")}),
    )
    monkeypatch.setattr(
        entrypoint,
        "plugin_input_interaction_manager",
        SimpleNamespace(create_or_replace=lambda **kwargs: rearmed.append(kwargs)),
    )

    plugin.handle_confirmation_input(
        SimpleNamespace(
            event_data={
                "plugin_id": "XhsMovieAssistant",
                "text": "plugin_input|session-21",
                "input_text": input_text,
                "userid": "user-a",
                "channel": entrypoint.MessageChannel.Wechat,
                "source": "primary",
                "payload": {"request_id": 21},
            }
        )
    )

    assert calls == []
    assert [call["payload"] for call in rearmed] == [{"request_id": 21}]


def test_confirmation_input_arms_only_targets_receiving_a_prompt(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    prompts = []
    interactions = []
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary"), ("user-b", "backup")}),
    )

    def post_message(**kwargs):
        prompts.append(kwargs)
        if kwargs["source"] == "backup":
            raise RuntimeError("notification unavailable")

    monkeypatch.setattr(plugin, "post_message", post_message)
    monkeypatch.setattr(
        entrypoint,
        "plugin_input_interaction_manager",
        SimpleNamespace(create_or_replace=lambda **kwargs: interactions.append(kwargs)),
    )

    plugin._arm_wechat_confirmation(21)

    assert [(prompt["userid"], prompt["source"]) for prompt in prompts] == [
        ("user-a", "primary"),
        ("user-b", "backup"),
    ]
    assert [(call["user_id"], call["source"]) for call in interactions] == [
        ("user-a", "primary"),
    ]


def test_confirmation_input_does_not_arm_without_wechat_admin_targets(
    tmp_path, monkeypatch
):
    plugin = _plugin(tmp_path)
    prompts = []
    interactions = []
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset(),
    )
    monkeypatch.setattr(plugin, "post_message", lambda **kwargs: prompts.append(kwargs))
    monkeypatch.setattr(
        entrypoint,
        "plugin_input_interaction_manager",
        SimpleNamespace(create_or_replace=lambda **kwargs: interactions.append(kwargs)),
    )

    plugin._arm_wechat_confirmation(21)

    assert prompts == []
    assert interactions == []


def test_confirmation_input_rejects_an_unconfigured_wechat_sender(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    calls = []
    plugin._enabled = True
    plugin._service = SimpleNamespace(
        confirm_from_text=lambda request_id, text: calls.append((request_id, text))
    )
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary")}),
    )

    plugin.handle_confirmation_input(
        SimpleNamespace(
            event_data={
                "plugin_id": "XhsMovieAssistant",
                "text": "plugin_input|session-1",
                "input_text": "穿越时空的少女，2006，电影",
                "userid": "other-user",
                "channel": entrypoint.MessageChannel.Wechat,
                "source": "primary",
                "payload": {"request_id": 21},
            }
        )
    )

    assert calls == []


def test_confirmation_input_reports_a_sanitized_failure_to_the_same_admin(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    plugin._enabled = True
    plugin._service = SimpleNamespace(
        confirm_from_text=lambda request_id, text: (_ for _ in ()).throw(
            RuntimeError("provider-secret")
        )
    )
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary")}),
    )

    plugin.handle_confirmation_input(
        SimpleNamespace(
            event_data={
                "plugin_id": "XhsMovieAssistant",
                "text": "plugin_input|session-1",
                "input_text": "穿越时空的少女，2006，电影",
                "userid": "user-a",
                "channel": entrypoint.MessageChannel.Wechat,
                "source": "primary",
                "payload": {"request_id": 21},
            }
        )
    )

    assert plugin._posted_message["channel"] is entrypoint.MessageChannel.Wechat
    assert plugin._posted_message["userid"] == "user-a"
    assert plugin._posted_message["source"] == "primary"
    assert "插件页人工确认" in plugin._posted_message["text"]
    assert "provider-secret" not in repr(plugin._posted_message)


def test_confirmation_slash_command_is_available_when_input_session_is_lost(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    calls = []
    plugin._enabled = True
    plugin._service = SimpleNamespace(
        confirm_from_text=lambda request_id, text: calls.append((request_id, text))
    )
    monkeypatch.setattr(
        plugin,
        "_wechat_confirmation_targets",
        lambda: frozenset({("user-a", "primary")}),
    )

    command = next(item for item in plugin.get_command() if item["cmd"] == "/xhs_confirm")
    plugin.handle_confirmation_command(
        SimpleNamespace(
            event_data={
                **command["data"],
                "arg_str": "21 穿越时空的少女，2006，电影",
                "user": "user-a",
                "channel": entrypoint.MessageChannel.Wechat,
                "source": "primary",
            }
        )
    )

    assert calls == [(21, "穿越时空的少女，2006，电影")]


def test_api_routes_are_post_only_and_explicitly_authenticated(tmp_path):
    plugin = _plugin(tmp_path)
    routes = plugin.get_api()

    assert [route["path"] for route in routes] == [
        "/state",
        "/chromium/install",
        "/login/start",
        "/logout",
        "/resume",
        "/poll",
        "/requests/{request_id}/reprocess",
        "/requests/{request_id}/ignore",
        "/requests/{request_id}/manual",
        "/requests/{request_id}/reply",
        "/test/ai",
        "/test/moviepilot",
        "/test/notification",
    ]
    state = routes[0]
    assert state["methods"] == ["GET"]
    assert all(route["methods"] == ["POST"] for route in routes[1:])
    assert all(route["auth"] == "bear" for route in routes)


def test_state_endpoint_returns_cached_status_and_durable_rows_without_runtime_work(
    tmp_path,
):
    plugin = _plugin(tmp_path)
    plugin._cached_status = {
        "browser": "READY",
        "login": "LOGGED_IN",
        "qrcode": None,
        "activity": "IDLE",
    }
    request = SimpleNamespace(
        id=7,
        status=RequestStatus.NEED_CONFIRMATION,
        title="Arrival",
        original_title="L'arrivee",
        media_type="movie",
        year=2016,
        season=None,
        media_source="tmdb",
        media_source_id="329865",
        tmdb_id=329865,
        score=0.93,
        subscription_id=None,
        error="UPSTREAM_ERROR",
        reply_status=ReplyStatus.PENDING,
        reply_id=None,
        updated_at=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
    )
    plugin._repository = SimpleNamespace(
        get_runtime_state=lambda: SimpleNamespace(
            browser_state=entrypoint.BrowserState.READY,
            pause_code=None,
        ),
        recent=lambda limit: [request],
    )
    plugin._browser = SimpleNamespace(
        check_login=lambda: (_ for _ in ()).throw(AssertionError("browser work")),
        install_chromium=lambda: (_ for _ in ()).throw(AssertionError("browser work")),
        chromium_status=lambda: (_ for _ in ()).throw(AssertionError("browser work")),
    )
    plugin._resolver = SimpleNamespace(
        resolve=lambda value: (_ for _ in ()).throw(AssertionError("LLM work")),
    )
    plugin._moviepilot = SimpleNamespace(
        match=lambda value: (_ for _ in ()).throw(AssertionError("MoviePilot work")),
    )
    plugin._service = SimpleNamespace(
        poll_once=lambda: (_ for _ in ()).throw(AssertionError("service work")),
    )
    plugin.post_message = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("notification work")
    )

    client = _moviepilot_route_client(plugin, "/state")
    response = client.get("/state", headers={"Authorization": "Bearer browser-jwt"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"] == {
        "status": {
            "browser": "READY",
            "login": "LOGGED_IN",
            "qrcode": None,
            "activity": "IDLE",
            "chromium": "UNKNOWN",
            "chromium_code": None,
            "pause_code": None,
        },
        "requests": [
            {
                "id": 7,
                "status": "NEED_CONFIRMATION",
                "title": "Arrival",
                "original_title": "L'arrivee",
                "media_type": "movie",
                "year": 2016,
                "season": "-",
                "match": "tmdb:329865 (0.93)",
                "subscription_id": "-",
                "reply": "PENDING",
                "error": "UPSTREAM_ERROR",
                "updated_at": "2026-09-07T12:00:00+00:00",
            }
        ],
    }


def test_state_preserves_completed_chromium_failure_with_durable_ready(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._repository = _RuntimeRepository()
    plugin._browser = SimpleNamespace(
        install_chromium=lambda: entrypoint.OperationResult(
            success=False,
            code="BROWSER_UNAVAILABLE",
            message="private install details",
        )
    )

    started = plugin.install_chromium(apikey="test-api-token")
    plugin._worker.join(timeout=0.5)
    response = _moviepilot_route_client(plugin, "/state").get(
        "/state", headers={"Authorization": "Bearer browser-jwt"}
    )

    assert started.success is True
    status = response.json()["data"]["status"]
    assert status["browser"] == "READY"
    assert status["chromium"] == "UNAVAILABLE"
    assert status["chromium_code"] == "BROWSER_UNAVAILABLE"
    assert "private install details" not in repr(status)
    fallback_text = plugin.get_page()[0]["props"]["text"]
    assert "Browser: READY" in fallback_text
    assert "Chromium: UNAVAILABLE (BROWSER_UNAVAILABLE)" in fallback_text


def test_state_replaces_prior_chromium_failure_after_completed_success(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._repository = _RuntimeRepository()
    outcomes = iter(
        (
            entrypoint.OperationResult(success=False, code="BROWSER_UNAVAILABLE"),
            entrypoint.OperationResult(success=True),
        )
    )
    plugin._browser = SimpleNamespace(install_chromium=lambda: next(outcomes))

    assert plugin.install_chromium(apikey="test-api-token").success is True
    plugin._worker.join(timeout=0.5)
    assert plugin.install_chromium(apikey="test-api-token").success is True
    plugin._worker.join(timeout=0.5)
    response = _moviepilot_route_client(plugin, "/state").get(
        "/state", headers={"Authorization": "Bearer browser-jwt"}
    )

    status = response.json()["data"]["status"]
    assert status["browser"] == "READY"
    assert status["chromium"] == "AVAILABLE"
    assert status["chromium_code"] is None


def test_state_keeps_chromium_failure_alongside_durable_pause(tmp_path):
    plugin = _plugin(tmp_path)
    repository = _RuntimeRepository()
    repository.state = SimpleNamespace(
        browser_state=entrypoint.BrowserState.PAUSED,
        pause_code="SESSION_EXPIRED",
    )
    plugin._repository = repository
    plugin._browser = SimpleNamespace(
        install_chromium=lambda: entrypoint.OperationResult(
            success=False, code="BROWSER_UNAVAILABLE"
        )
    )

    assert plugin.install_chromium(apikey="test-api-token").success is True
    plugin._worker.join(timeout=0.5)
    response = _moviepilot_route_client(plugin, "/state").get(
        "/state", headers={"Authorization": "Bearer browser-jwt"}
    )

    status = response.json()["data"]["status"]
    assert status["browser"] == "PAUSED"
    assert status["pause_code"] == "SESSION_EXPIRED"
    assert status["chromium"] == "UNAVAILABLE"
    assert status["chromium_code"] == "BROWSER_UNAVAILABLE"


def test_moviepilot_browser_jwt_reaches_bearer_authenticated_endpoint(tmp_path):
    plugin = _plugin(tmp_path)
    notifications = []
    plugin.post_message = lambda **kwargs: notifications.append(kwargs)
    client = _moviepilot_route_client(plugin, "/test/notification")

    response = client.post(
        "/test/notification",
        headers={"Authorization": "Bearer browser-jwt"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(notifications) == 1


def test_moviepilot_bearer_dependency_rejects_before_endpoint(tmp_path):
    plugin = _plugin(tmp_path)
    notifications = []
    plugin.post_message = lambda **kwargs: notifications.append(kwargs)
    client = _moviepilot_route_client(plugin, "/test/notification")

    response = client.post("/test/notification")

    assert response.status_code == 401
    assert notifications == []


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
    actions = {button["events"]["click"]["api"] for button in buttons}
    assert {
        "plugin/XhsMovieAssistant/test/ai",
        "plugin/XhsMovieAssistant/test/moviepilot",
        "plugin/XhsMovieAssistant/test/notification",
    } <= actions


def test_detail_page_exposes_match_errors_and_durable_row_actions(tmp_path):
    plugin = _plugin(tmp_path)
    request = SimpleNamespace(
        id=7,
        status=RequestStatus.NEED_CONFIRMATION,
        title="Arrival",
        original_title="",
        media_type="movie",
        year=2016,
        season=None,
        media_source="tmdb",
        media_source_id="329865",
        tmdb_id=329865,
        score=0.93,
        subscription_id=None,
        error="UPSTREAM_ERROR",
        reply_status=ReplyStatus.PENDING,
        reply_id=None,
        updated_at=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
    )
    plugin._repository = SimpleNamespace(recent=lambda limit: [request])

    nodes = _components(plugin.get_page())
    table = next(node for node in nodes if node["component"] == "VDataTable")
    headers = {header["key"] for header in table["props"]["headers"]}
    row = table["props"]["items"][0]

    assert {"media_type", "season", "match", "subscription_id", "error", "reply"} <= headers
    assert row["match"] == "tmdb:329865 (0.93)"
    assert row["error"] == "UPSTREAM_ERROR"
    assert row["reply"] == "PENDING"

    request_buttons = {
        button["events"]["click"]["api"]: button
        for button in nodes
        if button["component"] == "VBtn"
        and "/requests/7/" in button["events"]["click"]["api"]
    }
    assert set(request_buttons) == {
        "plugin/XhsMovieAssistant/requests/7/reprocess",
        "plugin/XhsMovieAssistant/requests/7/ignore",
        "plugin/XhsMovieAssistant/requests/7/manual",
        "plugin/XhsMovieAssistant/requests/7/reply",
    }
    assert request_buttons[
        "plugin/XhsMovieAssistant/requests/7/manual"
    ]["events"]["click"]["params"] == {
        "title": "Arrival",
        "media_type": "movie",
        "year": 2016,
        "season": None,
    }


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


def test_runtime_binds_external_cdp_browser_configuration(tmp_path):
    plugin = _plugin(tmp_path)

    plugin.init_plugin(
        {
            "enabled": True,
            "authorized_user_ids": "u1",
            "browser_mode": "cdp",
            "cdp_url": "http://cloakbrowser:9050/api/profiles/xhs/cdp",
            "cdp_token": "cdp-secret",
        }
    )

    assert plugin._browser.browser_mode == "cdp"
    assert plugin._browser.cdp_url == (
        "http://cloakbrowser:9050/api/profiles/xhs/cdp"
    )
    assert plugin._browser.cdp_token == "cdp-secret"
    assert plugin._cached_status["chromium"] == "EXTERNAL"
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


def test_poll_worker_stopped_at_entry_releases_activity_lock(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = []

    class _StopAtEntryThread:
        def __init__(self, *, target, **kwargs):
            self._target = target
            self.started = False

        def start(self):
            self.started = True
            plugin._stop_event.set()
            self._target()

        def is_alive(self):
            return False

    monkeypatch.setattr(entrypoint.threading, "Thread", _StopAtEntryThread)

    assert plugin._start_worker("poll", lambda: calls.append("poll")) is True
    assert plugin._worker.started is True
    assert plugin._worker.is_alive() is False
    assert calls == []
    assert plugin._activity_lock.acquire(blocking=False) is True
    plugin._activity_lock.release()


def test_management_action_does_not_overlap_active_poll(tmp_path):
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
    response = plugin.test_notification(apikey="test-api-token")
    release.set()
    plugin._worker.join(timeout=0.5)

    assert response.success is False
    assert not hasattr(plugin, "_posted_message")


def test_stop_service_uses_bounded_join_and_closes_active_context(tmp_path):
    plugin = _plugin(tmp_path)
    calls = []
    context = SimpleNamespace(close=lambda: calls.append("close"))
    plugin._browser = SimpleNamespace(
        _active_context=context,
        close_active_context=lambda: calls.append("manager-close"),
    )
    worker = _FakeWorker(alive=True, exits_on_join=True)
    plugin._worker = worker
    plugin._enabled = True

    plugin.stop_service()

    assert calls == ["manager-close"]
    assert worker.join_timeouts == [2.0]
    assert plugin.get_state() is False
    assert plugin._worker is None
    assert plugin._browser is None


@pytest.mark.parametrize(
    ("path", "args", "kwargs"),
    [
        ("/chromium/install", (), {}),
        ("/state", (), {}),
        ("/login/start", (), {}),
        ("/logout", (), {}),
        ("/resume", (), {}),
        ("/poll", (), {}),
        ("/requests/{request_id}/reprocess", (1,), {}),
        ("/requests/{request_id}/ignore", (1,), {}),
        ("/requests/{request_id}/manual", (1,), {"body": {"title": "A"}}),
        ("/requests/{request_id}/reply", (1,), {}),
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
        check_login=lambda: entrypoint.OperationResult(
            success=False,
            code="LOGIN_REQUIRED",
            should_pause=True,
        ),
        capture_login_qrcode=lambda: entrypoint.OperationResult(
            success=True, data=b"png"
        )
    )

    response = plugin.start_login(apikey="test-api-token")

    assert response.success is True
    assert response.data == {"qrcode": "data:image/png;base64,cG5n"}
    assert "test-api-token" not in repr(response.__dict__)


def test_login_endpoint_detects_persisted_login_after_reload(tmp_path):
    plugin = _plugin(tmp_path)
    captured = []
    plugin._browser = SimpleNamespace(
        check_login=lambda: entrypoint.OperationResult(success=True),
        capture_login_qrcode=lambda: captured.append(True),
    )

    response = plugin.start_login(apikey="test-api-token")

    assert response.success is True
    assert response.data == {"login": "LOGGED_IN"}
    assert plugin._cached_status["login"] == "LOGGED_IN"
    assert plugin._cached_status["qrcode"] is None
    assert captured == []


def test_login_endpoint_preserves_qr_when_status_is_indeterminate(tmp_path):
    plugin = _plugin(tmp_path)
    qrcode = "data:image/png;base64,b2xk"
    plugin._cached_status.update(login="WAITING_FOR_SCAN", qrcode=qrcode)
    captured = []
    plugin._browser = SimpleNamespace(
        check_login=lambda: entrypoint.OperationResult(
            success=False,
            code="TEMPORARY_FAILURE",
            should_pause=False,
        ),
        capture_login_qrcode=lambda: captured.append(True),
    )

    response = plugin.start_login(apikey="test-api-token")

    assert response.success is False
    assert response.data == {"code": "TEMPORARY_FAILURE"}
    assert plugin._cached_status["login"] == "WAITING_FOR_SCAN"
    assert plugin._cached_status["qrcode"] == qrcode
    assert captured == []


@pytest.mark.parametrize("secret", ["Cookie=session-secret", "xsec_token=secret"])
def test_browser_failure_response_redacts_external_secret(tmp_path, secret):
    plugin = _plugin(tmp_path)
    plugin._browser = SimpleNamespace(
        check_login=lambda: entrypoint.OperationResult(
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
        reply=lambda request_id: SimpleNamespace(
            id=request_id,
            status="SUBSCRIBED",
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
    assert plugin.reply(1, apikey="test-api-token").success is True
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

    assert components.count("VSwitch") == 8
    assert "VSelect" in components
    assert {
        "authorized_user_ids",
        "browser_mode",
        "cdp_url",
        "cdp_token",
        "poll_interval_minutes",
        "confidence_threshold",
    } <= models
    assert {
        "reply_success_enabled",
        "reply_existing_enabled",
        "reply_confirmation_enabled",
        "reply_failure_enabled",
    } <= models
    assert {f"template_{status}" for status in entrypoint.DEFAULT_TEMPLATES} <= models


def test_runtime_binds_per_category_reply_switches_into_templates(tmp_path):
    plugin = _plugin(tmp_path)

    plugin.init_plugin(
        {
            "enabled": True,
            "authorized_user_ids": "u1",
            "reply_enabled": True,
            "reply_success_enabled": True,
        }
    )

    templates = plugin._service.templates
    subscribed = ProcessingResult(status=RequestStatus.SUBSCRIBED)
    confirmation = ProcessingResult(status=RequestStatus.NEED_CONFIRMATION)
    assert templates.render(subscribed) is not None
    assert templates.render(confirmation) is None
    plugin.stop_service()


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
    [("start_login", "check_login"), ("logout", "logout")],
)
def test_browser_pause_outcome_is_persisted_and_safely_notified_once(
    tmp_path, endpoint_name, browser_method
):
    plugin = _plugin(tmp_path)
    repository = entrypoint.RequestRepository(tmp_path / "app.db")
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
    assert repository.get_runtime_state().browser_state is entrypoint.BrowserState.PAUSED
    assert repository.get_runtime_state().pause_code == "SESSION_EXPIRED"
    assert repository.get_runtime_state().pause_notified is True
    assert repository.pending_notifications(20) == []
    assert len(notifications) == 1
    assert "session-secret" not in repr(notifications)


def test_browser_pause_notification_retries_until_confirmed(tmp_path):
    plugin = _plugin(tmp_path)
    repository = entrypoint.RequestRepository(tmp_path / "app.db")
    outcome = entrypoint.OperationResult(
        success=False,
        code="SESSION_EXPIRED",
        should_pause=True,
    )
    plugin._repository = repository
    plugin._browser = SimpleNamespace(check_login=lambda: outcome)
    plugin._notifications_enabled = False
    attempts = []

    def flaky_post_message(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RuntimeError("notification unavailable")

    plugin.post_message = flaky_post_message

    first = plugin.start_login(apikey="test-api-token")
    failed = repository.get_runtime_state()
    assert first.success is False
    assert failed.browser_state is entrypoint.BrowserState.PAUSED
    assert failed.pause_notified is False
    assert repository.pending_notifications(20)[0].attempt_count == 1

    second = plugin.start_login(apikey="test-api-token")
    third = plugin.start_login(apikey="test-api-token")

    assert second.success is False
    assert third.success is False
    assert repository.get_runtime_state().pause_notified is True
    assert repository.pending_notifications(20) == []
    assert len(attempts) == 2


def test_ordinary_browser_failure_does_not_pause_or_notify(tmp_path):
    plugin = _plugin(tmp_path)
    repository = _RuntimeRepository()
    plugin._repository = repository
    plugin._browser = SimpleNamespace(
        check_login=lambda: entrypoint.OperationResult(
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
        check_login=lambda: entrypoint.OperationResult(
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


def test_stale_initializer_failure_does_not_clear_newer_runtime(
    tmp_path, monkeypatch
):
    plugin = _plugin(tmp_path)
    first_build_started = threading.Event()
    finish_first_build = threading.Event()
    browser_count = 0

    def build_browser(*args, **kwargs):
        nonlocal browser_count
        browser_count += 1
        if browser_count == 1:
            first_build_started.set()
            finish_first_build.wait(timeout=2)
        return SimpleNamespace(_active_context=None, build_number=browser_count)

    monkeypatch.setattr(entrypoint, "BrowserManager", build_browser)
    config = {"enabled": True, "authorized_user_ids": "u1"}
    first_initializer = threading.Thread(
        target=plugin.init_plugin,
        args=(config,),
    )

    first_initializer.start()
    try:
        assert first_build_started.wait(timeout=1)
        plugin.init_plugin(config)
        current_runtime = (
            plugin._browser,
            plugin._resolver,
            plugin._moviepilot,
            plugin._service,
        )
        current_status = dict(plugin._cached_status)
        assert plugin.get_state() is True
        assert all(component is not None for component in current_runtime)
        assert current_status["browser"] == entrypoint.BrowserState.READY.value
        assert current_status["activity"] == "IDLE"
    finally:
        finish_first_build.set()
        first_initializer.join(timeout=1)

    assert first_initializer.is_alive() is False
    assert plugin.get_state() is True
    assert plugin._browser is current_runtime[0]
    assert plugin._resolver is current_runtime[1]
    assert plugin._moviepilot is current_runtime[2]
    assert plugin._service is current_runtime[3]
    assert plugin._cached_status == current_status


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

"""MoviePilot V2 entrypoint for the Xiaohongshu movie assistant."""

from __future__ import annotations

import base64
import hmac
import re
import threading
from collections.abc import Callable, Mapping
from functools import wraps
from pathlib import Path
from typing import Any

try:
    from fastapi import Request
except ImportError:  # MoviePilot provides FastAPI; isolated plugin tests do not.
    Request = Any

from app import schemas
from app.core.config import settings
from app.core.event import Event, eventmanager
from app.core.module import ModuleManager
from app.helper.interaction import plugin_input_interaction_manager
from app.plugins import _PluginBase
from app.schemas import MessageChannel, NotificationType
from app.schemas.types import EventType

from .browser import BrowserManager, OperationResult
from .models import BrowserState, MediaRequest, NoteContext, RequestStatus, Resolution
from .moviepilot import MoviePilotGateway
from .notifications import enqueue_pause_notification, flush_notification_outbox
from .repository import RequestRepository
from .resolver import MediaResolver
from .service import AssistantService
from .templates import (
    DEFAULT_TEMPLATES,
    LEGACY_DEFAULT_TEMPLATES,
    REPLY_CATEGORY_STATUSES,
    ReplyTemplates,
)
from .xhs import XhsGateway
from .xhs_contracts import parse_authorized_ids


_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "enable_subscription": False,
    "notifications_enabled": True,
    "reply_enabled": False,
    "site": "xiaohongshu",
    "browser_mode": "embedded",
    "cdp_url": "",
    "cdp_token": "",
    "authorized_user_ids": "",
    "poll_interval_minutes": 2,
    "confidence_threshold": 0.85,
    **{f"reply_{category}_enabled": False for category in REPLY_CATEGORY_STATUSES},
    **{f"template_{key}": value for key, value in DEFAULT_TEMPLATES.items()},
}
_REPLY_CATEGORY_LABELS = {
    "success": "回复订阅成功",
    "existing": "回复已存在",
    "confirmation": "回复需人工确认",
    "failure": "回复处理失败",
}
_REQUEST_ACTION_STATUSES = frozenset(
    {"NEW", "FAILED", "NEED_CONFIRMATION", "DRY_RUN_MATCHED"}
)
_REPLY_ACTION_STATUSES = frozenset(
    {
        "SUBSCRIBED",
        "ALREADY_SUBSCRIBED",
        "ALREADY_IN_LIBRARY",
        "NEED_CONFIRMATION",
        "FAILED",
    }
)
_SITE_URLS = {
    "xiaohongshu": "https://www.xiaohongshu.com",
    "rednote": "https://www.rednote.com",
}
_JOIN_TIMEOUT_SECONDS = 2.0
_OPERATION_MESSAGES = {
    "AUTH_REQUIRED": "Authentication is required",
    "BROWSER_UNAVAILABLE": "Chromium is unavailable",
    "LOGIN_REQUIRED": "Login is required",
    "RATE_LIMITED": "The site rate limited this action",
    "SESSION_EXPIRED": "The browser session expired",
    "TEMPORARY_FAILURE": "The browser action temporarily failed",
    "UPSTREAM_ERROR": "The browser action failed",
    "XHS_RISK_CONTROL": "Site verification is required",
}
_AUTHORIZATION_HEADER = re.compile(
    r"\bauthorization\s*:\s*[^\r\n]*",
    re.IGNORECASE,
)
_COOKIE_HEADER = re.compile(r"\bcookie\s*:\s*[^\r\n]*", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"\b(?:authorization|cookie)\s*=\s*[^\r\n]*"
    r"|\b(?:xsec[_-]?token|api[_-]?(?:key|token))\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "rawnotification",
        "secret",
        "secretkey",
    }
)


def _serialized_management(operation: Callable[..., Any]) -> Callable[..., Any]:
    """Run one synchronous endpoint without overlapping background work."""

    @wraps(operation)
    def serialized(self: "XhsMovieAssistant", *args: Any, **kwargs: Any) -> Any:
        if not self._activity_lock.acquire(blocking=False):
            return self._failure("Another action is running")
        try:
            return operation(self, *args, **kwargs)
        finally:
            self._activity_lock.release()

    return serialized


class XhsMovieAssistant(_PluginBase):
    """Bind the domain services to MoviePilot's V2 plugin lifecycle."""

    plugin_name = "小红书影视助手"
    plugin_desc = "从授权账号的小红书 @ 请求识别影视作品并交给 MoviePilot 订阅。"
    plugin_icon = "xhsmovieassistant.png"
    plugin_version = "0.1.9"
    plugin_author = "s450586793"
    author_url = "https://github.com/s450586793/MoviePilot-XhsMovieAssistant"
    plugin_config_prefix = "xhsmovieassistant_"
    plugin_order = 30
    auth_level = 1

    def __init__(self) -> None:
        super().__init__()
        self._enabled = False
        self._enable_subscription = False
        self._notifications_enabled = True
        self._reply_enabled = False
        self._reply_categories = {
            category: False for category in REPLY_CATEGORY_STATUSES
        }
        self._site = "xiaohongshu"
        self._browser_mode = "embedded"
        self._cdp_url = ""
        self._cdp_token: str | None = None
        self._authorized_user_ids = frozenset()
        self._authorized_user_ids_raw = ""
        self._poll_interval_minutes = 2
        self._confidence_threshold = 0.85
        self._template_values = dict(DEFAULT_TEMPLATES)
        self._repository: RequestRepository | None = None
        self._browser: BrowserManager | None = None
        self._resolver: MediaResolver | None = None
        self._moviepilot: MoviePilotGateway | None = None
        self._service: AssistantService | None = None
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._generation = 0
        self._cached_status: dict[str, Any] = {
            "browser": "UNKNOWN",
            "chromium": "UNKNOWN",
            "chromium_code": None,
            "login": "UNKNOWN",
            "qrcode": None,
            "activity": "IDLE",
        }

    def init_plugin(self, config: dict[str, Any] | None = None) -> None:
        """Replace the current runtime with one built from validated config."""
        self.stop_service()
        if self._has_live_worker():
            self._cached_status["activity"] = "STOPPING"
            return
        generation, stop_event = self._begin_generation()
        values = dict(_DEFAULTS)
        if isinstance(config, Mapping):
            values.update(config)
        self._apply_config(values)
        try:
            self._repository = RequestRepository(Path(self.get_data_path()) / "app.db")
            if not self._enabled:
                return
            self._repository.recover_interrupted()
            self._build_runtime(generation, stop_event)
        except Exception:
            with self._worker_lock:
                if (
                    generation != self._generation
                    or stop_event is not self._stop_event
                    or stop_event.is_set()
                ):
                    return
                self._enabled = False
                self._cached_status["activity"] = "START_FAILED"
                self._clear_runtime(keep_repository=True)

    def get_state(self) -> bool:
        """Return the validated MoviePilot enable flag."""
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        """Register a durable fallback for lost WeChat input sessions."""
        return [
            {
                "cmd": "/xhs_confirm",
                "event": EventType.PluginAction,
                "desc": "确认小红书影视请求",
                "category": "订阅",
                "data": {"action": "xhs_confirm"},
            }
        ]

    @eventmanager.register(EventType.MessageAction)
    def handle_confirmation_input(self, event: Event) -> None:
        """Consume one MoviePilot-routed WeChat clarification."""
        data = getattr(event, "event_data", None)
        if not self._enabled or self._service is None or not isinstance(data, Mapping):
            return
        if data.get("plugin_id") != self.__class__.__name__:
            return
        if data.get("channel") is not MessageChannel.Wechat:
            return
        user_id = str(data.get("userid") or "").strip()
        source = str(data.get("source") or "").strip()
        if (user_id, source) not in self._wechat_confirmation_targets():
            return
        if not str(data.get("text") or "").startswith("plugin_input|"):
            return
        input_text = str(data.get("input_text") or "").strip()
        command = self._parse_confirmation_input_command(input_text)
        if input_text.split(maxsplit=1)[0:1] == ["/xhs_confirm"]:
            if command is not None:
                request_id, clarification = command
                self._confirm_request(
                    request_id,
                    clarification,
                    channel=MessageChannel.Wechat,
                    source=source,
                    user_id=user_id,
                )
            self._restore_consumed_confirmation_input(data, user_id, source)
            return
        payload = data.get("payload")
        if not isinstance(payload, Mapping):
            return
        request_id = payload.get("request_id")
        if (
            isinstance(request_id, bool)
            or not isinstance(request_id, int)
            or request_id < 1
            or not input_text
        ):
            return
        self._confirm_request(
            request_id,
            input_text,
            channel=MessageChannel.Wechat,
            source=source,
            user_id=user_id,
        )

    @eventmanager.register(EventType.PluginAction)
    def handle_confirmation_command(self, event: Event) -> None:
        """Handle `/xhs_confirm ID title/year/type` from WeChat admins."""
        data = getattr(event, "event_data", None)
        if not self._enabled or self._service is None or not isinstance(data, Mapping):
            return
        if data.get("action") != "xhs_confirm":
            return
        if data.get("channel") is not MessageChannel.Wechat:
            return
        user_id = str(data.get("user") or "").strip()
        source = str(data.get("source") or "").strip()
        if (user_id, source) not in self._wechat_confirmation_targets():
            return
        command = self._parse_confirmation_arguments(data.get("arg_str"))
        if command is None:
            return
        request_id, clarification = command
        self._confirm_request(
            request_id,
            clarification,
            channel=MessageChannel.Wechat,
            source=source,
            user_id=user_id,
        )

    @staticmethod
    def get_render_mode() -> tuple[str, str]:
        """Use MoviePilot's Vue federation host for configuration and detail pages."""
        return "vue", "dist/assets"

    def get_service(self) -> list[dict[str, Any]]:
        """Expose one interval service unless processing is disabled or paused."""
        if not self.get_state() or self._repository is None:
            return []
        try:
            if self._repository.get_runtime_state().browser_state is BrowserState.PAUSED:
                return []
        except Exception:
            return []
        return [
            {
                "id": "XhsMovieAssistantPoll",
                "name": "小红书影视请求轮询",
                "trigger": "interval",
                "func": self.poll_once,
                "kwargs": {"minutes": self._poll_interval_minutes},
            }
        ]

    def poll_once(self) -> bool:
        """Schedule one polling cycle without blocking MoviePilot's scheduler."""
        service = self._service
        if not self.get_state() or service is None or self._stop_event.is_set():
            return False
        return self._start_worker("poll", service.poll_once)

    def get_api(self) -> list[dict[str, Any]]:
        """Register the authenticated management API surface."""
        routes = (
            ("/chromium/install", self.install_chromium, "安装 Chromium"),
            ("/login/start", self.start_login, "生成登录二维码"),
            ("/logout", self.logout, "退出小红书登录"),
            ("/resume", self.resume, "恢复轮询"),
            ("/poll", self.poll, "立即轮询"),
            ("/requests/{request_id}/reprocess", self.reprocess, "重新处理请求"),
            ("/requests/{request_id}/ignore", self.ignore, "忽略请求"),
            ("/requests/{request_id}/manual", self.manual_resolve, "人工确认影视作品"),
            ("/requests/{request_id}/reply", self.reply, "补发小红书回复"),
            ("/test/ai", self.test_ai, "测试 AI 识别"),
            ("/test/moviepilot", self.test_moviepilot, "测试 MoviePilot 匹配"),
            ("/test/notification", self.test_notification, "测试 MoviePilot 通知"),
        )
        return [
            {
                "path": "/state",
                "endpoint": self.state,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "读取缓存状态和最近请求",
                "description": "读取缓存状态和最近请求",
            },
            *[
                {
                    "path": path,
                    "endpoint": endpoint,
                    "methods": ["POST"],
                    "auth": "bear",
                    "summary": summary,
                    "description": summary,
                }
                for path, endpoint, summary in routes
            ],
        ]

    def state(self, request: Request = None, apikey: str | None = None) -> Any:
        """Return cached health and durable rows without starting any runtime work."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        return schemas.Response(
            success=True,
            data=_sanitize_payload(
                {
                    "status": self._page_status(),
                    "requests": self._request_rows(),
                }
            ),
        )

    def get_form(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return MoviePilot's native Vuetify configuration schema."""
        controls: list[dict[str, Any]] = [
            _field("VSwitch", "启用轮询", "enabled", cols=6),
            _field("VSwitch", "允许真实订阅", "enable_subscription", cols=6),
            _field("VSwitch", "MoviePilot 通知", "notifications_enabled", cols=6),
            _field("VSwitch", "小红书公开回复", "reply_enabled", cols=6),
            *(
                _field(
                    "VSwitch",
                    label,
                    f"reply_{category}_enabled",
                    cols=3,
                )
                for category, label in _REPLY_CATEGORY_LABELS.items()
            ),
            _field(
                "VSelect",
                "站点",
                "site",
                cols=6,
                items=[
                    {"title": "小红书", "value": "xiaohongshu"},
                    {"title": "RedNote", "value": "rednote"},
                ],
            ),
            _field(
                "VSelect",
                "浏览器模式",
                "browser_mode",
                cols=6,
                items=[
                    {"title": "内置 Chromium", "value": "embedded"},
                    {"title": "CloakBrowser / CDP", "value": "cdp"},
                ],
            ),
            _field(
                "VTextField",
                "CloakBrowser CDP URL",
                "cdp_url",
                cols=8,
                placeholder="http://NAS-IP:9050/api/profiles/PROFILE-ID/cdp",
            ),
            _field(
                "VTextField",
                "CDP Access Token",
                "cdp_token",
                cols=4,
                type="password",
                autocomplete="new-password",
            ),
            _field(
                "VTextField",
                "轮询间隔（分钟）",
                "poll_interval_minutes",
                cols=3,
                type="number",
                min=1,
                max=10,
            ),
            _field(
                "VTextField",
                "AI 置信度阈值",
                "confidence_threshold",
                cols=3,
                type="number",
                min=0,
                max=1,
                step=0.05,
            ),
            _field(
                "VTextarea",
                "授权用户 ID（逗号或换行分隔）",
                "authorized_user_ids",
                cols=12,
                rows=3,
            ),
        ]
        controls.extend(
            _field(
                "VTextarea",
                f"{status} 回复模板",
                f"template_{status}",
                cols=12,
                rows=2,
            )
            for status in DEFAULT_TEMPLATES
        )
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": controls,
                    }
                ],
            }
        ], dict(_DEFAULTS)

    def get_page(self) -> list[dict[str, Any]]:
        """Render cached health and durable request summaries without I/O."""
        status = self._page_status()
        rows = self._request_rows()
        request_actions = self._request_actions(rows)
        paused = status.get("browser") == BrowserState.PAUSED.value
        alert_type = "warning" if paused else ("success" if self.get_state() else "info")
        qr_source = status.get("qrcode") or ""
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": alert_type,
                    "variant": "tonal",
                    "text": self._status_text(status),
                },
            },
            {
                "component": "VRow",
                "props": {"class": "mt-3"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VImg",
                                "props": {
                                    "src": qr_source,
                                    "width": 240,
                                    "height": 240,
                                    "cover": False,
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 8},
                        "content": [
                            _action_button("安装 Chromium", "mdi-download", "/chromium/install"),
                            _action_button("生成登录二维码", "mdi-qrcode-scan", "/login/start"),
                            _action_button("立即轮询", "mdi-refresh", "/poll"),
                            _action_button("恢复轮询", "mdi-play", "/resume"),
                            _action_button("退出登录", "mdi-logout", "/logout"),
                            _action_button(
                                "测试 AI",
                                "mdi-robot-outline",
                                "/test/ai",
                                params={"title": "星际穿越"},
                            ),
                            _action_button(
                                "测试 MoviePilot",
                                "mdi-movie-search-outline",
                                "/test/moviepilot",
                                params={"title": "星际穿越", "media_type": "movie"},
                            ),
                            _action_button(
                                "测试通知",
                                "mdi-bell-check-outline",
                                "/test/notification",
                            ),
                        ],
                    },
                ],
            },
            {
                "component": "VDataTable",
                "props": {
                    "density": "compact",
                    "items-per-page": 20,
                    "headers": [
                        {"title": "ID", "key": "id"},
                        {"title": "状态", "key": "status"},
                        {"title": "影视作品", "key": "title"},
                        {"title": "类型", "key": "media_type"},
                        {"title": "年份", "key": "year"},
                        {"title": "季", "key": "season"},
                        {"title": "匹配", "key": "match"},
                        {"title": "订阅 ID", "key": "subscription_id"},
                        {"title": "回复", "key": "reply"},
                        {"title": "错误", "key": "error"},
                        {"title": "更新时间", "key": "updated_at"},
                    ],
                    "items": rows,
                },
            },
            *(
                [
                    {
                        "component": "VDivider",
                        "props": {"class": "my-3"},
                    },
                    {
                        "component": "div",
                        "props": {"class": "d-flex flex-column ga-1"},
                        "content": request_actions,
                    },
                ]
                if request_actions
                else []
            ),
        ]

    def install_chromium(
        self, request: Request = None, apikey: str | None = None
    ) -> Any:
        """Start a private Chromium install in the plugin worker."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        try:
            browser = self._ensure_browser()
            started = self._start_worker("chromium_install", browser.install_chromium)
        except Exception:
            return self._failure("Chromium installation could not be started")
        return schemas.Response(
            success=started,
            message="Chromium installation started" if started else "Another action is running",
        )

    @_serialized_management
    def start_login(
        self, request: Request = None, apikey: str | None = None
    ) -> Any:
        """Return an authenticated QR payload without exposing an image route."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        try:
            browser = self._ensure_browser()
            login = browser.check_login()
            if login.success:
                self._cached_status.update(login="LOGGED_IN", qrcode=None)
                return schemas.Response(success=True, data={"login": "LOGGED_IN"})
            if login.code != "LOGIN_REQUIRED":
                return self._operation_response(login)
            result = browser.capture_login_qrcode()
            if not result.success or not isinstance(result.data, bytes):
                return self._operation_response(result)
            qrcode = "data:image/png;base64," + base64.b64encode(result.data).decode("ascii")
            self._cached_status.update(login="WAITING_FOR_SCAN", qrcode=qrcode)
            return schemas.Response(success=True, data={"qrcode": qrcode})
        except Exception:
            return self._failure("Login QR code could not be generated")

    @_serialized_management
    def logout(self, request: Request = None, apikey: str | None = None) -> Any:
        """Explicitly clear the persistent browser session."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        try:
            response = self._operation_response(self._ensure_browser().logout())
            if response.success:
                self._cached_status.update(login="LOGGED_OUT", qrcode=None)
            return response
        except Exception:
            return self._failure("Logout failed")

    @_serialized_management
    def resume(self, request: Request = None, apikey: str | None = None) -> Any:
        """Clear the durable pause state through the service boundary."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        if self._service is None:
            return self._failure("Plugin runtime is unavailable")
        try:
            self._service.resume()
            self._cached_status["browser"] = BrowserState.READY.value
            return schemas.Response(success=True, message="Polling resumed")
        except Exception:
            return self._failure("Polling could not be resumed")

    def poll(self, request: Request = None, apikey: str | None = None) -> Any:
        """Request an immediate non-blocking polling cycle."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        try:
            started = self.poll_once()
        except Exception:
            return self._failure("Polling could not be started")
        return schemas.Response(
            success=started,
            message="Polling started" if started else "Polling is unavailable or already running",
        )

    @_serialized_management
    def reprocess(
        self,
        request_id: int,
        request: Request = None,
        apikey: str | None = None,
    ) -> Any:
        """Reprocess one durable request through the application service."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        return self._service_action("reprocess", request_id)

    @_serialized_management
    def ignore(
        self,
        request_id: int,
        request: Request = None,
        apikey: str | None = None,
    ) -> Any:
        """Mark one durable request ignored through the application service."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        return self._service_action("ignore", request_id)

    @_serialized_management
    def manual_resolve(
        self,
        request_id: int,
        body: dict[str, Any] | None = None,
        request: Request = None,
        apikey: str | None = None,
    ) -> Any:
        """Apply an authenticated, strictly validated manual resolution."""
        if not self._authorized(request, apikey, body):
            return self._unauthorized()
        if self._service is None:
            return self._failure("Plugin runtime is unavailable")
        try:
            resolution = _manual_resolution(body)
            result = self._service.manual_resolve(request_id, resolution)
            return schemas.Response(
                success=True,
                data=_sanitize_payload(_result_data(result)),
            )
        except (TypeError, ValueError):
            return self._failure("Manual resolution is invalid")
        except Exception:
            return self._failure("Request could not be manually resolved")

    @_serialized_management
    def reply(
        self,
        request_id: int,
        request: Request = None,
        apikey: str | None = None,
    ) -> Any:
        """Retry one pending public reply through the service boundary."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        return self._service_action("reply", request_id)

    @_serialized_management
    def test_ai(
        self,
        body: dict[str, Any] | None = None,
        request: Request = None,
        apikey: str | None = None,
    ) -> Any:
        """Run a safe synthetic request through the configured resolver."""
        if not self._authorized(request, apikey, body):
            return self._unauthorized()
        if self._resolver is None:
            return self._failure("Plugin runtime is unavailable")
        try:
            media_request = _diagnostic_request(body)
            resolution = self._resolver.resolve(media_request)
            return schemas.Response(
                success=True,
                data=_sanitize_payload(resolution.model_dump(mode="json")),
            )
        except (TypeError, ValueError):
            return self._failure("AI test input is invalid")
        except Exception:
            return self._failure("AI test failed")

    @_serialized_management
    def test_moviepilot(
        self,
        body: dict[str, Any] | None = None,
        request: Request = None,
        apikey: str | None = None,
    ) -> Any:
        """Run deterministic MoviePilot matching without creating a subscription."""
        if not self._authorized(request, apikey, body):
            return self._unauthorized()
        if self._moviepilot is None:
            return self._failure("Plugin runtime is unavailable")
        try:
            decision = self._moviepilot.match(_manual_resolution(body))
            data = {"reason_code": decision.reason_code}
            if decision.match is not None:
                data["match"] = decision.match.model_dump(mode="json")
            return schemas.Response(success=True, data=_sanitize_payload(data))
        except (TypeError, ValueError):
            return self._failure("MoviePilot test input is invalid")
        except Exception:
            return self._failure("MoviePilot test failed")

    @_serialized_management
    def test_notification(
        self, request: Request = None, apikey: str | None = None
    ) -> Any:
        """Send a diagnostic through MoviePilot's configured notification channels."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        try:
            self._notify("小红书影视助手", "通知测试成功。")
            return schemas.Response(success=True, message="Notification submitted")
        except Exception:
            return self._failure("Notification test failed")

    def stop_service(self) -> None:
        """Bound shutdown and release active resources without deleting the Profile."""
        with self._worker_lock:
            self._enabled = False
            self._stop_event.set()
            worker = self._worker
            browser = self._browser
            self._clear_runtime(keep_repository=False)
        if browser is not None:
            try:
                close_active_context = getattr(browser, "close_active_context", None)
                if callable(close_active_context):
                    close_active_context()
                else:
                    context = getattr(browser, "_active_context", None)
                    if context is not None:
                        context.close()
            except Exception:
                pass
        if worker is not None and worker is not threading.current_thread():
            try:
                worker.join(timeout=_JOIN_TIMEOUT_SECONDS)
            except (RuntimeError, TypeError):
                pass
        with self._worker_lock:
            if self._worker is worker and not _worker_is_alive(worker):
                self._worker = None

    def _apply_config(self, values: Mapping[str, Any]) -> None:
        self._enable_subscription = _as_bool(values.get("enable_subscription"), False)
        self._notifications_enabled = _as_bool(values.get("notifications_enabled"), True)
        self._reply_enabled = _as_bool(values.get("reply_enabled"), False)
        self._reply_categories = {
            category: _as_bool(values.get(f"reply_{category}_enabled"), False)
            for category in REPLY_CATEGORY_STATUSES
        }
        site = str(values.get("site") or "").strip()
        self._site = site if site in _SITE_URLS else "xiaohongshu"
        browser_mode = str(values.get("browser_mode") or "").strip()
        self._browser_mode = (
            browser_mode if browser_mode in {"embedded", "cdp"} else "embedded"
        )
        self._cdp_url = str(values.get("cdp_url") or "").strip()
        self._cdp_token = str(values.get("cdp_token") or "").strip() or None
        self._cached_status["browser_mode"] = self._browser_mode.upper()
        raw_ids = values.get("authorized_user_ids")
        self._authorized_user_ids_raw = raw_ids if isinstance(raw_ids, str) else ""
        self._authorized_user_ids = parse_authorized_ids(raw_ids)
        self._poll_interval_minutes = int(
            _clamp(_number(values.get("poll_interval_minutes"), 2), 1, 10)
        )
        self._confidence_threshold = _clamp(
            _number(values.get("confidence_threshold"), 0.85), 0, 1
        )
        self._template_values = {
            status: (
                default
                if value == LEGACY_DEFAULT_TEMPLATES.get(status)
                else value
            )
            for status, default in DEFAULT_TEMPLATES.items()
            if isinstance(
                (value := values.get(f"template_{status}", default)), str
            )
        }
        requested = _as_bool(values.get("enabled"), False)
        self._enabled = bool(requested and self._authorized_user_ids)

    def _build_runtime(
        self,
        generation: int,
        stop_event: threading.Event,
    ) -> None:
        repository = self._repository
        if repository is None:
            raise RuntimeError("repository is unavailable")
        browser = BrowserManager(
            self.get_data_path(),
            self._site,
            getattr(settings, "PROXY_SERVER", None),
            browser_mode=self._browser_mode,
            cdp_url=self._cdp_url,
            cdp_token=self._cdp_token,
        )
        resolver = MediaResolver()
        moviepilot = MoviePilotGateway()
        xhs = XhsGateway(browser, replies_enabled=self._reply_enabled)
        service = AssistantService(
            repository=repository,
            xhs=xhs,
            resolver=resolver,
            moviepilot=moviepilot,
            authorized_user_ids=self._authorized_user_ids,
            notify=self._notify,
            site_url=_SITE_URLS[self._site],
            enable_subscription=self._enable_subscription,
            confidence_threshold=self._confidence_threshold,
            replies_enabled=self._reply_enabled,
            notifications_enabled=self._notifications_enabled,
            templates=ReplyTemplates(
                self._template_values,
                enabled_categories=self._reply_categories,
            ),
            request_confirmation=self._arm_wechat_confirmation,
            is_cancelled=lambda: (
                stop_event.is_set() or generation != self._generation
            ),
        )
        with self._worker_lock:
            if (
                generation != self._generation
                or stop_event is not self._stop_event
                or stop_event.is_set()
                or _worker_is_alive(self._worker)
            ):
                raise RuntimeError("plugin generation stopped during runtime build")
            self._browser = browser
            self._resolver = resolver
            self._moviepilot = moviepilot
            self._service = service
            chromium = (
                "EXTERNAL"
                if self._browser_mode == "cdp"
                else self._cached_status.get("chromium", "UNKNOWN")
            )
            self._cached_status.update(
                browser=BrowserState.READY.value,
                chromium=chromium,
                activity="IDLE",
            )

    def _ensure_browser(self) -> BrowserManager:
        if self._browser is None:
            self._browser = BrowserManager(
                self.get_data_path(),
                self._site,
                getattr(settings, "PROXY_SERVER", None),
                browser_mode=self._browser_mode,
                cdp_url=self._cdp_url,
                cdp_token=self._cdp_token,
            )
            if self._browser_mode == "cdp":
                self._cached_status["chromium"] = "EXTERNAL"
        return self._browser

    def _start_worker(self, activity: str, operation: Callable[[], Any]) -> bool:
        with self._worker_lock:
            if _worker_is_alive(self._worker):
                return False
            self._worker = None
            if self._stop_event.is_set():
                return False
            if not self._activity_lock.acquire(blocking=False):
                return False
            generation = self._generation
            stop_event = self._stop_event

            def run() -> None:
                try:
                    if activity == "poll" and stop_event.is_set():
                        return
                    self._cached_status["activity"] = activity.upper()
                    outcome = operation()
                    if (
                        generation == self._generation
                        and isinstance(outcome, OperationResult)
                        and activity == "chromium_install"
                    ):
                        chromium = "UNAVAILABLE"
                        if outcome.success:
                            chromium = (
                                "EXTERNAL"
                                if outcome.data == "EXTERNAL"
                                else "AVAILABLE"
                            )
                        self._cached_status.update(
                            chromium=chromium,
                            chromium_code=(
                                None
                                if outcome.success
                                else outcome.code or "BROWSER_UNAVAILABLE"
                            ),
                        )
                except Exception:
                    if generation == self._generation:
                        self._cached_status["activity"] = "FAILED"
                        if activity == "chromium_install":
                            self._cached_status.update(
                                chromium="UNAVAILABLE",
                                chromium_code="BROWSER_UNAVAILABLE",
                            )
                finally:
                    if (
                        generation == self._generation
                        and self._cached_status.get("activity") != "FAILED"
                    ):
                        self._cached_status["activity"] = "IDLE"
                    self._activity_lock.release()

            worker = threading.Thread(
                target=run,
                name=f"xhsmovieassistant-{activity}",
                daemon=True,
            )
            self._worker = worker
            try:
                worker.start()
            except Exception:
                self._worker = None
                self._activity_lock.release()
                raise
            return True

    def _has_live_worker(self) -> bool:
        with self._worker_lock:
            return _worker_is_alive(self._worker)

    def _begin_generation(self) -> tuple[int, threading.Event]:
        with self._worker_lock:
            if _worker_is_alive(self._worker):
                raise RuntimeError("previous worker is still running")
            self._worker = None
            self._generation += 1
            self._stop_event = threading.Event()
            return self._generation, self._stop_event

    def _service_action(self, method: str, request_id: int) -> Any:
        if self._service is None:
            return self._failure("Plugin runtime is unavailable")
        try:
            result = getattr(self._service, method)(request_id)
            return schemas.Response(
                success=True,
                data=_sanitize_payload(_result_data(result)),
            )
        except (TypeError, ValueError):
            return self._failure("Request action is invalid")
        except Exception:
            return self._failure("Request action failed")

    def _authorized(
        self,
        request: Request | None,
        apikey: str | None,
        body: Mapping[str, Any] | None = None,
    ) -> bool:
        if (
            Request is not Any
            and isinstance(request, Request)
            and getattr(request, "scope", {}).get("route") is not None
        ):
            return True
        expected = str(getattr(settings, "API_TOKEN", "") or "").strip()
        actual = _extract_credential(request, apikey, body)
        matches = hmac.compare_digest(actual, expected)
        return bool(actual and expected and matches)

    @staticmethod
    def _unauthorized() -> Any:
        return schemas.Response(success=False, message="API authentication failed")

    @staticmethod
    def _failure(message: str) -> Any:
        return schemas.Response(success=False, message=message)

    def _operation_response(self, result: OperationResult) -> Any:
        self._pause_for_browser_outcome(result)
        code = _public_operation_code(result.code)
        data = {"code": code} if code else {}
        message = (
            "Browser operation completed"
            if result.success
            else _OPERATION_MESSAGES.get(code, "Browser operation failed")
        )
        return schemas.Response(success=result.success, message=message, data=data)

    def _pause_for_browser_outcome(self, result: OperationResult) -> None:
        if not result.should_pause or self._repository is None:
            return
        code = _public_operation_code(result.code) or "UPSTREAM_ERROR"
        try:
            enqueue_pause_notification(self._repository, code)
            flush_notification_outbox(
                self._repository,
                self._notify,
                business_enabled=self._notifications_enabled,
                is_cancelled=self._stop_event.is_set,
            )
        except Exception:
            return
        self._cached_status.update(browser=BrowserState.PAUSED.value, pause_code=code)

    def _notify(self, title: str, text: str) -> None:
        self.post_message(mtype=NotificationType.Plugin, title=title, text=text)

    def _arm_wechat_confirmation(
        self,
        request_id: int,
        title: str = "小红书影视助手",
        text: str = "请直接回复明确的片名、年份和电影/剧集。",
    ) -> None:
        """Prompt each admin and only then arm that admin's next text."""
        for user_id, source in sorted(self._wechat_confirmation_targets()):
            try:
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title=title,
                    text=text,
                    channel=MessageChannel.Wechat,
                    source=source,
                    userid=user_id,
                )
            except Exception:
                continue
            self._create_wechat_confirmation_input(request_id, user_id, source)

    @staticmethod
    def _parse_confirmation_arguments(value: Any) -> tuple[int, str] | None:
        parts = str(value or "").strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].strip():
            return None
        request_id = int(parts[0])
        if request_id < 1:
            return None
        return request_id, parts[1].strip()

    @classmethod
    def _parse_confirmation_input_command(cls, value: str) -> tuple[int, str] | None:
        parts = value.split(maxsplit=1)
        if not parts or parts[0] != "/xhs_confirm":
            return None
        return cls._parse_confirmation_arguments(parts[1] if len(parts) == 2 else "")

    def _restore_consumed_confirmation_input(
        self, data: Mapping[str, Any], user_id: str, source: str
    ) -> None:
        payload = data.get("payload")
        if not isinstance(payload, Mapping):
            return
        request_id = payload.get("request_id")
        if (
            isinstance(request_id, bool)
            or not isinstance(request_id, int)
            or request_id < 1
            or self._repository is None
        ):
            return
        try:
            stored = self._repository.get(request_id)
        except Exception:
            return
        if stored is not None and stored.status is RequestStatus.NEED_CONFIRMATION:
            self._create_wechat_confirmation_input(request_id, user_id, source)

    def _create_wechat_confirmation_input(
        self, request_id: int, user_id: str, source: str
    ) -> None:
        try:
            plugin_input_interaction_manager.create_or_replace(
                user_id=user_id,
                plugin_id=self.__class__.__name__,
                channel=MessageChannel.Wechat,
                source=source,
                username=None,
                timeout_seconds=24 * 60 * 60,
                payload={"request_id": request_id},
            )
        except Exception:
            pass

    def _confirm_request(
        self,
        request_id: int,
        clarification: str,
        *,
        channel: MessageChannel,
        source: str,
        user_id: str,
    ) -> None:
        """Serialize one clarification and report only a generic failure."""
        service = self._service
        if service is None:
            return
        try:
            with self._activity_lock:
                service.confirm_from_text(request_id, clarification)
        except Exception:
            try:
                self.post_message(
                    channel=channel,
                    source=source,
                    userid=user_id,
                    title="小红书影视确认失败",
                    text=(
                        f"请求 #{request_id} 未能完成确认。"
                        "请使用兜底命令重试，或在插件页人工确认。"
                    ),
                )
            except Exception:
                pass

    @staticmethod
    def _wechat_confirmation_targets() -> frozenset[tuple[str, str]]:
        """Return enabled WeChat admin/source pairs, failing closed."""
        try:
            module = ModuleManager().get_running_module("WechatModule")
            configs = module.get_configs() if module is not None else {}
        except Exception:
            return frozenset()
        if not isinstance(configs, Mapping):
            return frozenset()

        targets: set[tuple[str, str]] = set()
        for key, item in configs.items():
            source = str(getattr(item, "name", None) or key or "").strip()
            config = getattr(item, "config", None)
            if not source or not isinstance(config, Mapping):
                continue
            raw_admins = str(config.get("WECHAT_ADMINS") or "").replace("\n", ",")
            targets.update(
                (admin, source)
                for value in raw_admins.split(",")
                if (admin := value.strip())
            )
        return frozenset(targets)

    def _request_rows(self) -> list[dict[str, Any]]:
        if self._repository is None:
            return []
        try:
            requests = self._repository.recent(20)
        except Exception:
            return []
        return [
            {
                "id": item.id,
                "status": item.status.value,
                "title": item.title or "-",
                "original_title": item.original_title or "-",
                "media_type": item.media_type or "-",
                "year": item.year or "-",
                "season": item.season or "-",
                "match": _match_text(item),
                "subscription_id": item.subscription_id or "-",
                "reply": _reply_text(item),
                "error": item.error or "-",
                "updated_at": item.updated_at.isoformat(timespec="seconds"),
            }
            for item in requests
        ]

    def _request_actions(
        self, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        actions = []
        for row in rows:
            manageable = row["status"] in _REQUEST_ACTION_STATUSES
            replyable = (
                row["status"] in _REPLY_ACTION_STATUSES
                and row["reply"] == "PENDING"
            )
            if not manageable and not replyable:
                continue
            request_id = int(row["id"])
            manual_params = {
                "title": "" if row["title"] == "-" else row["title"],
                "media_type": (
                    row["media_type"]
                    if row["media_type"] in {"movie", "tv"}
                    else "unknown"
                ),
                "year": None if row["year"] == "-" else row["year"],
                "season": None if row["season"] == "-" else row["season"],
            }
            actions.append(
                {
                    "component": "div",
                    "props": {
                        "class": "d-flex flex-wrap align-center py-1",
                    },
                    "content": [
                        {
                            "component": "span",
                            "props": {
                                "class": "text-body-2 font-weight-medium mr-2",
                            },
                            "text": f"#{request_id} {row['title']}",
                        },
                        *(
                            [
                                _action_button(
                                    "重新处理",
                                    "mdi-replay",
                                    f"/requests/{request_id}/reprocess",
                                    size="small",
                                ),
                                _action_button(
                                    "忽略",
                                    "mdi-eye-off-outline",
                                    f"/requests/{request_id}/ignore",
                                    size="small",
                                ),
                                _action_button(
                                    "人工确认",
                                    "mdi-check-decagram-outline",
                                    f"/requests/{request_id}/manual",
                                    params=manual_params,
                                    size="small",
                                ),
                            ]
                            if manageable
                            else []
                        ),
                        *(
                            [
                                _action_button(
                                    "补发小红书回复",
                                    "mdi-reply",
                                    f"/requests/{request_id}/reply",
                                    size="small",
                                )
                            ]
                            if replyable
                            else []
                        ),
                    ],
                }
            )
        return actions

    def _page_status(self) -> dict[str, Any]:
        status = dict(self._cached_status)
        status.setdefault("chromium", "UNKNOWN")
        status.setdefault("chromium_code", None)
        if self._repository is None:
            return status
        try:
            runtime = self._repository.get_runtime_state()
            status["browser"] = runtime.browser_state.value
            status["pause_code"] = runtime.pause_code
        except Exception:
            pass
        return status

    def _status_text(self, status: Mapping[str, Any]) -> str:
        state = "已启用" if self.get_state() else "未启用"
        browser = str(status.get("browser") or "UNKNOWN")
        chromium = str(status.get("chromium") or "UNKNOWN")
        chromium_code = str(status.get("chromium_code") or "")
        login = str(status.get("login") or "UNKNOWN")
        activity = str(status.get("activity") or "IDLE")
        pause = str(status.get("pause_code") or "")
        chromium_text = f"Chromium: {chromium}"
        if chromium_code:
            chromium_text = f"{chromium_text} ({chromium_code})"
        details = (
            f"{state} | Browser: {browser} | {chromium_text} | "
            f"Login: {login} | Activity: {activity}"
        )
        return f"{details} | Pause: {pause}" if pause else details

    def _clear_runtime(self, *, keep_repository: bool) -> None:
        self._browser = None
        self._resolver = None
        self._moviepilot = None
        self._service = None
        if not keep_repository:
            self._repository = None


def _field(
    component: str,
    label: str,
    model: str,
    *,
    cols: int,
    **props: Any,
) -> dict[str, Any]:
    return {
        "component": "VCol",
        "props": {"cols": 12, "md": cols},
        "content": [
            {
                "component": component,
                "props": {"label": label, "model": model, **props},
            }
        ],
    }


def _action_button(
    text: str,
    icon: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    size: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "api": f"plugin/XhsMovieAssistant{path}",
        "method": "post",
    }
    if params is not None:
        event["params"] = dict(params)
    props = {
        "prepend-icon": icon,
        "variant": "tonal",
        "class": "ma-1",
    }
    if size is not None:
        props["size"] = size
    return {
        "component": "VBtn",
        "props": props,
        "text": text,
        "events": {"click": event},
    }


def _match_text(item: Any) -> str:
    source = str(item.media_source or "")
    source_id = str(item.media_source_id or "")
    if not source or not source_id:
        return "-"
    score = item.score
    suffix = (
        f" ({score:.2f})"
        if isinstance(score, (int, float)) and not isinstance(score, bool)
        else ""
    )
    return f"{source}:{source_id}{suffix}"


def _reply_text(item: Any) -> str:
    status = item.reply_status.value
    return f"{status}:{item.reply_id}" if item.reply_id else status


def _extract_credential(
    request: Request | None,
    explicit: str | None,
    body: Mapping[str, Any] | None,
) -> str:
    if explicit:
        return str(explicit).strip()
    if request is not None:
        headers = getattr(request, "headers", {})
        direct = str(headers.get("X-API-Key") or "").strip()
        if direct:
            return direct
        authorization = str(headers.get("Authorization") or "").strip()
        if authorization.casefold().startswith("bearer "):
            return authorization.split(" ", 1)[1].strip()
        query = getattr(request, "query_params", {})
        direct = str(query.get("apikey") or query.get("token") or "").strip()
        if direct:
            return direct
    if body:
        for key in ("apikey", "api_key", "token"):
            if body.get(key):
                return str(body[key]).strip()
    return ""


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


def _manual_resolution(body: Mapping[str, Any] | None) -> Resolution:
    values = body or {}
    return Resolution(
        status="resolved",
        title=str(values.get("title") or "").strip(),
        original_title=str(values.get("original_title") or "").strip(),
        media_type=str(values.get("media_type") or "unknown").strip().casefold(),
        year=_optional_int(values.get("year")),
        season=_optional_int(values.get("season")),
        confidence=1.0,
        reason="MANUAL",
    )


def _diagnostic_request(body: Mapping[str, Any] | None) -> MediaRequest:
    values = body or {}
    title = str(values.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    return MediaRequest(
        request_id="diagnostic",
        source="xiaohongshu",
        intent="subscribe",
        trigger_comment="想看",
        note=NoteContext(
            id="diagnostic",
            url="https://www.xiaohongshu.com/explore/diagnostic",
            title=title,
        ),
    )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    return int(value)


def _result_data(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    status = getattr(result, "status", None)
    return {
        "id": getattr(result, "id", None),
        "status": getattr(status, "value", status),
    }


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_payload(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str):
        sanitized = _AUTHORIZATION_HEADER.sub("[redacted]", value)
        sanitized = _COOKIE_HEADER.sub("[redacted]", sanitized)
        sanitized = _SENSITIVE_ASSIGNMENT.sub("[redacted]", sanitized)
        api_token = str(getattr(settings, "API_TOKEN", "") or "")
        if api_token:
            sanitized = sanitized.replace(api_token, "[redacted]")
        return sanitized
    return value


def _public_operation_code(value: Any) -> str | None:
    if isinstance(value, str) and value in _OPERATION_MESSAGES:
        return value
    return "UPSTREAM_ERROR" if value else None


def _is_sensitive_key(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.startswith("cookie")
        or normalized.endswith(("apikey", "password", "secret", "token"))
    )


def _worker_is_alive(worker: Any) -> bool:
    if worker is None:
        return False
    try:
        return bool(worker.is_alive())
    except (AttributeError, RuntimeError, TypeError):
        return False

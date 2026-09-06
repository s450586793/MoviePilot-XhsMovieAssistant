"""MoviePilot V2 entrypoint for the Xiaohongshu movie assistant."""

from __future__ import annotations

import base64
import hmac
import re
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from fastapi import Request
except ImportError:  # MoviePilot provides FastAPI; isolated plugin tests do not.
    Request = Any

from app import schemas
from app.core.config import settings
from app.plugins import _PluginBase
from app.schemas import NotificationType

from .browser import BrowserManager, OperationResult
from .models import BrowserState, MediaRequest, NoteContext, Resolution
from .moviepilot import MoviePilotGateway
from .repository import RequestRepository
from .resolver import MediaResolver
from .service import AssistantService
from .templates import DEFAULT_TEMPLATES, ReplyTemplates
from .xhs import XhsGateway
from .xhs_contracts import parse_authorized_ids


_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "enable_subscription": False,
    "notifications_enabled": True,
    "reply_enabled": False,
    "site": "xiaohongshu",
    "authorized_user_ids": "",
    "poll_interval_minutes": 2,
    "confidence_threshold": 0.85,
    **{f"template_{key}": value for key, value in DEFAULT_TEMPLATES.items()},
}
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


class XhsMovieAssistant(_PluginBase):
    """Bind the domain services to MoviePilot's V2 plugin lifecycle."""

    plugin_name = "小红书影视助手"
    plugin_desc = "从授权账号的小红书 @ 请求识别影视作品并交给 MoviePilot 订阅。"
    plugin_icon = "xhsmovieassistant.png"
    plugin_version = "0.1.0"
    plugin_author = "community"
    author_url = ""
    plugin_config_prefix = "xhsmovieassistant_"
    plugin_order = 30
    auth_level = 1

    def __init__(self) -> None:
        super().__init__()
        self._enabled = False
        self._enable_subscription = False
        self._notifications_enabled = True
        self._reply_enabled = False
        self._site = "xiaohongshu"
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
        self._stop_event = threading.Event()
        self._generation = 0
        self._cached_status: dict[str, Any] = {
            "browser": "UNKNOWN",
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
            ("/test/ai", self.test_ai, "测试 AI 识别"),
            ("/test/moviepilot", self.test_moviepilot, "测试 MoviePilot 匹配"),
            ("/test/notification", self.test_notification, "测试 MoviePilot 通知"),
        )
        return [
            {
                "path": path,
                "endpoint": endpoint,
                "methods": ["POST"],
                "auth": "apikey",
                "summary": summary,
                "description": summary,
            }
            for path, endpoint, summary in routes
        ]

    def get_form(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return MoviePilot's native Vuetify configuration schema."""
        controls: list[dict[str, Any]] = [
            _field("VSwitch", "启用轮询", "enabled", cols=6),
            _field("VSwitch", "允许真实订阅", "enable_subscription", cols=6),
            _field("VSwitch", "MoviePilot 通知", "notifications_enabled", cols=6),
            _field("VSwitch", "小红书公开回复", "reply_enabled", cols=6),
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
                        {"title": "年份", "key": "year"},
                        {"title": "更新时间", "key": "updated_at"},
                    ],
                    "items": rows,
                },
            },
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

    def start_login(
        self, request: Request = None, apikey: str | None = None
    ) -> Any:
        """Return an authenticated QR payload without exposing an image route."""
        if not self._authorized(request, apikey):
            return self._unauthorized()
        try:
            result = self._ensure_browser().capture_login_qrcode()
            if not result.success or not isinstance(result.data, bytes):
                return self._operation_response(result)
            qrcode = "data:image/png;base64," + base64.b64encode(result.data).decode("ascii")
            self._cached_status.update(login="WAITING_FOR_SCAN", qrcode=qrcode)
            return schemas.Response(success=True, data={"qrcode": qrcode})
        except Exception:
            return self._failure("Login QR code could not be generated")

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
            context = getattr(browser, "_active_context", None)
            if context is not None:
                try:
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
        site = str(values.get("site") or "").strip()
        self._site = site if site in _SITE_URLS else "xiaohongshu"
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
            status: value
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
            templates=ReplyTemplates(self._template_values),
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
            self._cached_status.update(browser=BrowserState.READY.value, activity="IDLE")

    def _ensure_browser(self) -> BrowserManager:
        if self._browser is None:
            self._browser = BrowserManager(
                self.get_data_path(),
                self._site,
                getattr(settings, "PROXY_SERVER", None),
            )
        return self._browser

    def _start_worker(self, activity: str, operation: Callable[[], Any]) -> bool:
        with self._worker_lock:
            if _worker_is_alive(self._worker):
                return False
            self._worker = None
            if self._stop_event.is_set():
                return False
            generation = self._generation
            stop_event = self._stop_event

            def run() -> None:
                if activity == "poll" and stop_event.is_set():
                    return
                self._cached_status["activity"] = activity.upper()
                try:
                    outcome = operation()
                    if (
                        generation == self._generation
                        and isinstance(outcome, OperationResult)
                    ):
                        self._cached_status["browser"] = (
                            BrowserState.READY.value if outcome.success else outcome.code or "ERROR"
                        )
                except Exception:
                    if generation == self._generation:
                        self._cached_status["activity"] = "FAILED"
                finally:
                    if (
                        generation == self._generation
                        and self._cached_status.get("activity") != "FAILED"
                    ):
                        self._cached_status["activity"] = "IDLE"

            worker = threading.Thread(
                target=run,
                name=f"xhsmovieassistant-{activity}",
                daemon=True,
            )
            self._worker = worker
            worker.start()
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
            state = self._repository.get_runtime_state()
            if state.browser_state is BrowserState.PAUSED and state.pause_notified:
                return
            self._repository.set_runtime_state(
                BrowserState.PAUSED,
                pause_code=code,
                paused_at=datetime.now(timezone.utc),
                pause_notified=True,
            )
        except Exception:
            return
        self._cached_status.update(browser=BrowserState.PAUSED.value, pause_code=code)
        try:
            self._notify("小红书监听已暂停", f"暂停原因：{code}")
        except Exception:
            pass

    def _notify(self, title: str, text: str) -> None:
        self.post_message(mtype=NotificationType.Plugin, title=title, text=text)

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
                "year": item.year or "-",
                "updated_at": item.updated_at.isoformat(timespec="seconds"),
            }
            for item in requests
        ]

    def _page_status(self) -> dict[str, Any]:
        status = dict(self._cached_status)
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
        login = str(status.get("login") or "UNKNOWN")
        activity = str(status.get("activity") or "IDLE")
        pause = str(status.get("pause_code") or "")
        details = f"{state} | Browser: {browser} | Login: {login} | Activity: {activity}"
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


def _action_button(text: str, icon: str, path: str) -> dict[str, Any]:
    return {
        "component": "VBtn",
        "props": {
            "prepend-icon": icon,
            "variant": "tonal",
            "class": "ma-1",
        },
        "text": text,
        "events": {
            "click": {
                "api": f"plugin/XhsMovieAssistant{path}",
                "method": "post",
            }
        },
    }


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

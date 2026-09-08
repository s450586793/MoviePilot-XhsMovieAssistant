"""Metadata-only notification outbox delivery shared by service boundaries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from .models import BrowserState
from .repository import (
    OutboxNotification,
    RequestRepository,
    RuntimeState,
    StoredRequest,
)


_LOGIN_PAUSE_CODES = frozenset({"AUTH_REQUIRED", "LOGIN_REQUIRED", "SESSION_EXPIRED"})
_RISK_PAUSE_CODES = frozenset({"RATE_LIMITED", "XHS_RISK_CONTROL"})


def enqueue_pause_notification(
    repository: RequestRepository,
    code: str,
    *,
    now: datetime | None = None,
) -> RuntimeState:
    """Persist a paused runtime and its safety notification once per incident."""
    state = repository.get_runtime_state()
    if state.browser_state is BrowserState.PAUSED:
        return state
    paused_at = now or datetime.now(timezone.utc)
    paused = repository.set_runtime_state(
        BrowserState.PAUSED,
        pause_code=code,
        paused_at=paused_at,
        pause_notified=False,
    )
    try:
        repository.enqueue_notification(
            "PAUSE",
            request_id=None,
            code=paused.pause_code or "UPSTREAM_ERROR",
            dedupe_key=f"pause:{paused_at.isoformat()}",
            now=paused_at,
        )
    except Exception:
        pass
    return paused


def flush_notification_outbox(
    repository: RequestRepository,
    notify: Callable[[str, str], None] | None,
    *,
    business_enabled: bool,
    is_cancelled: Callable[[], bool],
) -> None:
    """Attempt pending events and acknowledge only confirmed callback success."""
    if is_cancelled():
        return
    _ensure_pause_outbox_event(repository)
    if notify is None:
        return
    try:
        pending = repository.pending_notifications(20, business_enabled=business_enabled)
    except Exception:
        return
    for event in pending:
        if event.kind != "PAUSE" and not business_enabled:
            continue
        if is_cancelled():
            return
        request = _request_for_event(repository, event)
        title, text = _notification_content(event, request)
        try:
            notify(title, text)
        except Exception:
            try:
                repository.record_notification_attempt(event.id, delivered=False)
            except Exception:
                pass
            continue
        try:
            repository.record_notification_attempt(event.id, delivered=True)
        except Exception:
            continue
        if event.kind == "PAUSE":
            _mark_pause_notified(repository, event)


def _mark_pause_notified(
    repository: RequestRepository, event: OutboxNotification
) -> None:
    try:
        state = repository.get_runtime_state()
        if (
            state.browser_state is BrowserState.PAUSED
            and state.pause_code == event.code
        ):
            repository.set_runtime_state(
                BrowserState.PAUSED,
                pause_code=state.pause_code,
                paused_at=state.paused_at,
                pause_notified=True,
            )
    except Exception:
        pass


def _ensure_pause_outbox_event(repository: RequestRepository) -> None:
    try:
        state = repository.get_runtime_state()
        if (
            state.browser_state is not BrowserState.PAUSED
            or state.pause_notified
            or state.paused_at is None
        ):
            return
        event = repository.enqueue_notification(
            "PAUSE",
            request_id=None,
            code=state.pause_code or "UPSTREAM_ERROR",
            dedupe_key=f"pause:{state.paused_at.isoformat()}",
            now=state.paused_at,
        )
        if event.delivered_at is not None:
            _mark_pause_notified(repository, event)
    except Exception:
        pass


def _request_for_event(
    repository: RequestRepository, event: OutboxNotification
) -> StoredRequest | None:
    if event.request_id is None:
        return None
    try:
        return repository.get(event.request_id)
    except Exception:
        return None


def _notification_content(
    event: OutboxNotification, request: StoredRequest | None
) -> tuple[str, str]:
    if event.kind == "PAUSE":
        return _pause_notification_content(event.code)
    if event.kind == "REPLY_FAILURE":
        detail = _media_or_note_lines(request)
        text = ["⚠️ 小红书回复失败", *detail, "处理结果已保留，请人工检查评论回复。"]
        return "小红书回复失败", "\n".join(text)
    return "小红书影视助手", _request_result_content(event.code, request)


def _pause_notification_content(code: str) -> tuple[str, str]:
    if code in _LOGIN_PAUSE_CODES:
        reason = "登录状态失效，请重新登录。"
    elif code in _RISK_PAUSE_CODES:
        reason = "检测到小红书风控，监听已暂停，请人工检查。"
    elif code == "BROWSER_UNAVAILABLE":
        reason = "浏览器连接异常，监听已暂停，请检查浏览器服务。"
    else:
        reason = "小红书访问异常，监听已暂停，请人工检查。"
    return "小红书影视助手异常", f"⚠️ 小红书影视助手异常\n{reason}"


def _request_result_content(code: str, request: StoredRequest | None) -> str:
    media = _media_lines(request)
    if code == "DRY_RUN_MATCHED":
        return "\n".join(
            ["🎬 已识别（测试模式）", *media, "MoviePilot：匹配成功，未创建订阅"]
        )
    if code == "SUBSCRIBED":
        return "\n".join(["🎬 已添加订阅", *media, "MoviePilot：订阅成功"])
    if code == "ALREADY_SUBSCRIBED":
        return "\n".join(["🎬 已存在", *media, "已经订阅，无需重复添加。"])
    if code == "ALREADY_IN_LIBRARY":
        return "\n".join(["🎬 已存在", *media, "已经在媒体库中，无需重复添加。"])
    if code == "NEED_CONFIRMATION":
        request_line = (
            f"请求 #{request.id}"
            if request is not None
            else "请求号暂时不可用"
        )
        return "\n".join(
            [
                "⚠️ 无法确定影视作品",
                request_line,
                *_note_lines(request),
                "请直接回复明确的片名、年份和电影/剧集。",
                (
                    f"兜底命令：/xhs_confirm {request.id} 片名 年份 电影/剧集"
                    if request is not None
                    else "也可在插件管理页人工确认。"
                ),
            ]
        )
    if code == "NOT_MEDIA":
        return "\n".join(
            ["⚠️ 无法确定影视作品", *_note_lines(request), "需要人工确认。"]
        )
    if code == "FAILED":
        return "\n".join(
            [
                "⚠️ 处理失败",
                *_media_or_note_lines(request),
                "处理失败，请在插件详情中查看。",
            ]
        )
    if code == "IGNORED":
        return "\n".join(["请求已忽略", *_note_lines(request)])
    return "⚠️ 处理结果异常\n请在插件详情中查看。"


def _media_or_note_lines(request: StoredRequest | None) -> list[str]:
    media = _media_lines(request)
    return media if media else _note_lines(request)


def _media_lines(request: StoredRequest | None) -> list[str]:
    if request is None or not request.title:
        return []
    lines = [f"《{request.title}》"]
    details: list[str] = []
    if request.year is not None:
        details.append(str(request.year))
    media_type = {"movie": "Movie", "tv": "TV"}.get(request.media_type or "")
    if media_type is not None:
        details.append(media_type)
    if request.season is not None:
        details.append(f"第 {request.season} 季")
    if details:
        lines.append(" · ".join(details))
    return lines


def _note_lines(request: StoredRequest | None) -> list[str]:
    if request is None:
        return ["请求详情暂时不可用。"]
    lines: list[str] = []
    if request.note is not None and request.note.title:
        lines.append(f"小红书：{request.note.title}")
    if request.note_url:
        lines.append(request.note_url)
    return lines or ["请求详情暂时不可用。"]

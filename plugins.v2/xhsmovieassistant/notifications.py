"""Metadata-only notification outbox delivery shared by service boundaries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from .models import BrowserState
from .repository import OutboxNotification, RequestRepository, RuntimeState


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
        title, text = _notification_content(event)
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


def _notification_content(event: OutboxNotification) -> tuple[str, str]:
    if event.kind == "PAUSE":
        return "小红书监听已暂停", f"暂停原因：{event.code}"
    if event.kind == "REPLY_FAILURE":
        return "小红书回复失败", f"请求 {event.request_id}：{event.code}"
    return "小红书影视助手", f"请求 {event.request_id} 处理结果：{event.code}"

"""End-to-end orchestration for authorized Xiaohongshu requests."""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .models import (
    BrowserState,
    MediaRequest,
    ProcessingResult,
    ReplyStatus,
    RequestStatus,
    Resolution,
)
from .repository import (
    InvalidTransition,
    NewMention,
    RequestNotFound,
    RequestRepository,
    StoredRequest,
)
from .request_builder import TEXT_LIMITS, build_media_request, sanitize_text
from .templates import ReplyTemplates
from .xhs import XhsContractError, XhsPausedError
from .xhs_contracts import TransientMention


_REPROCESSABLE = {
    RequestStatus.FAILED,
    RequestStatus.NEED_CONFIRMATION,
    RequestStatus.DRY_RUN_MATCHED,
}
_SUBMISSION_STATUSES = {
    RequestStatus.DRY_RUN_MATCHED,
    RequestStatus.ALREADY_IN_LIBRARY,
    RequestStatus.ALREADY_SUBSCRIBED,
    RequestStatus.SUBSCRIBED,
    RequestStatus.FAILED,
}
_REPLY_PAUSE_CODES = {
    "AUTH_REQUIRED",
    "LOGIN_REQUIRED",
    "RATE_LIMITED",
    "SESSION_EXPIRED",
    "TEMPORARY_FAILURE",
    "XHS_RISK_CONTROL",
}


class AssistantService:
    """Coordinate persistence and external gateways for one polling cycle."""

    def __init__(
        self,
        *,
        repository: RequestRepository,
        xhs: Any,
        resolver: Any,
        moviepilot: Any,
        authorized_user_ids: Collection[str],
        notify: Callable[[str, str], None] | None = None,
        site_url: str = "https://www.xiaohongshu.com",
        enable_subscription: bool = False,
        confidence_threshold: float = 0.85,
        replies_enabled: bool = False,
        notifications_enabled: bool = True,
        templates: ReplyTemplates | None = None,
    ) -> None:
        if (
            isinstance(confidence_threshold, bool)
            or not isinstance(confidence_threshold, (int, float))
            or not 0 <= confidence_threshold <= 1
        ):
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.repository = repository
        self.xhs = xhs
        self.resolver = resolver
        self.moviepilot = moviepilot
        self.authorized_user_ids = frozenset(
            value.strip()
            for value in authorized_user_ids
            if isinstance(value, str) and value.strip()
        )
        self.notify = notify
        self.site_url = site_url.rstrip("/")
        self.enable_subscription = enable_subscription
        self.confidence_threshold = float(confidence_threshold)
        self.replies_enabled = replies_enabled
        self.notifications_enabled = notifications_enabled
        self.templates = templates or ReplyTemplates()
        self._consecutive_poll_failures = 0

    def poll_once(self) -> list[ProcessingResult]:
        """Process each newly observed authorized mention at most once."""
        if self.repository.get_runtime_state().browser_state is BrowserState.PAUSED:
            return []
        try:
            mentions = self.xhs.fetch_mentions(limit=20)
        except XhsPausedError as error:
            self._pause(error.code)
            return []
        except XhsContractError:
            self._consecutive_poll_failures += 1
            if self._consecutive_poll_failures >= 3:
                self._pause("BROWSER_UNAVAILABLE")
            return []

        self._consecutive_poll_failures = 0
        results: list[ProcessingResult] = []
        for mention in mentions:
            result = self.process_request(mention)
            if result is not None:
                results.append(result)
            if self.repository.get_runtime_state().browser_state is BrowserState.PAUSED:
                break
        return results

    def process_request(
        self, mention: TransientMention
    ) -> ProcessingResult | None:
        """Persist and resolve one new authorized mention."""
        if not isinstance(mention, TransientMention):
            raise TypeError("mention must be a TransientMention")
        if mention.sender_user_id not in self.authorized_user_ids:
            return None

        saved = self.repository.save_mention(
            NewMention(
                note_id=mention.note_id,
                note_url=self._note_url(mention.note_id),
                mention_id=mention.mention_id,
                sender_user_id=mention.sender_user_id,
                comment_id=mention.comment_id,
                comment_text=mention.comment_text,
                created_at=mention.created_at,
            )
        )
        if not saved.created:
            return None

        return self._process_saved(saved.request.id, mention)

    def reprocess(self, request_id: int) -> ProcessingResult:
        """Requeue and process one eligible request using durable safe fields."""
        stored = self._require_authorized(request_id)
        self.repository.requeue(request_id, authenticated=True)
        if stored.note is None:
            return self._complete(
                request_id,
                None,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )
        media_request = MediaRequest(
            request_id=f"xhs_{stored.mention_id}",
            source="xiaohongshu",
            intent="subscribe",
            trigger_comment=sanitize_text(
                stored.comment_text,
                TEXT_LIMITS["comment"],
            ),
            note=stored.note,
        )
        return self._resolve_media_request(request_id, media_request, None)

    def ignore(self, request_id: int) -> StoredRequest:
        """Mark a new or manually requeueable request as ignored."""
        stored = self._require_request(request_id)
        if stored.status in _REPROCESSABLE:
            stored = self.repository.requeue(request_id, authenticated=True)
        if stored.status is not RequestStatus.NEW:
            raise InvalidTransition(f"Cannot ignore {stored.status.value}")
        return self.repository.transition(request_id, RequestStatus.IGNORED)

    def manual_resolve(
        self, request_id: int, resolution: Resolution
    ) -> ProcessingResult:
        """Apply an authenticated manual resolution through normal MP checks."""
        if not isinstance(resolution, Resolution):
            raise TypeError("resolution must be a Resolution")
        stored = self._require_authorized(request_id)
        if stored.status in _REPROCESSABLE:
            stored = self.repository.requeue(request_id, authenticated=True)
        if stored.status is not RequestStatus.NEW:
            raise InvalidTransition(f"Cannot manually resolve {stored.status.value}")
        self.repository.transition(
            request_id,
            RequestStatus.FETCHED,
            note=stored.note,
        )
        self.repository.transition(request_id, RequestStatus.RESOLVING)
        return self._process_resolution(
            request_id,
            None,
            resolution,
        )

    def resume(self) -> None:
        """Clear a persisted browser pause and its notification marker."""
        self.repository.set_runtime_state(BrowserState.READY)
        self._consecutive_poll_failures = 0

    def _process_saved(
        self, request_id: int, mention: TransientMention
    ) -> ProcessingResult:
        try:
            detail = self.xhs.fetch_note(mention)
        except XhsPausedError as error:
            result = self._fail(request_id, error.code)
            self._pause(error.code)
            return result
        except Exception:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "BROWSER_UNAVAILABLE"),
            )

        try:
            media_request = build_media_request(mention, detail)
        except Exception:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )
        return self._resolve_media_request(request_id, media_request, mention)

    def _resolve_media_request(
        self,
        request_id: int,
        media_request: MediaRequest,
        mention: TransientMention | None,
    ) -> ProcessingResult:
        self.repository.transition(
            request_id,
            RequestStatus.FETCHED,
            note=media_request.note,
        )
        self.repository.transition(request_id, RequestStatus.RESOLVING)
        try:
            resolution = self.resolver.resolve(media_request)
        except Exception:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )
        return self._process_resolution(request_id, mention, resolution)

    def _process_resolution(
        self,
        request_id: int,
        mention: TransientMention | None,
        resolution: Resolution,
    ) -> ProcessingResult:
        if resolution.status == "not_media":
            return self._finish_resolution(
                request_id, mention, RequestStatus.NOT_MEDIA, resolution
            )
        if (
            resolution.status == "need_confirmation"
            or resolution.confidence < self.confidence_threshold
        ):
            return self._finish_resolution(
                request_id, mention, RequestStatus.NEED_CONFIRMATION, resolution
            )

        try:
            decision = self.moviepilot.match(resolution)
        except Exception:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )
        if decision.match is None:
            return self._finish_resolution(
                request_id, mention, RequestStatus.NEED_CONFIRMATION, resolution
            )

        self.repository.transition(
            request_id,
            RequestStatus.MATCHED,
            resolution=resolution,
            match=decision.match,
        )
        try:
            outcome = self.moviepilot.submit(decision, self.enable_subscription)
        except Exception:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )
        if outcome.status not in _SUBMISSION_STATUSES:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )

        transition_values: dict[str, str] = {}
        if outcome.status is RequestStatus.FAILED:
            transition_values["error"] = "UPSTREAM_ERROR"
        if outcome.subscription_id is not None:
            transition_values["subscription_id"] = outcome.subscription_id
        self.repository.transition(request_id, outcome.status, **transition_values)
        return self._complete(
            request_id,
            mention,
            ProcessingResult(
                status=outcome.status,
                resolution=resolution,
                match=decision.match,
                subscription_id=outcome.subscription_id,
            ),
        )

    def _finish_resolution(
        self,
        request_id: int,
        mention: TransientMention | None,
        status: RequestStatus,
        resolution: Resolution,
    ) -> ProcessingResult:
        self.repository.transition(request_id, status, resolution=resolution)
        return self._complete(
            request_id,
            mention,
            ProcessingResult(status=status, resolution=resolution),
        )

    def _fail(self, request_id: int, code: str) -> ProcessingResult:
        self.repository.transition(request_id, RequestStatus.FAILED, error=code)
        return ProcessingResult(status=RequestStatus.FAILED, message=code)

    def _complete(
        self,
        request_id: int,
        mention: TransientMention | None,
        result: ProcessingResult,
    ) -> ProcessingResult:
        self._safe_notify(
            "小红书影视助手",
            f"请求 {request_id} 处理结果：{result.status.value}",
        )
        if mention is not None:
            self._reply(request_id, mention, result)
        return result

    def _reply(
        self,
        request_id: int,
        mention: TransientMention,
        result: ProcessingResult,
    ) -> None:
        if not self.replies_enabled:
            return
        stored = self._require_request(request_id)
        if stored.reply_status is not ReplyStatus.PENDING:
            return
        text = self.templates.render(result)
        if text is None:
            return

        try:
            outcome = self.xhs.reply_to_comment(mention, text)
        except XhsPausedError as error:
            self.repository.mark_reply(request_id, status=ReplyStatus.FAILED)
            self._pause(error.code)
            return
        except Exception:
            self.repository.mark_reply(request_id, status=ReplyStatus.FAILED)
            self._safe_notify("小红书回复失败", f"请求 {request_id}：REPLY_FAILED")
            return

        if outcome.success:
            self.repository.mark_reply(request_id, mention.comment_id)
            return
        self.repository.mark_reply(request_id, status=ReplyStatus.FAILED)
        if outcome.code in _REPLY_PAUSE_CODES:
            self._pause(outcome.code)
        else:
            self._safe_notify("小红书回复失败", f"请求 {request_id}：REPLY_FAILED")

    def _pause(self, code: str) -> None:
        state = self.repository.get_runtime_state()
        if state.browser_state is BrowserState.PAUSED and state.pause_notified:
            return
        self.repository.set_runtime_state(
            BrowserState.PAUSED,
            pause_code=code,
            paused_at=datetime.now(timezone.utc),
            pause_notified=True,
        )
        self._safe_notify("小红书监听已暂停", f"暂停原因：{code}")

    def _safe_notify(self, title: str, text: str) -> None:
        if not self.notifications_enabled or self.notify is None:
            return
        try:
            self.notify(title, text)
        except Exception:
            pass

    def _require_request(self, request_id: int) -> StoredRequest:
        stored = self.repository.get(request_id)
        if stored is None:
            raise RequestNotFound(f"Request {request_id} was not found")
        return stored

    def _require_authorized(self, request_id: int) -> StoredRequest:
        stored = self._require_request(request_id)
        if stored.sender_user_id not in self.authorized_user_ids:
            raise PermissionError("Request does not belong to an authorized sender")
        return stored

    def _note_url(self, note_id: str) -> str:
        parsed = urlsplit(self.site_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.port is not None:
            raise ValueError("site_url must be a supported HTTPS site")
        prefixes = {
            "www.xiaohongshu.com": "/explore/",
            "www.rednote.com": "/discovery/item/",
        }
        if host not in prefixes:
            raise ValueError("site_url must be a supported Xiaohongshu site")
        return urlunsplit(
            ("https", host, f"{prefixes[host]}{quote(note_id, safe='')}", "", "")
        )

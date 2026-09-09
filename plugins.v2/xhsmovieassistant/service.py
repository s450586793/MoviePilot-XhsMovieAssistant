"""End-to-end orchestration for authorized Xiaohongshu requests."""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable, Collection
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .models import (
    BrowserState,
    MediaMatch,
    MediaRequest,
    ProcessingResult,
    ReplyStatus,
    RequestStatus,
    Resolution,
)
from .notifications import enqueue_pause_notification, flush_notification_outbox
from .repository import (
    IdempotencyConflict,
    InvalidTransition,
    NewMention,
    OutboxNotification,
    RequestNotFound,
    RequestRepository,
    StoredRequest,
)
from .request_builder import TEXT_LIMITS, build_media_request, sanitize_text
from .templates import ReplyTemplates
from .xhs import ReplyOutcome, XhsContractError, XhsPausedError
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
    "BROWSER_UNAVAILABLE",
    "LOGIN_REQUIRED",
    "RATE_LIMITED",
    "SESSION_EXPIRED",
    "XHS_RISK_CONTROL",
}
_REPLYABLE_STATUSES = {
    RequestStatus.SUBSCRIBED,
    RequestStatus.ALREADY_SUBSCRIBED,
    RequestStatus.ALREADY_IN_LIBRARY,
    RequestStatus.NEED_CONFIRMATION,
    RequestStatus.FAILED,
}

_LOGGER = logging.getLogger(__name__)


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
        request_confirmation: Callable[[int, str, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
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
        self.request_confirmation = request_confirmation
        self._is_cancelled = is_cancelled or (lambda: False)
        self._consecutive_poll_failures = 0

    def poll_once(self) -> list[ProcessingResult]:
        """Process each newly observed authorized mention at most once."""
        self._flush_notifications()
        if self.repository.get_runtime_state().browser_state is BrowserState.PAUSED:
            return []
        try:
            self._ensure_active()
            mentions = self.xhs.fetch_mentions(limit=20)
        except _ServiceCancelled:
            return []
        except XhsPausedError as error:
            self._pause(error.code)
            return []
        except XhsContractError as error:
            self._consecutive_poll_failures += 1
            _LOGGER.warning(
                "XHS poll failed: stage=fetch_mentions attempt=%d exception=%s",
                self._consecutive_poll_failures,
                type(error).__name__,
            )
            if self._consecutive_poll_failures >= 3:
                self._pause("TEMPORARY_FAILURE")
            return []

        self._consecutive_poll_failures = 0
        results: list[ProcessingResult] = []
        for mention in mentions:
            try:
                result = self.process_request(mention)
            except IdempotencyConflict:
                continue
            except _ServiceCancelled:
                break
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
            recovered = (
                saved.request.status is RequestStatus.NEW
                and saved.request.mention_id == mention.mention_id
            )
            if not recovered:
                return None

        return self._process_saved(saved.request.id, mention)

    def reprocess(self, request_id: int) -> ProcessingResult:
        """Requeue and process one eligible request using durable safe fields."""
        self._flush_notifications()
        stored = self._require_authorized(request_id)
        if stored.status is not RequestStatus.NEW:
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
        self._flush_notifications()
        stored = self._require_request(request_id)
        if stored.status in _REPROCESSABLE:
            stored = self.repository.requeue(request_id, authenticated=True)
        if stored.status is not RequestStatus.NEW:
            raise InvalidTransition(f"Cannot ignore {stored.status.value}")
        return self._transition(request_id, RequestStatus.IGNORED)

    def manual_resolve(
        self, request_id: int, resolution: Resolution
    ) -> ProcessingResult:
        """Apply an authenticated manual resolution through normal MP checks."""
        self._flush_notifications()
        if not isinstance(resolution, Resolution):
            raise TypeError("resolution must be a Resolution")
        stored = self._require_authorized(request_id)
        if stored.status in _REPROCESSABLE:
            stored = self.repository.requeue(request_id, authenticated=True)
        if stored.status is not RequestStatus.NEW:
            raise InvalidTransition(f"Cannot manually resolve {stored.status.value}")
        self._transition(
            request_id,
            RequestStatus.FETCHED,
            note=stored.note,
        )
        self._transition(request_id, RequestStatus.RESOLVING)
        return self._process_resolution(
            request_id,
            None,
            resolution,
        )

    def confirm_from_text(
        self,
        request_id: int,
        clarification: str,
    ) -> ProcessingResult:
        """Resolve one pending request from an authorized WeChat clarification."""
        self._flush_notifications()
        stored = self._require_authorized(request_id)
        if stored.status is not RequestStatus.NEED_CONFIRMATION:
            raise InvalidTransition("Request is not awaiting NEED_CONFIRMATION")
        if stored.note is None:
            raise InvalidTransition("Request has no durable note context")
        clarification = "".join(
            character
            for character in str(clarification or "")
            if character in "\n\t" or unicodedata.category(character) != "Cc"
        ).strip()[: TEXT_LIMITS["comment"]]
        if not clarification:
            raise ValueError("clarification must be a non-empty string")

        self.repository.requeue(request_id, authenticated=True)
        media_request = self._media_request(stored)
        self._transition(request_id, RequestStatus.FETCHED, note=stored.note)
        self._transition(request_id, RequestStatus.RESOLVING)
        try:
            self._ensure_active(request_id)
            resolution = self.resolver.resolve_confirmation(
                media_request,
                clarification,
            )
        except _ServiceCancelled:
            raise
        except Exception:
            return self._complete(
                request_id,
                None,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )
        return self._process_resolution(request_id, None, resolution)

    def confirm_candidate(
        self,
        request_id: int,
        selection: int,
    ) -> ProcessingResult:
        """Submit one authorized, persisted MoviePilot candidate by number."""
        self._flush_notifications()
        if isinstance(selection, bool) or not isinstance(selection, int) or selection < 1:
            raise ValueError("candidate selection must be a positive integer")
        stored = self._require_authorized(request_id)
        if stored.status is not RequestStatus.NEED_CONFIRMATION:
            raise InvalidTransition("Request is not awaiting NEED_CONFIRMATION")
        if stored.note is None:
            raise InvalidTransition("Request has no durable note context")
        if selection > len(stored.candidates):
            raise ValueError("candidate selection is out of range")

        selected = stored.candidates[selection - 1]
        self._ensure_active(request_id)
        decision = self.moviepilot.select(selected)
        if decision.match is None or decision.media_info is None:
            raise InvalidTransition("Selected candidate is no longer available")
        resolution = Resolution(
            status="resolved",
            title=decision.match.title,
            original_title=decision.match.original_title,
            media_type=decision.match.media_type,
            year=decision.match.year,
            season=decision.match.season,
            confidence=1.0,
            reason="Authorized MoviePilot candidate selection",
        )
        self.repository.requeue(request_id, authenticated=True)
        self._transition(request_id, RequestStatus.FETCHED, note=stored.note)
        self._transition(request_id, RequestStatus.RESOLVING)
        return self._submit_match(request_id, None, resolution, decision)

    def reply(self, request_id: int) -> StoredRequest:
        """Retry one pending public reply after refreshing its transient token."""
        self._flush_notifications()
        stored = self._require_authorized(request_id)
        if not self.replies_enabled:
            raise InvalidTransition("XHS replies are disabled")
        if stored.status not in _REPLYABLE_STATUSES:
            raise InvalidTransition(f"Cannot reply for {stored.status.value}")
        if stored.reply_status is not ReplyStatus.PENDING:
            raise InvalidTransition("Reply delivery is already final")
        result = self._stored_result(stored)
        if self.templates.render(result) is None:
            raise InvalidTransition("Reply category is disabled")
        self._reply_latest(request_id, result)
        return self._require_request(request_id)

    def check_reply_target(self, request_id: int) -> ReplyOutcome:
        """Inspect the latest reply controls without submitting a public reply."""
        stored = self._require_authorized(request_id)
        expected = TransientMention(
            mention_id=stored.mention_id,
            sender_user_id=stored.sender_user_id,
            comment_id=stored.comment_id,
            comment_text=stored.comment_text,
            note_id=stored.note_id,
            xsec_token="",
            created_at=stored.created_at,
        )
        try:
            self._ensure_active(request_id)
            return self.xhs.check_reply_target(expected)
        except XhsPausedError as error:
            return ReplyOutcome(
                success=False,
                code=error.code,
                message="Browser operation paused",
            )
        except Exception:
            return ReplyOutcome(
                success=False,
                code="REPLY_CHECK_FAILED",
                message="The reply target check failed",
            )

    def resume(self) -> None:
        """Clear a persisted browser pause and its notification marker."""
        self._flush_notifications()
        self.repository.set_runtime_state(BrowserState.READY)
        self._consecutive_poll_failures = 0

    def _process_saved(
        self, request_id: int, mention: TransientMention
    ) -> ProcessingResult:
        try:
            self._ensure_active(request_id)
            detail = self.xhs.fetch_note(mention)
        except _ServiceCancelled:
            raise
        except XhsPausedError as error:
            result = self._fail(request_id, error.code)
            self._pause(error.code)
            return result
        except Exception:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "TEMPORARY_FAILURE"),
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
        self._transition(
            request_id,
            RequestStatus.FETCHED,
            note=media_request.note,
        )
        self._transition(request_id, RequestStatus.RESOLVING)
        try:
            self._ensure_active(request_id)
            resolution = self.resolver.resolve(media_request)
        except _ServiceCancelled:
            raise
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
        if resolution.status == "need_confirmation" and resolution.candidates:
            try:
                self._ensure_active(request_id)
                candidates = self._match_resolution_candidates(resolution)
            except _ServiceCancelled:
                raise
            except Exception:
                return self._complete(
                    request_id,
                    mention,
                    self._fail(request_id, "UPSTREAM_ERROR"),
                )
            return self._finish_resolution(
                request_id,
                mention,
                RequestStatus.NEED_CONFIRMATION,
                resolution,
                candidates=candidates or (),
                match_reason="MULTIPLE_MEDIA" if candidates else "NO_MATCH",
            )
        if (
            resolution.status == "need_confirmation"
            or resolution.confidence < self.confidence_threshold
        ):
            return self._finish_resolution(
                request_id, mention, RequestStatus.NEED_CONFIRMATION, resolution
            )

        try:
            self._ensure_active(request_id)
            decision = self.moviepilot.match(resolution)
        except _ServiceCancelled:
            raise
        except Exception:
            return self._complete(
                request_id,
                mention,
                self._fail(request_id, "UPSTREAM_ERROR"),
            )
        if decision.match is None:
            return self._finish_resolution(
                request_id,
                mention,
                RequestStatus.NEED_CONFIRMATION,
                resolution,
                candidates=decision.candidates,
                match_reason=decision.reason_code,
            )

        return self._submit_match(request_id, mention, resolution, decision)

    def _match_resolution_candidates(
        self,
        resolution: Resolution,
    ) -> tuple[MediaMatch, ...]:
        matches: list[MediaMatch] = []
        seen: set[tuple[str, str, int | None]] = set()
        for candidate in resolution.candidates:
            self._ensure_active()
            requested = Resolution(
                status="resolved",
                title=candidate.title,
                original_title=candidate.original_title,
                media_type=candidate.media_type,
                year=candidate.year,
                season=candidate.season,
                confidence=1.0,
                reason="Candidate from a multi-media XHS note",
            )
            decision = self.moviepilot.match(requested)
            if (
                decision.match is None
                and not decision.candidates
                and requested.year is not None
            ):
                self._ensure_active()
                decision = self.moviepilot.match(
                    requested.model_copy(update={"year": None})
                )
            available = (
                (decision.match,) if decision.match is not None else decision.candidates
            )
            for match in available:
                identity = (match.source.casefold(), match.source_id, match.season)
                if identity in seen:
                    continue
                seen.add(identity)
                matches.append(match)
                if len(matches) == 20:
                    return tuple(matches)
        return tuple(matches)

    def _submit_match(
        self,
        request_id: int,
        mention: TransientMention | None,
        resolution: Resolution,
        decision: Any,
    ) -> ProcessingResult:
        """Persist and submit one exact MoviePilot decision."""

        self._transition(
            request_id,
            RequestStatus.MATCHED,
            resolution=resolution,
            match=decision.match,
        )
        try:
            self._ensure_active(request_id)
            outcome = self.moviepilot.submit(decision, self.enable_subscription)
        except _ServiceCancelled:
            raise
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
        self._transition(request_id, outcome.status, **transition_values)
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
        *,
        candidates: tuple[MediaMatch, ...] = (),
        match_reason: str | None = None,
    ) -> ProcessingResult:
        self._transition(
            request_id,
            status,
            resolution=resolution,
            candidates=candidates or None,
            match_reason=match_reason,
        )
        return self._complete(
            request_id,
            mention,
            ProcessingResult(status=status, resolution=resolution),
        )

    def _fail(self, request_id: int, code: str) -> ProcessingResult:
        self._transition(request_id, RequestStatus.FAILED, error=code)
        return ProcessingResult(status=RequestStatus.FAILED, message=code)

    def _complete(
        self,
        request_id: int,
        mention: TransientMention | None,
        result: ProcessingResult,
    ) -> ProcessingResult:
        if self._is_cancelled():
            return result
        if mention is not None:
            self._reply(request_id, mention, result)
        elif self.replies_enabled and self.templates.render(result) is not None:
            self._reply_latest(request_id, result)
        self._flush_notifications()
        return result

    def _reply_latest(
        self,
        request_id: int,
        result: ProcessingResult,
    ) -> None:
        stored = self._require_request(request_id)
        if stored.reply_status is not ReplyStatus.PENDING:
            return
        try:
            self._ensure_active(request_id)
            mentions = self.xhs.fetch_mentions(limit=20)
        except _ServiceCancelled:
            return
        except XhsPausedError as error:
            self._mark_reply_failed(request_id)
            self._pause(error.code)
            return
        except Exception:
            self._mark_reply_failed(request_id)
            return

        mention = self._matching_mention(stored, mentions)
        if mention is None:
            self._mark_reply_failed(request_id)
            return
        self._reply(request_id, mention, result)

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
            self._ensure_active(request_id)
            outcome = self.xhs.reply_to_comment(mention, text)
        except _ServiceCancelled:
            return
        except XhsPausedError as error:
            self.repository.mark_reply(
                request_id,
                status=ReplyStatus.FAILED,
                error_code=error.code,
                business_notifications_enabled=self.notifications_enabled,
            )
            self._pause(error.code)
            return
        except Exception:
            self.repository.mark_reply(
                request_id,
                status=ReplyStatus.FAILED,
                error_code="REPLY_FAILED",
                business_notifications_enabled=self.notifications_enabled,
            )
            return

        reply_id = (
            outcome.reply_id.strip()
            if isinstance(outcome.reply_id, str) and outcome.reply_id.strip()
            else None
        )
        if outcome.success and (reply_id is not None or outcome.code == "UI_CONFIRMED"):
            self.repository.mark_reply(request_id, reply_id)
            return
        self.repository.mark_reply(
            request_id,
            status=ReplyStatus.FAILED,
            error_code=outcome.code or "SUBMIT_UNCONFIRMED",
            business_notifications_enabled=self.notifications_enabled,
        )
        if outcome.code in _REPLY_PAUSE_CODES:
            self._pause(outcome.code)

    def _mark_reply_failed(
        self,
        request_id: int,
        error_code: str = "REPLY_FAILED",
    ) -> None:
        try:
            self.repository.mark_reply(
                request_id,
                status=ReplyStatus.FAILED,
                error_code=error_code,
                business_notifications_enabled=self.notifications_enabled,
            )
        except InvalidTransition:
            pass

    def _pause(self, code: str) -> None:
        enqueue_pause_notification(self.repository, code)
        self._flush_notifications()

    def _transition(
        self, request_id: int, target: RequestStatus, **kwargs: Any
    ) -> StoredRequest:
        return self.repository.transition(
            request_id,
            target,
            business_notifications_enabled=self.notifications_enabled,
            **kwargs,
        )

    def _flush_notifications(self) -> None:
        flush_notification_outbox(
            self.repository,
            self.notify,
            business_enabled=self.notifications_enabled,
            is_cancelled=self._is_cancelled,
            on_delivered=self._handle_delivered_notification,
        )

    def _handle_delivered_notification(
        self, event: OutboxNotification, title: str, text: str
    ) -> None:
        if (
            event.kind != "REQUEST_RESULT"
            or event.code != RequestStatus.NEED_CONFIRMATION.value
            or event.request_id is None
            or self.request_confirmation is None
        ):
            return
        self.request_confirmation(event.request_id, title, text)

    def _ensure_active(self, request_id: int | None = None) -> None:
        if self._is_cancelled():
            if request_id is not None:
                self.repository.retry_interrupted(request_id)
            raise _ServiceCancelled

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

    @staticmethod
    def _matching_mention(
        stored: StoredRequest,
        mentions: Collection[TransientMention],
    ) -> TransientMention | None:
        candidates = [
            mention
            for mention in mentions
            if mention.mention_id == stored.mention_id
        ]
        if len(candidates) != 1:
            return None
        mention = candidates[0]
        if (
            mention.sender_user_id != stored.sender_user_id
            or mention.comment_id != stored.comment_id
            or mention.note_id != stored.note_id
        ):
            return None
        return mention

    @staticmethod
    def _media_request(stored: StoredRequest) -> MediaRequest:
        if stored.note is None:
            raise InvalidTransition("Request has no durable note context")
        return MediaRequest(
            request_id=f"xhs_{stored.mention_id}",
            source="xiaohongshu",
            intent="subscribe",
            trigger_comment=sanitize_text(
                stored.comment_text,
                TEXT_LIMITS["comment"],
            ),
            note=stored.note,
        )

    @staticmethod
    def _stored_result(stored: StoredRequest) -> ProcessingResult:
        media_type = (
            stored.media_type
            if stored.media_type in {"movie", "tv", "unknown"}
            else "unknown"
        )
        resolution_status = "need_confirmation"
        if stored.status is RequestStatus.NOT_MEDIA:
            resolution_status = "not_media"
        elif stored.title and media_type in {"movie", "tv"}:
            resolution_status = "resolved"
        resolution = Resolution(
            status=resolution_status,
            title=stored.title or "",
            original_title=stored.original_title or "",
            media_type=media_type,
            year=stored.year,
            season=stored.season,
            confidence=stored.confidence if stored.confidence is not None else 0,
            reason=stored.resolution_reason or "",
        )
        match = None
        if (
            stored.title
            and media_type in {"movie", "tv"}
            and stored.media_source
            and stored.media_source_id
        ):
            match = MediaMatch(
                title=stored.title,
                original_title=stored.original_title or "",
                media_type=media_type,
                year=stored.year,
                season=stored.season,
                source=stored.media_source,
                source_id=stored.media_source_id,
                tmdb_id=stored.tmdb_id,
                score=stored.score,
            )
        return ProcessingResult(
            status=stored.status,
            message=stored.error or "",
            resolution=resolution,
            match=match,
            subscription_id=stored.subscription_id,
        )

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


class _ServiceCancelled(RuntimeError):
    """Stop the current generation before it starts another external call."""

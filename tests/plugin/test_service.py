from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Callable

import pytest

from xhsmovieassistant import models as domain_models
from xhsmovieassistant.models import (
    BrowserState,
    MediaMatch,
    MediaRequest,
    NoteContext,
    ReplyStatus,
    RequestStatus,
    Resolution,
)
from xhsmovieassistant.moviepilot import MatchDecision, SubscriptionOutcome
from xhsmovieassistant.repository import (
    InvalidTransition,
    NewMention,
    RequestRepository,
)
from xhsmovieassistant.resolver import ResolverError
from xhsmovieassistant.service import AssistantService
from xhsmovieassistant.xhs import (
    NoteDetail,
    ReplyOutcome,
    XhsContractError,
    XhsPausedError,
)
from xhsmovieassistant.xhs_contracts import TransientMention


def mention(
    *,
    mention_id: str = "m1",
    sender_user_id: str = "authorized-user",
    comment_text: str = "想看",
) -> TransientMention:
    return TransientMention(
        mention_id=mention_id,
        sender_user_id=sender_user_id,
        comment_id=f"comment-{mention_id}",
        comment_text=comment_text,
        note_id=f"note-{mention_id}",
        xsec_token="transient-token",
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )


def resolution(
    *, status: str = "resolved", confidence: float = 0.98
) -> Resolution:
    if status == "resolved":
        return Resolution(
            status="resolved",
            title="星际穿越",
            original_title="Interstellar",
            media_type="movie",
            year=2014,
            confidence=confidence,
        )
    return Resolution(
        status=status,  # type: ignore[arg-type]
        media_type="unknown",
        confidence=confidence,
        reason="safe reason",
    )


def matched_decision() -> MatchDecision:
    return MatchDecision(
        match=MediaMatch(
            title="星际穿越",
            original_title="Interstellar",
            media_type="movie",
            year=2014,
            source="tmdb",
            source_id="157336",
            tmdb_id=157336,
            score=0.95,
        ),
        media_info=object(),
        reason_code="MATCHED",
    )


class FakeXhs:
    def __init__(self) -> None:
        self.mentions: list[TransientMention] = []
        self.mention_failures: list[Exception] = []
        self.note_failures: dict[str, Exception] = {}
        self.reply_outcomes: list[ReplyOutcome | Exception] = []
        self.reply_probe_outcomes: list[ReplyOutcome | Exception] = []
        self.reply_probe_mentions: list[TransientMention] = []
        self.fetch_mentions_calls = 0
        self.fetch_note_calls = 0
        self.reply_calls = 0
        self.reply_probe_calls = 0
        self.repository: RequestRepository | None = None
        self.note_call_statuses: list[RequestStatus] = []
        self.reply_call_statuses: list[RequestStatus] = []

    def fetch_mentions(self, limit: int = 20) -> tuple[TransientMention, ...]:
        self.fetch_mentions_calls += 1
        if self.mention_failures:
            raise self.mention_failures.pop(0)
        return tuple(self.mentions[:limit])

    def fetch_note(self, item: TransientMention) -> NoteDetail:
        self.fetch_note_calls += 1
        if self.repository is not None:
            self.note_call_statuses.append(self.repository.recent(1)[0].status)
        if not item.xsec_token:
            raise AssertionError("fetch_note requires a transient xsec_token")
        failure = self.note_failures.get(item.mention_id)
        if failure is not None:
            raise failure
        return NoteDetail(
            note_id=item.note_id,
            url=f"https://www.xiaohongshu.com/explore/{item.note_id}",
            type="video",
            title="星际穿越",
            content="2014 年电影",
            author="作者",
        )

    def reply_to_comment(self, item: TransientMention, text: str) -> ReplyOutcome:
        self.reply_calls += 1
        if self.repository is not None:
            self.reply_call_statuses.append(self.repository.recent(1)[0].status)
        if not self.reply_outcomes:
            return ReplyOutcome(success=True, reply_id="reply-default")
        outcome = self.reply_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def check_reply_target(self, item: TransientMention) -> ReplyOutcome:
        self.reply_probe_calls += 1
        self.reply_probe_mentions.append(item)
        if not self.reply_probe_outcomes:
            return ReplyOutcome(
                success=True,
                code="REPLY_READY",
                message="Reply controls are ready",
            )
        outcome = self.reply_probe_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeResolver:
    def __init__(self) -> None:
        self.outcomes: list[Resolution | Exception] = []
        self.confirmation_outcomes: list[Resolution | Exception] = []
        self.requests: list[object] = []
        self.confirmation_requests: list[tuple[object, str]] = []
        self.repository: RequestRepository | None = None
        self.call_statuses: list[RequestStatus] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def resolve(self, request: object) -> Resolution:
        self.requests.append(request)
        if self.repository is not None:
            self.call_statuses.append(self.repository.recent(1)[0].status)
        outcome = self.outcomes.pop(0) if self.outcomes else resolution()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def resolve_confirmation(self, request: object, clarification: str) -> Resolution:
        self.confirmation_requests.append((request, clarification))
        outcome = (
            self.confirmation_outcomes.pop(0)
            if self.confirmation_outcomes
            else resolution()
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeMoviePilot:
    def __init__(self) -> None:
        self.match_outcomes: list[MatchDecision | Exception] = []
        self.select_outcomes: list[MatchDecision | Exception] = []
        self.submit_outcomes: list[SubscriptionOutcome | Exception] = []
        self.match_calls: list[Resolution] = []
        self.select_calls: list[MediaMatch] = []
        self.submit_calls: list[tuple[MatchDecision, bool]] = []
        self.repository: RequestRepository | None = None
        self.match_call_statuses: list[RequestStatus] = []
        self.submit_call_statuses: list[RequestStatus] = []

    @property
    def calls(self) -> int:
        return len(self.match_calls) + len(self.submit_calls)

    def match(self, value: Resolution) -> MatchDecision:
        self.match_calls.append(value)
        if self.repository is not None:
            self.match_call_statuses.append(self.repository.recent(1)[0].status)
        outcome = self.match_outcomes.pop(0) if self.match_outcomes else matched_decision()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def select(self, candidate: MediaMatch) -> MatchDecision:
        self.select_calls.append(candidate)
        outcome = (
            self.select_outcomes.pop(0)
            if self.select_outcomes
            else MatchDecision(
                match=candidate,
                media_info=object(),
                reason_code="SELECTED",
            )
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def submit(
        self, decision: MatchDecision, enable_subscription: bool
    ) -> SubscriptionOutcome:
        self.submit_calls.append((decision, enable_subscription))
        if self.repository is not None:
            self.submit_call_statuses.append(self.repository.recent(1)[0].status)
        outcome = (
            self.submit_outcomes.pop(0)
            if self.submit_outcomes
            else SubscriptionOutcome(status=RequestStatus.DRY_RUN_MATCHED)
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def build_service(
    database_path: Path,
    *,
    enable_subscription: bool = False,
    replies_enabled: bool = False,
    confidence_threshold: float = 0.85,
    notify: Callable[[str, str], None] | None = None,
    notifications_enabled: bool = True,
    request_confirmation: Callable[[int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[
    AssistantService,
    RequestRepository,
    FakeXhs,
    FakeResolver,
    FakeMoviePilot,
    list[tuple[str, str]],
]:
    repository = RequestRepository(database_path)
    xhs = FakeXhs()
    resolver = FakeResolver()
    moviepilot = FakeMoviePilot()
    notifications: list[tuple[str, str]] = []
    callback = notify or (lambda title, text: notifications.append((title, text)))
    service = AssistantService(
        repository=repository,
        xhs=xhs,
        resolver=resolver,
        moviepilot=moviepilot,
        authorized_user_ids=frozenset({"authorized-user"}),
        notify=callback,
        enable_subscription=enable_subscription,
        replies_enabled=replies_enabled,
        notifications_enabled=notifications_enabled,
        request_confirmation=request_confirmation,
        confidence_threshold=confidence_threshold,
        is_cancelled=is_cancelled,
    )
    xhs.repository = repository
    resolver.repository = repository
    moviepilot.repository = repository
    return service, repository, xhs, resolver, moviepilot, notifications


@pytest.fixture
def service(tmp_path: Path) -> AssistantService:
    return build_service(tmp_path / "assistant.db")[0]


def test_unauthorized_sender_never_reaches_note_or_llm(
    service: AssistantService,
) -> None:
    service.xhs.mentions = [mention(sender_user_id="other-user")]

    assert service.poll_once() == []

    assert service.repository.recent(10) == []
    assert service.xhs.fetch_note_calls == 0
    assert service.resolver.calls == 0
    assert service.moviepilot.calls == 0


def test_duplicate_mention_never_resolves_twice(service: AssistantService) -> None:
    service.xhs.mentions = [mention(mention_id="m1")]

    service.poll_once()
    service.poll_once()

    assert service.resolver.calls == 1


def test_poll_once_skips_mention_identity_conflict(tmp_path: Path) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    repository.save_mention(
        NewMention(
            note_id="note-m1",
            note_url="https://www.xiaohongshu.com/explore/note-m1",
            mention_id="m1",
            sender_user_id="authorized-user",
            comment_id="comment-m1",
            comment_text="想看星际穿越",
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    )
    xhs.mentions = [mention(comment_text="想看沙丘")]

    assert service.poll_once() == []

    assert resolver.calls == 0
    assert moviepilot.calls == 0


def test_poll_once_propagates_terminal_outbox_metadata_conflict(tmp_path: Path) -> None:
    service, repository, xhs, _, _, _ = build_service(tmp_path / "assistant.db")
    saved = repository.save_mention(
        NewMention(
            note_id="note-m1",
            note_url="https://www.xiaohongshu.com/explore/note-m1",
            mention_id="m1",
            sender_user_id="authorized-user",
            comment_id="comment-m1",
            comment_text="想看",
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    )
    repository.enqueue_notification(
        "REPLY_FAILURE",
        request_id=saved.request.id,
        code="REPLY_FAILED",
        dedupe_key=f"REQUEST_RESULT:{saved.request.id}:0",
    )
    xhs.mentions = [mention()]

    with pytest.raises(RuntimeError, match="notification dedupe key"):
        service.poll_once()

    assert repository.get(saved.request.id).status is RequestStatus.MATCHED  # type: ignore[union-attr]


def test_recovered_request_is_retried_by_next_poll_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "assistant.db"
    interrupted_at = datetime(2026, 9, 7, 11, 40, tzinfo=timezone.utc)
    restarted_at = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    repository = RequestRepository(database_path)
    saved = repository.save_mention(
        NewMention(
            note_id="note-m1",
            note_url="https://www.xiaohongshu.com/explore/note-m1",
            mention_id="m1",
            sender_user_id="authorized-user",
            comment_id="comment-m1",
            comment_text="想看",
            created_at=interrupted_at,
        )
    )
    repository.transition(
        saved.request.id,
        RequestStatus.FETCHED,
        note=NoteContext(
            id="note-m1",
            url="https://www.xiaohongshu.com/explore/note-m1",
            title="星际穿越",
        ),
        now=interrupted_at,
    )
    repository.transition(
        saved.request.id,
        RequestStatus.RESOLVING,
        now=interrupted_at,
    )
    assert repository.recover_interrupted(now=restarted_at) == 1

    service, restarted_repository, xhs, resolver, _, _ = build_service(database_path)
    xhs.mentions = [mention(mention_id="m1")]

    results = service.poll_once()

    assert [result.status for result in results] == [RequestStatus.DRY_RUN_MATCHED]
    assert resolver.calls == 1
    recovered = restarted_repository.get(saved.request.id)
    assert recovered is not None
    assert recovered.status is RequestStatus.DRY_RUN_MATCHED
    assert recovered.attempt_count == 1


def test_reprocess_resumes_recovered_new_request_from_durable_snapshot(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "assistant.db"
    interrupted_at = datetime(2026, 9, 7, 11, 40, tzinfo=timezone.utc)
    restarted_at = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    repository = RequestRepository(database_path)
    saved = repository.save_mention(
        NewMention(
            note_id="note-m1",
            note_url="https://www.xiaohongshu.com/explore/note-m1",
            mention_id="m1",
            sender_user_id="authorized-user",
            comment_id="comment-m1",
            comment_text="想看",
            created_at=interrupted_at,
        )
    )
    repository.transition(
        saved.request.id,
        RequestStatus.FETCHED,
        note=NoteContext(
            id="note-m1",
            url="https://www.xiaohongshu.com/explore/note-m1",
            title="星际穿越",
        ),
        now=interrupted_at,
    )
    repository.transition(
        saved.request.id,
        RequestStatus.RESOLVING,
        now=interrupted_at,
    )
    assert repository.recover_interrupted(now=restarted_at) == 1

    service, restarted_repository, xhs, resolver, moviepilot, _ = build_service(
        database_path
    )

    result = service.reprocess(saved.request.id)

    recovered = restarted_repository.get(saved.request.id)
    assert result.status is RequestStatus.DRY_RUN_MATCHED
    assert recovered is not None
    assert recovered.status is RequestStatus.DRY_RUN_MATCHED
    assert recovered.attempt_count == 1
    assert xhs.fetch_mentions_calls == 0
    assert xhs.fetch_note_calls == 0
    assert resolver.calls == 1
    assert len(moviepilot.match_calls) == 1
    assert len(moviepilot.submit_calls) == 1


def test_reprocess_rejects_resolving_request(tmp_path: Path) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    saved = repository.save_mention(
        NewMention(
            note_id="note-m1",
            note_url="https://www.xiaohongshu.com/explore/note-m1",
            mention_id="m1",
            sender_user_id="authorized-user",
            comment_id="comment-m1",
            comment_text="想看",
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    )
    repository.transition(
        saved.request.id,
        RequestStatus.FETCHED,
        note=NoteContext(
            id="note-m1",
            url="https://www.xiaohongshu.com/explore/note-m1",
            title="星际穿越",
        ),
    )
    repository.transition(saved.request.id, RequestStatus.RESOLVING)

    with pytest.raises(InvalidTransition, match="Cannot requeue RESOLVING"):
        service.reprocess(saved.request.id)

    assert xhs.fetch_note_calls == 0
    assert resolver.calls == 0
    assert moviepilot.calls == 0


def test_subscribed_pipeline_commits_each_state_before_external_calls(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, notifications = build_service(
        tmp_path / "assistant.db", enable_subscription=True
    )
    xhs.mentions = [mention()]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    results = service.poll_once()

    assert [result.status for result in results] == [RequestStatus.SUBSCRIBED]
    assert repository.recent(1)[0].status is RequestStatus.SUBSCRIBED
    assert repository.recent(1)[0].subscription_id == "42"
    assert xhs.note_call_statuses == [RequestStatus.NEW]
    assert resolver.call_statuses == [RequestStatus.RESOLVING]
    assert moviepilot.match_call_statuses == [RequestStatus.RESOLVING]
    assert moviepilot.submit_call_statuses == [RequestStatus.MATCHED]
    assert moviepilot.submit_calls[0][1] is True
    assert len(notifications) == 1


@pytest.mark.parametrize(
    ("stop_stage", "resolver_calls", "match_calls", "submit_calls"),
    [
        ("fetch", 0, 0, 0),
        ("resolve", 1, 0, 0),
        ("match", 1, 1, 0),
    ],
)
def test_cancellation_stops_new_external_work_at_each_pipeline_boundary(
    tmp_path: Path,
    stop_stage: str,
    resolver_calls: int,
    match_calls: int,
    submit_calls: int,
) -> None:
    stopped = Event()
    service, repository, xhs, resolver, moviepilot, notifications = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
        replies_enabled=True,
        is_cancelled=stopped.is_set,
    )
    xhs.mentions = [mention()]

    if stop_stage == "fetch":
        original = xhs.fetch_note

        def stop_after_fetch(item: TransientMention) -> NoteDetail:
            detail = original(item)
            stopped.set()
            return detail

        xhs.fetch_note = stop_after_fetch
    elif stop_stage == "resolve":
        original_resolve = resolver.resolve

        def stop_after_resolve(request: object) -> Resolution:
            resolved = original_resolve(request)
            stopped.set()
            return resolved

        resolver.resolve = stop_after_resolve
    else:
        original_match = moviepilot.match

        def stop_after_match(value: Resolution) -> MatchDecision:
            decision = original_match(value)
            stopped.set()
            return decision

        moviepilot.match = stop_after_match

    service.poll_once()

    assert resolver.calls == resolver_calls
    assert len(moviepilot.match_calls) == match_calls
    assert len(moviepilot.submit_calls) == submit_calls
    assert xhs.reply_calls == 0
    assert notifications == []
    assert repository.recent(1)[0].status is RequestStatus.NEW


def test_cancellation_after_submit_prevents_notification_and_public_reply(
    tmp_path: Path,
) -> None:
    stopped = Event()
    service, _, xhs, _, moviepilot, notifications = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
        replies_enabled=True,
        is_cancelled=stopped.is_set,
    )
    xhs.mentions = [mention()]
    original_submit = moviepilot.submit

    def stop_after_submit(
        decision: MatchDecision, enable_subscription: bool
    ) -> SubscriptionOutcome:
        outcome = original_submit(decision, enable_subscription)
        stopped.set()
        return outcome

    moviepilot.submit = stop_after_submit

    service.poll_once()

    assert len(moviepilot.submit_calls) == 1
    assert xhs.reply_calls == 0
    assert notifications == []


@pytest.mark.parametrize(
    "outcome_status",
    [
        RequestStatus.DRY_RUN_MATCHED,
        RequestStatus.ALREADY_IN_LIBRARY,
        RequestStatus.ALREADY_SUBSCRIBED,
    ],
)
def test_moviepilot_terminal_outcomes_are_persisted(
    tmp_path: Path, outcome_status: RequestStatus
) -> None:
    service, repository, xhs, _, moviepilot, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    moviepilot.submit_outcomes = [SubscriptionOutcome(status=outcome_status)]

    assert service.poll_once()[0].status is outcome_status
    assert repository.recent(1)[0].status is outcome_status
    assert len(notifications) == 1


@pytest.mark.parametrize(
    ("resolved", "expected_status"),
    [
        (resolution(status="need_confirmation", confidence=0.3), RequestStatus.NEED_CONFIRMATION),
        (resolution(status="not_media", confidence=0.99), RequestStatus.NOT_MEDIA),
        (resolution(confidence=0.849), RequestStatus.NEED_CONFIRMATION),
    ],
)
def test_resolution_short_circuits_never_call_moviepilot(
    tmp_path: Path, resolved: Resolution, expected_status: RequestStatus
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolved]

    assert service.poll_once()[0].status is expected_status
    assert repository.recent(1)[0].status is expected_status
    assert moviepilot.calls == 0


def test_multiple_resolved_titles_become_numbered_moviepilot_candidates(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, notifications = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
    )
    xhs.mentions = [mention()]
    suggestions = (
        domain_models.ResolutionCandidate(
            title="凪的新生活", media_type="tv", year=2019
        ),
        domain_models.ResolutionCandidate(
            title="我的事说来话长", media_type="tv", year=2019
        ),
        domain_models.ResolutionCandidate(
            title="平屋慢生活", media_type="tv", year=2025
        ),
        domain_models.ResolutionCandidate(
            title="吃饱睡好等幸福", media_type="tv", year=2025
        ),
    )
    resolver.outcomes = [
        Resolution(
            status="need_confirmation",
            media_type="unknown",
            confidence=0,
            reason="笔记同时推荐了四部作品",
            candidates=suggestions,
        )
    ]
    matches = tuple(
        MediaMatch(
            title=suggestion.title,
            original_title=suggestion.original_title,
            media_type=suggestion.media_type,
            year=suggestion.year,
            source="themoviedb",
            source_id=str(91000 + index),
            tmdb_id=91000 + index,
            score=1.0,
        )
        for index, suggestion in enumerate(suggestions, start=1)
    )
    moviepilot.match_outcomes = [
        MatchDecision(
            match=match,
            media_info=object(),
            reason_code="MATCHED",
        )
        for match in matches
    ]

    result = service.poll_once()[0]

    stored = repository.recent(1)[0]
    assert result.status is RequestStatus.NEED_CONFIRMATION
    assert [item.title for item in moviepilot.match_calls] == [
        "凪的新生活",
        "我的事说来话长",
        "平屋慢生活",
        "吃饱睡好等幸福",
    ]
    assert stored.candidates == matches
    assert stored.match_reason == "MULTIPLE_MEDIA"
    assert moviepilot.submit_calls == []
    assert notifications == [
        (
            "小红书影视助手",
            "🎬 识别到多部作品，请选择 MoviePilot 候选：\n"
            f"请求 #{stored.id}\n"
            "1. 凪的新生活（2019） · TV · TMDB\n"
            "2. 我的事说来话长（2019） · TV · TMDB\n"
            "3. 平屋慢生活（2025） · TV · TMDB\n"
            "4. 吃饱睡好等幸福（2025） · TV · TMDB\n"
            f"选择 1：/xhs_pick {stored.id} 1\n"
            f"选择 2：/xhs_pick {stored.id} 2\n"
            f"选择 3：/xhs_pick {stored.id} 3\n"
            f"选择 4：/xhs_pick {stored.id} 4",
        )
    ]


def test_multi_media_candidate_retries_without_an_unreliable_ai_year(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [
        Resolution(
            status="need_confirmation",
            media_type="unknown",
            confidence=0.8,
            reason="笔记同时推荐了多部作品",
            candidates=(
                domain_models.ResolutionCandidate(
                    title="平屋慢生活",
                    original_title="平屋日和",
                    media_type="tv",
                    year=2024,
                ),
            ),
        )
    ]
    corrected = MediaMatch(
        title="平屋慢生活",
        media_type="tv",
        year=2025,
        source="themoviedb",
        source_id="297207",
        tmdb_id=297207,
        score=0.8,
    )
    moviepilot.match_outcomes = [
        MatchDecision(reason_code="NO_MATCH"),
        MatchDecision(
            match=corrected,
            media_info=object(),
            reason_code="MATCHED",
        ),
    ]

    result = service.poll_once()[0]

    assert result.status is RequestStatus.NEED_CONFIRMATION
    assert [call.year for call in moviepilot.match_calls] == [2024, None]
    assert repository.recent(1)[0].candidates == (corrected,)


def test_confidence_equal_to_threshold_is_eligible_for_matching(tmp_path: Path) -> None:
    service, _, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db", confidence_threshold=0.85
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(confidence=0.85)]

    service.poll_once()

    assert len(moviepilot.match_calls) == 1


def test_ambiguous_match_needs_confirmation_without_submit(tmp_path: Path) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    moviepilot.match_outcomes = [MatchDecision(reason_code="AMBIGUOUS_RESULTS")]

    result = service.poll_once()[0]

    assert result.status is RequestStatus.NEED_CONFIRMATION
    assert repository.recent(1)[0].status is RequestStatus.NEED_CONFIRMATION
    assert moviepilot.submit_calls == []


def test_resolved_media_without_moviepilot_results_has_specific_notification(
    tmp_path: Path,
) -> None:
    service, repository, xhs, _, moviepilot, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    moviepilot.match_outcomes = [MatchDecision(reason_code="NO_MATCH")]

    result = service.poll_once()[0]

    request_id = repository.recent(1)[0].id
    assert result.status is RequestStatus.NEED_CONFIRMATION
    assert notifications == [
        (
            "小红书影视助手",
            "⚠️ MoviePilot 未找到匹配结果\n"
            "《星际穿越》\n"
            "2014 · Movie\n"
            "小红书：星际穿越\n"
            "https://www.xiaohongshu.com/explore/note-m1\n"
            "请发送明确的确认命令：\n"
            f"/xhs_confirm {request_id} 片名 年份 电影/剧集",
        )
    ]


def test_ambiguous_match_persists_distinct_candidates_and_notifies_numbers(
    tmp_path: Path,
) -> None:
    service, repository, xhs, _, moviepilot, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    candidates = (
        MediaMatch(
            title="掉链子刑警",
            original_title="おしい刑事",
            media_type="tv",
            year=2019,
            source="themoviedb",
            source_id="93230",
            tmdb_id=93230,
            score=0.8,
        ),
        MediaMatch(
            title="果然是掉链子刑警",
            original_title="やっぱりおしい刑事",
            media_type="tv",
            year=2021,
            source="themoviedb",
            source_id="120350",
            tmdb_id=120350,
            score=0.65,
        ),
    )
    moviepilot.match_outcomes = [
        MatchDecision(reason_code="AMBIGUOUS_RESULTS", candidates=candidates)
    ]

    result = service.poll_once()[0]

    stored = repository.recent(1)[0]
    assert result.status is RequestStatus.NEED_CONFIRMATION
    assert stored.candidates == candidates
    assert notifications == [
        (
            "小红书影视助手",
            "🎬 已识别《星际穿越》，请选择 MoviePilot 候选：\n"
            f"请求 #{stored.id}\n"
            "1. 掉链子刑警（2019） · TV · TMDB\n"
            "2. 果然是掉链子刑警（2021） · TV · TMDB\n"
            f"选择 1：/xhs_pick {stored.id} 1\n"
            f"选择 2：/xhs_pick {stored.id} 2",
        )
    ]


def test_candidate_confirmation_submits_selected_identity_without_llm(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
    )
    xhs.mentions = [mention()]
    first = matched_decision().match
    assert first is not None
    second = first.model_copy(
        update={
            "title": "星际穿越续集",
            "year": 2026,
            "source_id": "999999",
            "tmdb_id": 999999,
        }
    )
    moviepilot.match_outcomes = [
        MatchDecision(reason_code="AMBIGUOUS_RESULTS", candidates=(first, second))
    ]
    service.poll_once()
    request_id = repository.recent(1)[0].id
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    result = service.confirm_candidate(request_id, 2)

    assert result.status is RequestStatus.SUBSCRIBED
    assert result.match == second
    assert moviepilot.select_calls == [second]
    assert len(resolver.confirmation_requests) == 0
    assert repository.get(request_id).subscription_id == "42"  # type: ignore[union-attr]


@pytest.mark.parametrize("selection", [0, 3])
def test_candidate_confirmation_rejects_invalid_number_without_state_change(
    tmp_path: Path,
    selection: int,
) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    candidate = matched_decision().match
    assert candidate is not None
    moviepilot.match_outcomes = [
        MatchDecision(reason_code="AMBIGUOUS_RESULTS", candidates=(candidate,))
    ]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    with pytest.raises(ValueError, match="candidate"):
        service.confirm_candidate(request_id, selection)

    assert repository.get(request_id).status is RequestStatus.NEED_CONFIRMATION  # type: ignore[union-attr]
    assert moviepilot.select_calls == []
    assert moviepilot.submit_calls == []


def test_request_failure_is_sanitized_and_next_mention_continues(tmp_path: Path) -> None:
    service, repository, xhs, resolver, _, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention(mention_id="m1"), mention(mention_id="m2")]
    resolver.outcomes = [
        ResolverError("https://provider.invalid?api_key=secret"),
        resolution(),
    ]

    results = service.poll_once()

    assert [result.status for result in results] == [
        RequestStatus.FAILED,
        RequestStatus.DRY_RUN_MATCHED,
    ]
    requests = repository.recent(10)
    assert {request.mention_id: request.status for request in requests} == {
        "m1": RequestStatus.FAILED,
        "m2": RequestStatus.DRY_RUN_MATCHED,
    }
    assert "secret" not in (tmp_path / "assistant.db").read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert len(notifications) == 2


@pytest.mark.parametrize("failure_stage", ["match", "submit"])
def test_moviepilot_errors_become_failed_without_exposing_exception(
    tmp_path: Path, failure_stage: str
) -> None:
    service, repository, xhs, _, moviepilot, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    secret_error = RuntimeError("secret-key")
    if failure_stage == "match":
        moviepilot.match_outcomes = [secret_error]
    else:
        moviepilot.submit_outcomes = [secret_error]

    assert service.poll_once()[0].status is RequestStatus.FAILED
    assert repository.recent(1)[0].error == "UPSTREAM_ERROR"
    assert "secret-key" not in repr(notifications)


def test_note_contract_error_fails_only_that_request_and_continues(tmp_path: Path) -> None:
    service, repository, xhs, _, _, _ = build_service(tmp_path / "assistant.db")
    xhs.mentions = [mention(mention_id="m1"), mention(mention_id="m2")]
    xhs.note_failures["m1"] = XhsContractError("raw browser details")

    results = service.poll_once()

    assert [result.status for result in results] == [
        RequestStatus.FAILED,
        RequestStatus.DRY_RUN_MATCHED,
    ]
    assert repository.get_runtime_state().browser_state is BrowserState.READY


@pytest.mark.parametrize("success", [True, False])
def test_reply_is_attempted_once_and_persisted_as_final(
    tmp_path: Path, success: bool
) -> None:
    service, repository, xhs, _, moviepilot, notifications = build_service(
        tmp_path / "assistant.db", enable_subscription=True, replies_enabled=True
    )
    xhs.mentions = [mention()]
    xhs.reply_outcomes = [
        ReplyOutcome(
            success=success,
            reply_id="reply-m1" if success else None,
            code=None if success else "TIMEOUT",
        )
    ]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    service.poll_once()
    service.poll_once()

    stored = repository.recent(1)[0]
    assert xhs.reply_calls == 1
    assert xhs.reply_call_statuses == [RequestStatus.SUBSCRIBED]
    assert stored.reply_status is (ReplyStatus.SENT if success else ReplyStatus.FAILED)
    assert stored.reply_id == ("reply-m1" if success else None)
    assert stored.reply_error_code == (None if success else "TIMEOUT")
    assert len(notifications) == (1 if success else 2)
    if not success:
        assert "TIMEOUT" in notifications[-1][1]


def test_reply_success_without_returned_id_is_not_persisted_as_sent(
    tmp_path: Path,
) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db", enable_subscription=True, replies_enabled=True
    )
    xhs.mentions = [mention()]
    xhs.reply_outcomes = [ReplyOutcome(success=True)]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    service.poll_once()

    stored = repository.recent(1)[0]
    assert stored.reply_status is ReplyStatus.FAILED
    assert stored.reply_id is None


def test_ui_confirmed_reply_without_returned_id_is_persisted_as_sent(
    tmp_path: Path,
) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db", enable_subscription=True, replies_enabled=True
    )
    xhs.mentions = [mention()]
    xhs.reply_outcomes = [ReplyOutcome(success=True, code="UI_CONFIRMED")]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    service.poll_once()

    stored = repository.recent(1)[0]
    assert stored.reply_status is ReplyStatus.SENT
    assert stored.reply_id is None
    assert stored.reply_error_code is None


def test_reply_probe_reports_exact_stage_without_changing_delivery_state(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, _, _ = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]
    service.poll_once()
    stored = repository.recent(1)[0]
    xhs.reply_probe_outcomes = [
        ReplyOutcome(
            success=False,
            code="NOTIFICATION_MISMATCH",
            message="The notification list did not match",
        )
    ]

    outcome = service.check_reply_target(stored.id)

    assert outcome.code == "NOTIFICATION_MISMATCH"
    assert xhs.reply_probe_calls == 1
    assert xhs.fetch_mentions_calls == 1
    assert xhs.reply_probe_mentions[0].xsec_token == ""
    assert repository.get(stored.id).reply_status is ReplyStatus.PENDING


def test_reply_exception_is_not_retried(tmp_path: Path) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db", enable_subscription=True, replies_enabled=True
    )
    xhs.mentions = [mention()]
    xhs.reply_outcomes = [RuntimeError("secret reply failure")]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    service.poll_once()
    service.poll_once()

    assert xhs.reply_calls == 1
    assert repository.recent(1)[0].reply_status is ReplyStatus.FAILED


def test_paused_error_notifies_once_and_resume_allows_polling(tmp_path: Path) -> None:
    service, repository, xhs, _, _, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mention_failures = [XhsPausedError("AUTH_REQUIRED")]

    assert service.poll_once() == []
    assert service.poll_once() == []

    paused = repository.get_runtime_state()
    assert paused.browser_state is BrowserState.PAUSED
    assert paused.pause_code == "AUTH_REQUIRED"
    assert paused.paused_at is not None
    assert paused.pause_notified is True
    assert xhs.fetch_mentions_calls == 1
    assert len(notifications) == 1

    service.resume()
    assert repository.get_runtime_state().browser_state is BrowserState.READY
    assert service.poll_once() == []
    assert xhs.fetch_mentions_calls == 2


def test_note_pause_fails_current_request_and_stops_the_batch(tmp_path: Path) -> None:
    service, repository, xhs, resolver, _, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention(mention_id="m1"), mention(mention_id="m2")]
    xhs.note_failures["m1"] = XhsPausedError("RATE_LIMITED")

    results = service.poll_once()

    assert [result.status for result in results] == [RequestStatus.FAILED]
    assert resolver.calls == 0
    assert [request.mention_id for request in repository.recent(10)] == ["m1"]
    assert repository.get_runtime_state().pause_code == "RATE_LIMITED"
    assert notifications == [
        (
            "小红书影视助手异常",
            "⚠️ 小红书影视助手异常\n检测到小红书风控，监听已暂停，请人工检查。",
        ),
        (
            "小红书影视助手",
            "⚠️ 处理失败\n"
            "https://www.xiaohongshu.com/explore/note-m1\n"
            "处理失败，请在插件详情中查看。",
        ),
    ]


def test_three_consecutive_contract_failures_pause_but_success_resets_count(
    tmp_path: Path,
) -> None:
    service, repository, xhs, _, _, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mention_failures = [
        XhsContractError("one"),
        XhsContractError("two"),
    ]
    service.poll_once()
    service.poll_once()
    service.poll_once()
    assert repository.get_runtime_state().browser_state is BrowserState.READY

    xhs.mention_failures = [
        XhsContractError("one"),
        XhsContractError("two"),
        XhsContractError("three"),
    ]
    service.poll_once()
    service.poll_once()
    service.poll_once()
    service.poll_once()

    assert repository.get_runtime_state().browser_state is BrowserState.PAUSED
    assert repository.get_runtime_state().pause_code == "BROWSER_UNAVAILABLE"
    assert xhs.fetch_mentions_calls == 6
    assert len(notifications) == 1


def test_temporary_reply_failure_does_not_pause_polling(tmp_path: Path) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
        replies_enabled=True,
    )
    xhs.mentions = [mention()]
    xhs.reply_outcomes = [
        ReplyOutcome(success=False, code="TEMPORARY_FAILURE")
    ]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    service.poll_once()

    stored = repository.recent(1)[0]
    assert stored.reply_status is ReplyStatus.FAILED
    assert repository.get_runtime_state().browser_state is BrowserState.READY


def test_reprocess_requeues_failed_request_without_persisting_token(tmp_path: Path) -> None:
    service, repository, xhs, resolver, _, _ = build_service(tmp_path / "assistant.db")
    xhs.mentions = [mention()]
    resolver.outcomes = [ResolverError("secret"), resolution()]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    result = service.reprocess(request_id)

    assert result.status is RequestStatus.DRY_RUN_MATCHED
    assert resolver.calls == 2
    assert xhs.fetch_note_calls == 1
    assert isinstance(resolver.requests[1], MediaRequest)
    assert resolver.requests[1].note.title == "星际穿越"
    assert resolver.requests[1].note.content == "2014 年电影"
    assert repository.get(request_id).attempt_count == 1  # type: ignore[union-attr]
    assert b"transient-token" not in (tmp_path / "assistant.db").read_bytes()


def test_reprocess_sanitizes_persisted_trigger_comment_like_initial_processing(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, _, _ = build_service(tmp_path / "assistant.db")
    raw_comment = " \x00想看\x07" + "片" * 1200 + "\x1f尾 "
    expected_comment = "想看" + "片" * 998
    xhs.mentions = [mention(comment_text=raw_comment)]
    resolver.outcomes = [ResolverError("first attempt"), resolution()]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    service.reprocess(request_id)

    assert len(resolver.requests) == 2
    assert all(isinstance(request, MediaRequest) for request in resolver.requests)
    assert [request.trigger_comment for request in resolver.requests] == [
        expected_comment,
        expected_comment,
    ]


def test_reprocess_without_snapshot_fails_without_accessing_xhs(tmp_path: Path) -> None:
    service, repository, xhs, resolver, moviepilot, notifications = build_service(
        tmp_path / "assistant.db"
    )
    saved = repository.save_mention(
        NewMention(
            note_id="legacy-note",
            note_url="https://www.xiaohongshu.com/explore/legacy-note",
            mention_id="legacy-mention",
            sender_user_id="authorized-user",
            comment_id="legacy-comment",
            comment_text="想看",
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    )
    repository.transition(saved.request.id, RequestStatus.FAILED, error="UPSTREAM_ERROR")

    result = service.reprocess(saved.request.id)

    stored = repository.get(saved.request.id)
    assert result.status is RequestStatus.FAILED
    assert stored is not None
    assert stored.status is RequestStatus.FAILED
    assert stored.error == "UPSTREAM_ERROR"
    assert stored.attempt_count == 1
    assert xhs.fetch_note_calls == 0
    assert resolver.calls == 0
    assert moviepilot.calls == 0
    assert len(notifications) == 1


def test_ignore_requeues_a_confirmation_before_marking_ignored(tmp_path: Path) -> None:
    service, repository, xhs, resolver, _, _ = build_service(tmp_path / "assistant.db")
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    ignored = service.ignore(request_id)

    assert ignored.status is RequestStatus.IGNORED
    assert repository.get(request_id).status is RequestStatus.IGNORED  # type: ignore[union-attr]


def test_manual_resolution_skips_xhs_and_llm_but_still_matches_and_submits(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    result = service.manual_resolve(request_id, resolution())

    assert result.status is RequestStatus.DRY_RUN_MATCHED
    assert xhs.fetch_note_calls == 1
    assert resolver.calls == 1
    assert len(moviepilot.match_calls) == 1
    assert len(moviepilot.submit_calls) == 1


def test_manual_resolution_replies_again_for_the_new_processing_attempt(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
        replies_enabled=True,
    )
    xhs.mentions = [mention()]
    xhs.reply_outcomes = [
        ReplyOutcome(success=True, reply_id="reply-confirmation"),
        ReplyOutcome(success=True, reply_id="reply-subscribed"),
    ]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    result = service.manual_resolve(request_id, resolution())

    stored = repository.get(request_id)
    assert result.status is RequestStatus.SUBSCRIBED
    assert xhs.fetch_mentions_calls == 2
    assert xhs.reply_call_statuses == [
        RequestStatus.NEED_CONFIRMATION,
        RequestStatus.SUBSCRIBED,
    ]
    assert stored is not None
    assert stored.reply_status is ReplyStatus.SENT
    assert stored.reply_id == "reply-subscribed"
    assert b"transient-token" not in (tmp_path / "assistant.db").read_bytes()


def test_pending_terminal_result_can_be_replied_after_replies_are_enabled(
    tmp_path: Path,
) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
        replies_enabled=False,
    )
    xhs.mentions = [mention()]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]
    service.poll_once()
    request_id = repository.recent(1)[0].id
    service.replies_enabled = True
    xhs.reply_outcomes = [ReplyOutcome(success=True, reply_id="reply-late")]

    replied = service.reply(request_id)

    assert replied.reply_status is ReplyStatus.SENT
    assert replied.reply_id == "reply-late"
    assert xhs.fetch_mentions_calls == 2
    assert xhs.reply_call_statuses == [RequestStatus.SUBSCRIBED]


def test_need_confirmation_requests_wechat_input_for_the_durable_request(
    tmp_path: Path,
) -> None:
    confirmations: list[int] = []
    service, repository, xhs, resolver, _, notifications = build_service(
        tmp_path / "assistant.db",
        request_confirmation=lambda request_id, _title, _text: confirmations.append(
            request_id
        ),
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]

    result = service.poll_once()[0]

    request_id = repository.recent(1)[0].id
    assert result.status is RequestStatus.NEED_CONFIRMATION
    assert confirmations == [request_id]
    assert notifications == [
        (
            "小红书影视助手",
            "⚠️ 无法确定影视作品\n"
            f"请求 #{request_id}\n"
            "小红书：星际穿越\n"
            "https://www.xiaohongshu.com/explore/note-m1\n"
            "请发送明确的确认命令：\n"
            f"/xhs_confirm {request_id} 片名 年份 电影/剧集",
        )
    ]


def test_confirmation_input_is_not_requested_when_notifications_are_disabled(
    tmp_path: Path,
) -> None:
    confirmations: list[int] = []
    service, _, xhs, resolver, _, notifications = build_service(
        tmp_path / "assistant.db",
        notifications_enabled=False,
        request_confirmation=lambda request_id, _title, _text: confirmations.append(
            request_id
        ),
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]

    assert service.poll_once()[0].status is RequestStatus.NEED_CONFIRMATION
    assert confirmations == []
    assert notifications == []


def test_confirmation_input_is_not_requested_when_notification_delivery_fails(
    tmp_path: Path,
) -> None:
    confirmations: list[int] = []

    def broken_notify(_title: str, _text: str) -> None:
        raise RuntimeError("notification unavailable")

    service, repository, xhs, resolver, _, _ = build_service(
        tmp_path / "assistant.db",
        notify=broken_notify,
        request_confirmation=lambda request_id, _title, _text: confirmations.append(
            request_id
        ),
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]

    assert service.poll_once()[0].status is RequestStatus.NEED_CONFIRMATION
    assert confirmations == []
    pending = repository.pending_notifications(20)
    assert [(event.code, event.attempt_count) for event in pending] == [
        ("NEED_CONFIRMATION", 1)
    ]


def test_wechat_clarification_reuses_the_manual_confirmation_pipeline(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]
    resolver.confirmation_outcomes = [resolution()]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    result = service.confirm_from_text(
        request_id,
        "星际穿越，2014，电影",
    )

    stored = repository.get(request_id)
    assert result.status is RequestStatus.SUBSCRIBED
    assert stored is not None
    assert stored.status is RequestStatus.SUBSCRIBED
    assert stored.subscription_id == "42"
    assert len(resolver.confirmation_requests) == 1
    media_request, clarification = resolver.confirmation_requests[0]
    assert isinstance(media_request, MediaRequest)
    assert media_request.note.title == "星际穿越"
    assert clarification == "星际穿越，2014，电影"


def test_wechat_clarification_only_accepts_a_pending_authorized_confirmation(
    tmp_path: Path,
) -> None:
    service, repository, xhs, _, _, _ = build_service(tmp_path / "assistant.db")
    xhs.mentions = [mention()]
    service.poll_once()
    request_id = repository.recent(1)[0].id

    with pytest.raises(InvalidTransition, match="NEED_CONFIRMATION"):
        service.confirm_from_text(request_id, "星际穿越，2014，电影")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sender_user_id", "other-user"),
        ("comment_id", "other-comment"),
        ("note_id", "other-note"),
    ],
)
def test_reply_refuses_a_notification_whose_durable_identity_changed(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
        replies_enabled=False,
    )
    original = mention()
    xhs.mentions = [original]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]
    service.poll_once()
    request_id = repository.recent(1)[0].id
    service.replies_enabled = True
    xhs.mentions = [replace(original, **{field: value})]

    replied = service.reply(request_id)

    assert replied.status is RequestStatus.SUBSCRIBED
    assert replied.reply_status is ReplyStatus.FAILED
    assert xhs.reply_calls == 0


def test_manual_resolution_keeps_the_subscription_when_reply_refresh_fails(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        enable_subscription=True,
        replies_enabled=False,
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]
    service.poll_once()
    request_id = repository.recent(1)[0].id
    service.replies_enabled = True
    xhs.mention_failures = [XhsContractError("notification unavailable")]

    result = service.manual_resolve(request_id, resolution())

    stored = repository.get(request_id)
    assert result.status is RequestStatus.SUBSCRIBED
    assert stored is not None
    assert stored.status is RequestStatus.SUBSCRIBED
    assert stored.subscription_id == "42"
    assert stored.reply_status is ReplyStatus.FAILED
    assert xhs.reply_calls == 0


def test_manual_resolution_rechecks_current_authorization_before_moviepilot(
    tmp_path: Path,
) -> None:
    service, repository, xhs, resolver, moviepilot, _ = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="need_confirmation", confidence=0.2)]
    service.poll_once()
    request_id = repository.recent(1)[0].id
    service.authorized_user_ids = frozenset()

    with pytest.raises(PermissionError, match="authorized sender"):
        service.manual_resolve(request_id, resolution())

    assert moviepilot.calls == 0


def test_notification_failure_does_not_change_committed_result(tmp_path: Path) -> None:
    def broken_notify(title: str, text: str) -> None:
        raise RuntimeError("notification unavailable")

    service, repository, xhs, _, _, _ = build_service(
        tmp_path / "assistant.db", notify=broken_notify
    )
    xhs.mentions = [mention()]

    assert service.poll_once()[0].status is RequestStatus.DRY_RUN_MATCHED
    assert repository.recent(1)[0].status is RequestStatus.DRY_RUN_MATCHED


def test_dry_run_notification_describes_the_matched_media(tmp_path: Path) -> None:
    service, _, xhs, resolver, moviepilot, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [
        Resolution(
            status="resolved",
            title="家族的形式",
            original_title="家族ノカタチ",
            media_type="tv",
            year=2016,
            season=1,
            confidence=0.98,
        )
    ]
    moviepilot.match_outcomes = [
        MatchDecision(
            match=MediaMatch(
                title="家族的形式",
                original_title="家族ノカタチ",
                media_type="tv",
                year=2016,
                season=1,
                source="tmdb",
                source_id="68983",
                tmdb_id=68983,
                score=1.0,
            ),
            media_info=object(),
            reason_code="MATCHED",
        )
    ]

    service.poll_once()

    assert notifications == [
        (
            "小红书影视助手",
            "🎬 已识别（测试模式）\n"
            "《家族的形式》\n"
            "2016 · TV · 第 1 季\n"
            "MoviePilot：匹配成功，未创建订阅",
        )
    ]


def test_not_media_notification_uses_note_context_without_tokens(tmp_path: Path) -> None:
    service, _, xhs, resolver, _, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mentions = [mention()]
    resolver.outcomes = [resolution(status="not_media", confidence=0.99)]

    service.poll_once()

    assert notifications == [
        (
            "小红书影视助手",
            "⚠️ 无法确定影视作品\n"
            "小红书：星际穿越\n"
            "https://www.xiaohongshu.com/explore/note-m1\n"
            "需要人工确认。",
        )
    ]


@pytest.mark.parametrize(
    ("status", "subscription_id", "expected_text"),
    [
        (
            RequestStatus.SUBSCRIBED,
            "42",
            "🎬 已添加订阅\n"
            "《星际穿越》\n"
            "2014 · Movie\n"
            "MoviePilot：订阅成功",
        ),
        (
            RequestStatus.ALREADY_SUBSCRIBED,
            None,
            "🎬 已存在\n"
            "《星际穿越》\n"
            "2014 · Movie\n"
            "已经订阅，无需重复添加。",
        ),
        (
            RequestStatus.ALREADY_IN_LIBRARY,
            None,
            "🎬 已存在\n"
            "《星际穿越》\n"
            "2014 · Movie\n"
            "已经在媒体库中，无需重复添加。",
        ),
        (
            RequestStatus.FAILED,
            None,
            "⚠️ 处理失败\n"
            "《星际穿越》\n"
            "2014 · Movie\n"
            "处理失败，请在插件详情中查看。",
        ),
    ],
)
def test_subscription_notification_explains_the_outcome(
    tmp_path: Path,
    status: RequestStatus,
    subscription_id: str | None,
    expected_text: str,
) -> None:
    service, _, xhs, _, moviepilot, notifications = build_service(
        tmp_path / "assistant.db", enable_subscription=True
    )
    xhs.mentions = [mention()]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=status, subscription_id=subscription_id)
    ]

    service.poll_once()

    assert notifications == [("小红书影视助手", expected_text)]


def test_login_pause_notification_explains_the_required_action(tmp_path: Path) -> None:
    service, _, xhs, _, _, notifications = build_service(
        tmp_path / "assistant.db"
    )
    xhs.mention_failures = [XhsPausedError("AUTH_REQUIRED")]

    service.poll_once()

    assert notifications == [
        (
            "小红书影视助手异常",
            "⚠️ 小红书影视助手异常\n登录状态失效，请重新登录。",
        )
    ]


def test_cancellation_after_terminal_commit_leaves_result_outbox_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = Event()
    service, repository, xhs, _, _, _ = build_service(
        tmp_path / "assistant.db", is_cancelled=cancelled.is_set
    )
    original_transition = repository.transition

    def cancel_after_terminal_transition(request_id: int, target: RequestStatus, **kwargs):
        stored = original_transition(request_id, target, **kwargs)
        if target is RequestStatus.DRY_RUN_MATCHED:
            cancelled.set()
        return stored

    monkeypatch.setattr(repository, "transition", cancel_after_terminal_transition)
    xhs.mentions = [mention()]

    assert service.poll_once()[0].status is RequestStatus.DRY_RUN_MATCHED
    assert repository.recent(1)[0].status is RequestStatus.DRY_RUN_MATCHED
    pending = repository.pending_notifications()
    assert [(event.kind, event.code) for event in pending] == [
        ("REQUEST_RESULT", "DRY_RUN_MATCHED")
    ]


def test_pause_event_is_delivered_after_disabled_business_backlog(tmp_path: Path) -> None:
    deliveries: list[tuple[str, str]] = []
    service, repository, _, _, _, _ = build_service(
        tmp_path / "assistant.db",
        notifications_enabled=False,
        notify=lambda title, text: deliveries.append((title, text)),
    )
    saved = repository.save_mention(
        NewMention(
            note_id="note-backlog",
            note_url="https://www.xiaohongshu.com/explore/note-backlog",
            mention_id="mention-backlog",
            sender_user_id="authorized-user",
            comment_id="comment-backlog",
            comment_text="想看",
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    )
    for attempt in range(21):
        repository.enqueue_notification(
            "REQUEST_RESULT",
            request_id=saved.request.id,
            code="DRY_RUN_MATCHED",
            dedupe_key=f"business-backlog:{attempt}",
        )
    repository.enqueue_notification(
        "PAUSE",
        request_id=None,
        code="AUTH_REQUIRED",
        dedupe_key="pause-after-backlog",
    )

    service.poll_once()

    assert deliveries == [
        (
            "小红书影视助手异常",
            "⚠️ 小红书影视助手异常\n登录状态失效，请重新登录。",
        )
    ]


def test_terminal_notification_failure_retries_after_restart_once(
    tmp_path: Path,
) -> None:
    attempts: list[tuple[str, str]] = []

    def flaky_notify(title: str, text: str) -> None:
        attempts.append((title, text))
        if len(attempts) == 1:
            raise RuntimeError("notification unavailable")

    database_path = tmp_path / "assistant.db"
    service, repository, xhs, _, _, _ = build_service(
        database_path,
        notify=flaky_notify,
    )
    xhs.mentions = [mention()]

    assert service.poll_once()[0].status is RequestStatus.DRY_RUN_MATCHED
    pending = repository.pending_notifications(20)
    assert len(pending) == 1
    assert pending[0].code == "DRY_RUN_MATCHED"
    assert pending[0].attempt_count == 1

    restarted, restarted_repository, _, _, _, _ = build_service(
        database_path,
        notify=flaky_notify,
    )
    restarted.poll_once()
    restarted.poll_once()

    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert restarted_repository.pending_notifications(20) == []


def test_pause_notification_retries_when_business_notifications_are_disabled(
    tmp_path: Path,
) -> None:
    attempts: list[tuple[str, str]] = []

    def flaky_notify(title: str, text: str) -> None:
        attempts.append((title, text))
        if len(attempts) == 1:
            raise RuntimeError("notification unavailable")

    service, repository, xhs, _, _, _ = build_service(
        tmp_path / "assistant.db",
        notify=flaky_notify,
        notifications_enabled=False,
    )
    xhs.mention_failures = [XhsPausedError("AUTH_REQUIRED")]

    service.poll_once()
    failed = repository.get_runtime_state()
    assert failed.browser_state is BrowserState.PAUSED
    assert failed.pause_notified is False
    assert repository.pending_notifications(20)[0].attempt_count == 1

    service.poll_once()
    service.poll_once()

    delivered = repository.get_runtime_state()
    assert delivered.pause_notified is True
    assert len(attempts) == 2
    assert repository.pending_notifications(20) == []
    assert xhs.fetch_mentions_calls == 1


def test_paused_poll_recreates_missing_safety_outbox_event(tmp_path: Path) -> None:
    delivered: list[tuple[str, str]] = []
    service, repository, xhs, _, _, _ = build_service(
        tmp_path / "assistant.db",
        notify=lambda title, text: delivered.append((title, text)),
    )
    xhs.mention_failures = [XhsPausedError("AUTH_REQUIRED")]
    original_enqueue = repository.enqueue_notification
    enqueue_attempts = 0

    def fail_first_two_enqueues(*args, **kwargs):
        nonlocal enqueue_attempts
        enqueue_attempts += 1
        if enqueue_attempts <= 2:
            raise RuntimeError("database temporarily unavailable")
        return original_enqueue(*args, **kwargs)

    repository.enqueue_notification = fail_first_two_enqueues  # type: ignore[method-assign]

    service.poll_once()
    assert repository.get_runtime_state().pause_notified is False
    assert delivered == []

    service.poll_once()

    assert repository.get_runtime_state().pause_notified is True
    assert len(delivered) == 1
    assert repository.pending_notifications(20) == []


def test_business_notification_disablement_leaves_no_pending_result_event(
    tmp_path: Path,
) -> None:
    attempts: list[tuple[str, str]] = []
    service, repository, xhs, _, moviepilot, _ = build_service(
        tmp_path / "assistant.db",
        notify=lambda title, text: attempts.append((title, text)),
        notifications_enabled=False,
        enable_subscription=True,
        replies_enabled=True,
    )
    xhs.mentions = [mention()]
    xhs.reply_outcomes = [ReplyOutcome(success=False, code="TEMPORARY_FAILURE")]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    assert service.poll_once()[0].status is RequestStatus.SUBSCRIBED
    assert repository.recent(1)[0].reply_status is ReplyStatus.FAILED

    assert attempts == []
    assert repository.pending_notifications(20) == []


def test_constructor_rejects_out_of_range_confidence_threshold(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="confidence_threshold"):
        AssistantService(
            repository=RequestRepository(tmp_path / "assistant.db"),
            xhs=FakeXhs(),
            resolver=FakeResolver(),
            moviepilot=FakeMoviePilot(),
            authorized_user_ids={"authorized-user"},
            confidence_threshold=1.1,
        )

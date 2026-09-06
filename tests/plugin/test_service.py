from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from xhsmovieassistant.models import (
    BrowserState,
    MediaMatch,
    MediaRequest,
    ReplyStatus,
    RequestStatus,
    Resolution,
)
from xhsmovieassistant.moviepilot import MatchDecision, SubscriptionOutcome
from xhsmovieassistant.repository import NewMention, RequestRepository
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
        self.fetch_mentions_calls = 0
        self.fetch_note_calls = 0
        self.reply_calls = 0
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
            return ReplyOutcome(success=True)
        outcome = self.reply_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeResolver:
    def __init__(self) -> None:
        self.outcomes: list[Resolution | Exception] = []
        self.requests: list[object] = []
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


class FakeMoviePilot:
    def __init__(self) -> None:
        self.match_outcomes: list[MatchDecision | Exception] = []
        self.submit_outcomes: list[SubscriptionOutcome | Exception] = []
        self.match_calls: list[Resolution] = []
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
        confidence_threshold=confidence_threshold,
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
    xhs.reply_outcomes = [ReplyOutcome(success=success, code=None if success else "TIMEOUT")]
    moviepilot.submit_outcomes = [
        SubscriptionOutcome(status=RequestStatus.SUBSCRIBED, subscription_id="42")
    ]

    service.poll_once()
    service.poll_once()

    stored = repository.recent(1)[0]
    assert xhs.reply_calls == 1
    assert xhs.reply_call_statuses == [RequestStatus.SUBSCRIBED]
    assert stored.reply_status is (ReplyStatus.SENT if success else ReplyStatus.FAILED)
    assert stored.reply_id == ("comment-m1" if success else None)
    assert len(notifications) == (1 if success else 2)


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
    assert len(notifications) == 1


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

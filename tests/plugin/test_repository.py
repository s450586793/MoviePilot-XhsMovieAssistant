import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from xhsmovieassistant.models import (
    BrowserState,
    MediaMatch,
    ReplyStatus,
    RequestStatus,
    Resolution,
)
from xhsmovieassistant.repository import (
    InvalidTransition,
    NewMention,
    RequestNotFound,
    RequestRepository,
)


def make_mention(
    mention_id: str = "mention-1", comment_text: str = "想看星际穿越"
) -> NewMention:
    return NewMention(
        note_id="note-1",
        note_url="https://www.xiaohongshu.com/explore/note-1",
        mention_id=mention_id,
        sender_user_id="user-1",
        comment_id="comment-1",
        comment_text=comment_text,
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )


def test_save_mention_is_idempotent_by_mention_and_request_key(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")

    first = repo.save_mention(make_mention(mention_id="m1"))
    same_mention = repo.save_mention(make_mention(mention_id="m1"))
    same_request = repo.save_mention(make_mention(mention_id="m2"))

    assert first.created is True
    assert same_mention.created is False
    assert same_request.created is False
    assert len(repo.recent(10)) == 1


def test_transition_rejects_invalid_state_jump(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    with pytest.raises(InvalidTransition):
        repo.transition(saved.request.id, RequestStatus.SUBSCRIBED)


def test_transition_persists_resolution_match_and_explicit_enums(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())
    repo.transition(saved.request.id, RequestStatus.FETCHED)
    repo.transition(saved.request.id, RequestStatus.RESOLVING)
    updated = repo.transition(
        saved.request.id,
        RequestStatus.MATCHED,
        resolution=Resolution(
            status="resolved",
            title="Interstellar",
            original_title="Interstellar",
            media_type="movie",
            year=2014,
            confidence=0.99,
        ),
        match=MediaMatch(
            title="Interstellar",
            original_title="Interstellar",
            media_type="movie",
            year=2014,
            source="tmdb",
            source_id="157336",
            tmdb_id=157336,
            score=0.99,
        ),
    )

    assert updated.status is RequestStatus.MATCHED
    assert updated.title == "Interstellar"
    assert updated.media_source == "tmdb"
    assert repo.get(saved.request.id) == updated

    with pytest.raises(TypeError):
        repo.transition(saved.request.id, "FAILED")  # type: ignore[arg-type]


def test_requeue_requires_authentication_and_only_permitted_terminal_states(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())
    failed = repo.transition(saved.request.id, RequestStatus.FAILED, error="temporary")

    with pytest.raises(PermissionError):
        repo.requeue(saved.request.id, authenticated=False)

    requeued = repo.requeue(saved.request.id, authenticated=True)
    assert requeued.status is RequestStatus.NEW
    assert requeued.error is None
    assert requeued.attempt_count == failed.attempt_count + 1

    with pytest.raises(InvalidTransition):
        repo.requeue(saved.request.id, authenticated=True)


def test_recover_interrupted_resets_only_stale_in_progress_requests(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    repo = RequestRepository(tmp_path / "app.db")
    stale = repo.save_mention(make_mention("stale"))
    fresh = repo.save_mention(make_mention("fresh", "想看沙丘"))
    terminal = repo.save_mention(make_mention("terminal", "想看盗梦空间"))
    repo.transition(stale.request.id, RequestStatus.FETCHED, now=now - timedelta(minutes=16))
    repo.transition(fresh.request.id, RequestStatus.FETCHED, now=now - timedelta(minutes=14))
    repo.transition(terminal.request.id, RequestStatus.FAILED, now=now - timedelta(minutes=30))

    assert repo.recover_interrupted(now=now) == 1
    assert repo.get(stale.request.id).status is RequestStatus.NEW
    assert repo.get(stale.request.id).attempt_count == 1
    assert repo.get(fresh.request.id).status is RequestStatus.FETCHED
    assert repo.get(terminal.request.id).status is RequestStatus.FAILED


def test_mark_reply_is_idempotent_and_requires_an_existing_request(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    first = repo.mark_reply(saved.request.id, "reply-1")
    duplicate = repo.mark_reply(saved.request.id, "reply-1")

    assert first.reply_status is ReplyStatus.SENT
    assert first.reply_id == "reply-1"
    assert duplicate == first
    with pytest.raises(InvalidTransition):
        repo.mark_reply(saved.request.id, "reply-2")
    with pytest.raises(RequestNotFound):
        repo.mark_reply(999, "reply-1")


def test_runtime_state_is_separate_and_contains_only_browser_health(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")

    assert repo.get_runtime_state().browser_state is BrowserState.READY
    paused_at = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    paused = repo.set_runtime_state(
        BrowserState.PAUSED,
        pause_code="login_required",
        paused_at=paused_at,
        pause_notified=True,
    )

    assert paused.browser_state is BrowserState.PAUSED
    assert paused.paused_at == paused_at
    assert repo.get_runtime_state() == paused

    with sqlite3.connect(tmp_path / "app.db") as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runtime_state)")}
    assert columns == {"id", "browser_state", "pause_code", "paused_at", "pause_notified"}


def test_initialization_migrates_legacy_schema_and_configures_private_wal_database(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE xhs_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT NOT NULL,
                note_url TEXT NOT NULL,
                mention_id TEXT NOT NULL,
                sender_user_id TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )

    repo = RequestRepository(database_path)
    saved = repo.save_mention(make_mention())

    assert saved.created is True
    assert os.stat(database_path).st_mode & 0o777 == 0o600
    with sqlite3.connect(database_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(xhs_requests)")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(xhs_requests)")}
    assert journal_mode == "wal"
    assert {"ux_xhs_requests_mention_id", "ux_xhs_requests_request_key"} <= indexes
    assert {"request_key", "comment_id", "reply_status", "attempt_count", "updated_at"} <= columns


def test_migration_keeps_existing_legacy_rows_readable(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE xhs_requests (
                id INTEGER PRIMARY KEY,
                note_id TEXT NOT NULL,
                note_url TEXT NOT NULL,
                mention_id TEXT NOT NULL,
                sender_user_id TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO xhs_requests VALUES
            (1, 'note-1', 'https://example.test/note-1', 'mention-1', 'user-1',
             '想看星际穿越', '2026-09-07T12:00:00+00:00', 'NEW')
            """
        )

    migrated = RequestRepository(database_path).get(1)

    assert migrated is not None
    assert migrated.status is RequestStatus.NEW
    assert migrated.updated_at == datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    assert migrated.reply_status is ReplyStatus.PENDING
    assert migrated.attempt_count == 0


def test_get_and_recent_validate_requests_and_limit(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    first = repo.save_mention(make_mention("m1", "想看星际穿越"))
    repo.save_mention(make_mention("m2", "想看沙丘"))

    assert repo.get(first.request.id) == first.request
    assert repo.get(999) is None
    assert len(repo.recent(1)) == 1
    with pytest.raises(ValueError):
        repo.recent(0)

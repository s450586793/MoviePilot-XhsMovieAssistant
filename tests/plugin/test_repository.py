import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

import xhsmovieassistant.repository as repository_module
from xhsmovieassistant.models import (
    BrowserState,
    MediaMatch,
    NoteContext,
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


def make_note_context() -> NoteContext:
    return NoteContext(
        id="note-1",
        url="https://www.xiaohongshu.com/explore/note-1",
        type="video",
        title="星际穿越",
        content="2014 年电影",
        author="作者",
        relevant_comments=["这部电影叫《星际穿越》"],
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


def test_save_mention_rejects_split_unique_key_collision(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    first = make_mention("mention-a", "想看星际穿越")
    second = NewMention(
        note_id="note-2",
        note_url="https://www.xiaohongshu.com/explore/note-2",
        mention_id="mention-b",
        sender_user_id=first.sender_user_id,
        comment_id="comment-b",
        comment_text="想看沙丘",
        created_at=first.created_at,
    )
    repo.save_mention(first)
    repo.save_mention(second)
    split_collision = NewMention(
        note_id=second.note_id,
        note_url=second.note_url,
        mention_id=first.mention_id,
        sender_user_id=second.sender_user_id,
        comment_id="comment-collision",
        comment_text=second.comment_text,
        created_at=first.created_at,
    )

    with pytest.raises(RuntimeError, match="different requests"):
        repo.save_mention(split_collision)

    assert {item.mention_id for item in repo.recent(10)} == {
        "mention-a",
        "mention-b",
    }


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


def test_fetched_transition_atomically_round_trips_typed_note_snapshot(tmp_path) -> None:
    database_path = tmp_path / "app.db"
    repo = RequestRepository(database_path)
    saved = repo.save_mention(make_mention())
    note = make_note_context()

    fetched = repo.transition(saved.request.id, RequestStatus.FETCHED, note=note)

    assert fetched.note == note
    assert repo.get(saved.request.id).note == note  # type: ignore[union-attr]
    with sqlite3.connect(database_path) as connection:
        raw_snapshot = connection.execute(
            "SELECT note_snapshot FROM xhs_requests WHERE id = ?",
            (saved.request.id,),
        ).fetchone()[0]
    assert json.loads(raw_snapshot) == {
        "id": "note-1",
        "url": "https://www.xiaohongshu.com/explore/note-1",
        "type": "video",
        "title": "星际穿越",
        "content": "2014 年电影",
        "author": "作者",
        "relevant_comments": ["这部电影叫《星际穿越》"],
    }


def test_failed_fetched_cas_does_not_write_note_snapshot(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    with pytest.raises(InvalidTransition):
        repo.transition(
            saved.request.id,
            RequestStatus.RESOLVING,
            note=make_note_context(),
        )

    unchanged = repo.get(saved.request.id)
    assert unchanged is not None
    assert unchanged.status is RequestStatus.NEW
    assert unchanged.note is None


def test_note_snapshot_rejects_untyped_data_before_transition(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    with pytest.raises(TypeError, match="NoteContext"):
        repo.transition(
            saved.request.id,
            RequestStatus.FETCHED,
            note={"id": "note-1", "xsec_token": "secret"},  # type: ignore[arg-type]
        )

    unchanged = repo.get(saved.request.id)
    assert unchanged is not None
    assert unchanged.status is RequestStatus.NEW
    assert unchanged.note is None


def test_note_snapshot_strips_navigation_credentials_from_url(tmp_path) -> None:
    database_path = tmp_path / "app.db"
    repo = RequestRepository(database_path)
    saved = repo.save_mention(make_mention())
    note = NoteContext(
        id="note-1",
        url=(
            "https://www.xiaohongshu.com/explore/note-1"
            "?xsec_token=credential#private"
        ),
        title="星际穿越",
    )

    fetched = repo.transition(saved.request.id, RequestStatus.FETCHED, note=note)

    assert fetched.note is not None
    assert fetched.note.url == "https://www.xiaohongshu.com/explore/note-1"
    assert b"credential" not in database_path.read_bytes()


def test_requeue_requires_authentication_and_only_permitted_terminal_states(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())
    repo.mark_reply(saved.request.id, "reply-old")
    failed = repo.transition(saved.request.id, RequestStatus.FAILED, error="temporary")

    with pytest.raises(PermissionError):
        repo.requeue(saved.request.id, authenticated=False)

    requeued = repo.requeue(saved.request.id, authenticated=True)
    assert requeued.status is RequestStatus.NEW
    assert requeued.error is None
    assert requeued.attempt_count == failed.attempt_count + 1
    assert requeued.reply_status is ReplyStatus.PENDING
    assert requeued.reply_id is None
    assert requeued.replied_at is None

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


def test_notification_outbox_is_durable_deduplicated_and_metadata_only(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    repo = RequestRepository(database_path)
    saved = repo.save_mention(make_mention())
    created_at = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    enqueue = getattr(repo, "enqueue_notification", None)

    assert callable(enqueue)

    first = enqueue(
        "REQUEST_RESULT",
        request_id=saved.request.id,
        code="SUBSCRIBED",
        dedupe_key="request:1:secret-dedupe-material",
        now=created_at,
    )
    duplicate = enqueue(
        "REQUEST_RESULT",
        request_id=saved.request.id,
        code="SUBSCRIBED",
        dedupe_key="request:1:secret-dedupe-material",
        now=created_at + timedelta(minutes=1),
    )

    assert first.kind == "REQUEST_RESULT"
    assert duplicate == first
    assert first.attempt_count == 0
    assert first.delivered_at is None
    assert RequestRepository(database_path).pending_notifications(20) == [first]
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(notification_outbox)")
        }
    assert columns == {
        "id",
        "kind",
        "request_id",
        "code",
        "dedupe_key",
        "created_at",
        "last_attempt_at",
        "delivered_at",
        "attempt_count",
    }
    assert b"secret-dedupe-material" not in database_path.read_bytes()


def test_notification_outbox_retries_then_marks_delivery_once(tmp_path) -> None:
    database_path = tmp_path / "app.db"
    repo = RequestRepository(database_path)
    enqueue = getattr(repo, "enqueue_notification", None)

    assert callable(enqueue)
    event = enqueue(
        "PAUSE",
        request_id=None,
        code="AUTH_REQUIRED",
        dedupe_key="pause:2026-09-07T12:00:00Z",
        now=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
    )

    failed = repo.record_notification_attempt(
        event.id,
        delivered=False,
        now=datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc),
    )
    pending = RequestRepository(database_path).pending_notifications(20)
    delivered = repo.record_notification_attempt(
        event.id,
        delivered=True,
        now=datetime(2026, 9, 7, 12, 2, tzinfo=timezone.utc),
    )
    duplicate = repo.record_notification_attempt(
        event.id,
        delivered=True,
        now=datetime(2026, 9, 7, 12, 3, tzinfo=timezone.utc),
    )

    assert failed.attempt_count == 1
    assert failed.delivered_at is None
    assert pending == [failed]
    assert delivered.attempt_count == 2
    assert delivered.delivered_at == datetime(2026, 9, 7, 12, 2, tzinfo=timezone.utc)
    assert duplicate == delivered
    assert repo.pending_notifications(20) == []


def test_terminal_transition_rolls_back_when_atomic_result_outbox_insert_fails(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    repo = RequestRepository(database_path)
    saved = repo.save_mention(make_mention())
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_result_outbox_insert
            BEFORE INSERT ON notification_outbox
            WHEN NEW.kind = 'REQUEST_RESULT'
            BEGIN
                SELECT RAISE(ABORT, 'result outbox unavailable');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="result outbox unavailable"):
        repo.transition(
            saved.request.id,
            RequestStatus.FAILED,
            error="UPSTREAM_ERROR",
            business_notifications_enabled=True,
        )

    assert repo.get(saved.request.id).status is RequestStatus.NEW  # type: ignore[union-attr]
    assert repo.pending_notifications() == []


def test_reply_failure_rolls_back_when_atomic_outbox_insert_fails(tmp_path) -> None:
    database_path = tmp_path / "app.db"
    repo = RequestRepository(database_path)
    saved = repo.save_mention(make_mention())
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_reply_outbox_insert
            BEFORE INSERT ON notification_outbox
            WHEN NEW.kind = 'REPLY_FAILURE'
            BEGIN
                SELECT RAISE(ABORT, 'reply outbox unavailable');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="reply outbox unavailable"):
        repo.mark_reply(
            saved.request.id,
            status=ReplyStatus.FAILED,
            business_notifications_enabled=True,
        )

    assert repo.get(saved.request.id).reply_status is ReplyStatus.PENDING  # type: ignore[union-attr]
    assert repo.pending_notifications() == []


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
    assert {
        "request_key",
        "comment_id",
        "note_snapshot",
        "reply_status",
        "attempt_count",
        "updated_at",
    } <= columns


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
    assert migrated.note is None


def test_get_and_recent_validate_requests_and_limit(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    first = repo.save_mention(make_mention("m1", "想看星际穿越"))
    repo.save_mention(make_mention("m2", "想看沙丘"))

    assert repo.get(first.request.id) == first.request
    assert repo.get(999) is None
    assert len(repo.recent(1)) == 1
    with pytest.raises(ValueError):
        repo.recent(0)


def test_transition_uses_compare_and_swap_when_two_workers_read_new_state(
    tmp_path, monkeypatch
) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())
    barrier = _barrier_after_two_request_reads(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(repo.transition, saved.request.id, RequestStatus.FAILED),
            workers.submit(repo.transition, saved.request.id, RequestStatus.FETCHED),
        ]
    outcomes = [future.exception() or future.result() for future in futures]

    assert barrier["calls"] >= 2
    assert sum(isinstance(outcome, InvalidTransition) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, type(saved.request)) for outcome in outcomes) == 1
    assert repo.get(saved.request.id).status in {RequestStatus.FAILED, RequestStatus.FETCHED}


def test_mark_reply_compares_pending_state_before_recording_reply(tmp_path, monkeypatch) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())
    _barrier_after_two_request_reads(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(repo.mark_reply, saved.request.id, "reply-1"),
            workers.submit(repo.mark_reply, saved.request.id, "reply-2"),
        ]
    outcomes = [future.exception() or future.result() for future in futures]

    assert sum(isinstance(outcome, InvalidTransition) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, type(saved.request)) for outcome in outcomes) == 1
    assert repo.get(saved.request.id).reply_id in {"reply-1", "reply-2"}


def test_mark_reply_persists_failure_and_keeps_sent_reply_terminal(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    failed = repo.save_mention(make_mention("failed"))
    sent = repo.save_mention(make_mention("sent", "想看沙丘"))

    failed_reply = repo.mark_reply(
        failed.request.id, status=ReplyStatus.FAILED
    )
    sent_reply = repo.mark_reply(sent.request.id, "reply-1")

    assert failed_reply.reply_status is ReplyStatus.FAILED
    assert failed_reply.reply_id is None
    assert repo.mark_reply(sent.request.id, "reply-1") == sent_reply
    with pytest.raises(InvalidTransition):
        repo.mark_reply(sent.request.id, status=ReplyStatus.FAILED)


def test_mark_reply_rejects_pending_before_writing_reply_metadata(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    with pytest.raises(ValueError):
        repo.mark_reply(saved.request.id, status=ReplyStatus.PENDING)

    unchanged = repo.get(saved.request.id)
    assert unchanged is not None
    assert unchanged.reply_status is ReplyStatus.PENDING
    assert unchanged.reply_id is None
    assert unchanged.replied_at is None


def test_migration_rejects_phase1_rekey_collision_without_losing_audit_state(tmp_path) -> None:
    database_path = tmp_path / "phase1.db"
    first_text = "想看 星际穿越"
    duplicate_text = "想看　星际穿越"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE xhs_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT NOT NULL,
                note_url TEXT NOT NULL,
                mention_id TEXT NOT NULL,
                sender_user_id TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                request_key TEXT NOT NULL,
                note_title TEXT,
                note_content TEXT,
                note_author_id TEXT,
                note_author_name TEXT,
                detected_title TEXT,
                media_type TEXT,
                year INTEGER,
                season INTEGER,
                mp_id TEXT,
                confidence REAL,
                error TEXT,
                processed_at TEXT,
                reply_status TEXT NOT NULL DEFAULT 'PENDING',
                reply_id TEXT,
                replied_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX ux_xhs_requests_mention_id ON xhs_requests (mention_id)"
        )
        for mention_id, comment_text, status, reply_status, reply_id in (
            ("m1", first_text, "NEW", "PENDING", None),
            ("m2", duplicate_text, "SUBSCRIBED", "SENT", "reply-2"),
        ):
            old_key = _phase1_request_key("note-1", "user-1", comment_text)
            if mention_id == "m2":
                old_key = f"{old_key}{mention_id}"
            connection.execute(
                """
                INSERT INTO xhs_requests (
                    note_id, note_url, mention_id, sender_user_id, comment_id, comment_text,
                    created_at, status, request_key, reply_status, reply_id, replied_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "note-1", "https://example.test/note-1?xsec_token=secret", mention_id, "user-1",
                    f"comment-{mention_id}", comment_text, "2026-09-07T12:00:00+00:00",
                    status, old_key, reply_status, reply_id, "2026-09-07T12:01:00+00:00",
                    "2026-09-07T12:01:00+00:00",
                ),
            )
        connection.execute(
            "CREATE UNIQUE INDEX ux_xhs_requests_request_key ON xhs_requests (request_key)"
        )
        schema_before = _request_table_schema_snapshot(connection)
        tables_before = tuple(
            connection.execute(
                "SELECT name, sql FROM sqlite_schema WHERE type = 'table' ORDER BY name"
            )
        )

    with pytest.raises(RuntimeError, match="request key collision"):
        RequestRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT mention_id, status, reply_status, reply_id, request_key
            FROM xhs_requests ORDER BY id
            """
        ).fetchall()
        schema_after = _request_table_schema_snapshot(connection)
        tables_after = tuple(
            connection.execute(
                "SELECT name, sql FROM sqlite_schema WHERE type = 'table' ORDER BY name"
            )
        )
    assert rows == [
        ("m1", "NEW", "PENDING", None, _phase1_request_key("note-1", "user-1", first_text)),
        (
            "m2",
            "SUBSCRIBED",
            "SENT",
            "reply-2",
            f"{_phase1_request_key('note-1', 'user-1', duplicate_text)}m2",
        ),
    ]
    assert schema_after == schema_before
    assert tables_after == tables_before


def test_repository_strips_or_redacts_sensitive_persistence_inputs(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    mention = NewMention(
        note_id="note-1",
        note_url="https://www.xiaohongshu.com/explore/note-1?xsec_token=secret#token",
        mention_id="m1",
        sender_user_id="user-1",
        comment_id="comment-1",
        comment_text="想看星际穿越",
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )

    saved = repo.save_mention(mention)
    failed = repo.transition(saved.request.id, RequestStatus.FAILED, error="token=secret")
    paused = repo.set_runtime_state(
        BrowserState.PAUSED, pause_code="cookie=secret", pause_notified=True
    )

    assert saved.request.note_url == "https://www.xiaohongshu.com/explore/note-1"
    assert failed.error == "REDACTED"
    assert paused.pause_code == "REDACTED"
    with sqlite3.connect(tmp_path / "app.db") as connection:
        persisted = " ".join(
            str(value)
            for row in connection.execute("SELECT note_url, error FROM xhs_requests")
            for value in row
        )
    assert "secret" not in persisted


@pytest.mark.parametrize(
    "unsafe_value",
    ["sk-live-abc123", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature", "request failed"],
)
def test_repository_redacts_non_code_error_and_pause_values(tmp_path, unsafe_value: str) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    failed = repo.transition(saved.request.id, RequestStatus.FAILED, error=unsafe_value)
    paused = repo.set_runtime_state(BrowserState.PAUSED, pause_code=unsafe_value)

    assert failed.error == "REDACTED"
    assert paused.pause_code == "REDACTED"


def test_repository_persists_only_known_stable_error_codes(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    failed = repo.transition(saved.request.id, RequestStatus.FAILED, error="AUTH_REQUIRED")
    paused = repo.set_runtime_state(BrowserState.PAUSED, pause_code="AUTH_REQUIRED")

    assert failed.error == "AUTH_REQUIRED"
    assert paused.pause_code == "AUTH_REQUIRED"


def test_repository_persists_planned_risk_control_code_and_redacts_unknown_code(tmp_path) -> None:
    repo = RequestRepository(tmp_path / "app.db")
    saved = repo.save_mention(make_mention())

    failed = repo.transition(saved.request.id, RequestStatus.FAILED, error="XHS_RISK_CONTROL")
    paused = repo.set_runtime_state(BrowserState.PAUSED, pause_code="UNKNOWN_SAFE_SHAPE")

    assert failed.error == "XHS_RISK_CONTROL"
    assert paused.pause_code == "REDACTED"


def _barrier_after_two_request_reads(monkeypatch) -> dict[str, int]:
    original = repository_module._require_row
    state = {"calls": 0}
    barrier = Barrier(2)

    def synchronized(connection, request_id):
        row = original(connection, request_id)
        if state["calls"] < 2:
            state["calls"] += 1
            barrier.wait()
        return row

    monkeypatch.setattr(repository_module, "_require_row", synchronized)
    return state


def _phase1_request_key(note_id: str, sender_user_id: str, comment_text: str) -> str:
    import hashlib
    import json
    import unicodedata

    normalized = " ".join(unicodedata.normalize("NFKC", comment_text).split()).casefold()
    payload = json.dumps(
        [note_id, sender_user_id, normalized], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _request_table_schema_snapshot(connection: sqlite3.Connection) -> tuple:
    columns = tuple(connection.execute("PRAGMA table_info(xhs_requests)"))
    indexes = tuple(connection.execute("PRAGMA index_list(xhs_requests)"))
    index_details = tuple(
        (
            index,
            connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'index' AND name = ?",
                (index[1],),
            ).fetchone(),
            tuple(
                connection.execute(
                    "SELECT seqno, cid, name, desc, coll, key "
                    "FROM pragma_index_xinfo(?) ORDER BY seqno",
                    (index[1],),
                )
            ),
        )
        for index in indexes
    )
    return columns, index_details

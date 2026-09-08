"""SQLite persistence and state transitions for assistant requests."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import (
    BrowserState,
    MediaMatch,
    NoteContext,
    ReplyStatus,
    RequestStatus,
    Resolution,
)


class InvalidTransition(ValueError):
    """Raised when a request is asked to take an unsupported state transition."""


class RequestNotFound(LookupError):
    """Raised when a mutation references no persisted request."""


class IdempotencyConflict(RuntimeError):
    """Raised when independent unique keys identify different requests."""


class NotificationIdempotencyConflict(RuntimeError):
    """Raised when one outbox dedupe key identifies different metadata."""


ALLOWED_TRANSITIONS: dict[RequestStatus, set[RequestStatus]] = {
    RequestStatus.NEW: {RequestStatus.FETCHED, RequestStatus.IGNORED, RequestStatus.FAILED},
    RequestStatus.FETCHED: {RequestStatus.RESOLVING, RequestStatus.FAILED},
    RequestStatus.RESOLVING: {
        RequestStatus.NEED_CONFIRMATION,
        RequestStatus.NOT_MEDIA,
        RequestStatus.MATCHED,
        RequestStatus.FAILED,
    },
    RequestStatus.MATCHED: {
        RequestStatus.DRY_RUN_MATCHED,
        RequestStatus.ALREADY_IN_LIBRARY,
        RequestStatus.ALREADY_SUBSCRIBED,
        RequestStatus.SUBSCRIBED,
        RequestStatus.FAILED,
    },
}

REQUEUEABLE_STATUSES = {
    RequestStatus.FAILED,
    RequestStatus.NEED_CONFIRMATION,
    RequestStatus.DRY_RUN_MATCHED,
}
SAFE_PERSISTED_CODES = frozenset(
    {
        "AUTH_REQUIRED",
        "LOGIN_REQUIRED",
        "SESSION_EXPIRED",
        "BROWSER_UNAVAILABLE",
        "RATE_LIMITED",
        "NETWORK_ERROR",
        "UPSTREAM_ERROR",
        "TEMPORARY_FAILURE",
        "XHS_RISK_CONTROL",
    }
)
RECOVERABLE_STATUSES = {
    RequestStatus.FETCHED,
    RequestStatus.RESOLVING,
    RequestStatus.MATCHED,
}
NOTIFICATION_KINDS = frozenset({"REQUEST_RESULT", "REPLY_FAILURE", "PAUSE"})
SAFE_NOTIFICATION_CODES = SAFE_PERSISTED_CODES | frozenset(
    status.value for status in RequestStatus
) | {"REPLY_FAILED"}
TERMINAL_REQUEST_STATUSES = frozenset(
    {
        RequestStatus.IGNORED,
        RequestStatus.NEED_CONFIRMATION,
        RequestStatus.NOT_MEDIA,
        RequestStatus.DRY_RUN_MATCHED,
        RequestStatus.ALREADY_IN_LIBRARY,
        RequestStatus.ALREADY_SUBSCRIBED,
        RequestStatus.SUBSCRIBED,
        RequestStatus.FAILED,
    }
)


@dataclass(frozen=True)
class NewMention:
    """Sanitized source data captured for one authorized mention."""

    note_id: str
    note_url: str
    mention_id: str
    sender_user_id: str
    comment_id: str
    comment_text: str
    created_at: datetime


@dataclass(frozen=True)
class StoredRequest:
    """The durable representation of one assistant request."""

    id: int
    note_id: str
    note_url: str
    mention_id: str
    sender_user_id: str
    comment_id: str
    comment_text: str
    request_key: str
    created_at: datetime
    updated_at: datetime
    status: RequestStatus
    note: NoteContext | None
    title: str | None
    original_title: str | None
    media_type: str | None
    year: int | None
    season: int | None
    confidence: float | None
    resolution_reason: str | None
    media_source: str | None
    media_source_id: str | None
    tmdb_id: int | None
    score: float | None
    subscription_id: str | None
    error: str | None
    reply_status: ReplyStatus
    reply_id: str | None
    replied_at: datetime | None
    attempt_count: int


@dataclass(frozen=True)
class SaveOutcome:
    """Result of an idempotent mention save."""

    request: StoredRequest
    created: bool


@dataclass(frozen=True)
class RuntimeState:
    """Persisted browser health only; never authentication material."""

    browser_state: BrowserState
    pause_code: str | None
    paused_at: datetime | None
    pause_notified: bool


@dataclass(frozen=True)
class OutboxNotification:
    """One metadata-only notification awaiting confirmed delivery."""

    id: int
    kind: str
    request_id: int | None
    code: str
    dedupe_key: str
    created_at: datetime
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    attempt_count: int


class RequestRepository:
    """Single SQLite persistence boundary for assistant request processing."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_database_file()
        self._initialize()

    def save_mention(self, mention: NewMention) -> SaveOutcome:
        """Persist a mention once, deduplicating by source and semantic request keys."""
        _validate_mention(mention)
        request_key = _request_key(mention)
        created_at = _format_datetime(mention.created_at)
        updated_at = _format_datetime(_utc_now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO xhs_requests (
                    note_id, note_url, mention_id, sender_user_id, comment_id,
                    comment_text, request_key, created_at, updated_at, status,
                    reply_status, attempt_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mention.note_id,
                    _safe_note_url(mention.note_url),
                    mention.mention_id,
                    mention.sender_user_id,
                    mention.comment_id,
                    mention.comment_text,
                    request_key,
                    created_at,
                    updated_at,
                    RequestStatus.NEW.value,
                    ReplyStatus.PENDING.value,
                    0,
                ),
            )
            if cursor.rowcount:
                row = connection.execute(
                    "SELECT * FROM xhs_requests WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                created = True
            else:
                mention_row = connection.execute(
                    "SELECT * FROM xhs_requests WHERE mention_id = ?",
                    (mention.mention_id,),
                ).fetchone()
                request_row = connection.execute(
                    "SELECT * FROM xhs_requests WHERE request_key = ?",
                    (request_key,),
                ).fetchone()
                if mention_row is not None and mention_row["request_key"] != request_key:
                    raise IdempotencyConflict(
                        "mention and request keys identify different requests"
                    )
                if (
                    mention_row is not None
                    and request_row is not None
                    and mention_row["id"] != request_row["id"]
                ):
                    raise IdempotencyConflict(
                        "mention and request keys identify different requests"
                    )
                row = mention_row or request_row
                created = False
        if row is None:
            raise RuntimeError("Persisted request could not be read back")
        return SaveOutcome(request=_request_from_row(row), created=created)

    def get(self, request_id: int) -> StoredRequest | None:
        """Return one request by its database identifier."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM xhs_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return _request_from_row(row) if row is not None else None

    def recent(self, limit: int) -> list[StoredRequest]:
        """Return the newest persisted requests, bounded by a positive limit."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM xhs_requests ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_request_from_row(row) for row in rows]

    def transition(
        self,
        request_id: int,
        target: RequestStatus,
        *,
        error: str | None = None,
        note: NoteContext | None = None,
        resolution: Resolution | None = None,
        match: MediaMatch | None = None,
        subscription_id: str | None = None,
        business_notifications_enabled: bool = False,
        now: datetime | None = None,
    ) -> StoredRequest:
        """Advance one request through the explicit forward-only state machine."""
        if not isinstance(target, RequestStatus):
            raise TypeError("target must be a RequestStatus")
        if note is not None and not isinstance(note, NoteContext):
            raise TypeError("note must be a NoteContext")
        if resolution is not None and not isinstance(resolution, Resolution):
            raise TypeError("resolution must be a Resolution")
        if match is not None and not isinstance(match, MediaMatch):
            raise TypeError("match must be a MediaMatch")
        if not isinstance(business_notifications_enabled, bool):
            raise TypeError("business_notifications_enabled must be a bool")
        event_time = now or _utc_now()
        timestamp = _format_datetime(event_time)
        with self._connect() as connection:
            row = _require_row(connection, request_id)
            current = RequestStatus(row["status"])
            if target not in ALLOWED_TRANSITIONS.get(current, set()):
                raise InvalidTransition(f"Cannot transition {current.value} to {target.value}")
            if note is not None and target is not RequestStatus.FETCHED:
                raise ValueError("note snapshot can only be saved with FETCHED")
            values: dict[str, Any] = {"status": target.value, "updated_at": timestamp}
            if error is not None:
                values["error"] = _safe_persisted_code(error)
            if note is not None:
                values["note_snapshot"] = _serialize_note(note)
            if subscription_id is not None:
                values["subscription_id"] = subscription_id
            if resolution is not None:
                values.update(_resolution_values(resolution))
            if match is not None:
                values.update(_match_values(match))
            if not _compare_and_update(connection, request_id, current, values):
                raced = _require_row(connection, request_id)
                raise InvalidTransition(
                    f"Cannot transition {RequestStatus(raced['status']).value} to {target.value}"
                )
            updated = _require_row(connection, request_id)
            if business_notifications_enabled and target in TERMINAL_REQUEST_STATUSES:
                _enqueue_notification(
                    connection,
                    "REQUEST_RESULT",
                    request_id=request_id,
                    code=target.value,
                    dedupe_key=f"REQUEST_RESULT:{request_id}:{updated['attempt_count']}",
                    now=event_time,
                )
        return _request_from_row(updated)

    def requeue(
        self, request_id: int, *, authenticated: bool, now: datetime | None = None
    ) -> StoredRequest:
        """Return an authenticated manual retry from the limited terminal states."""
        if not authenticated:
            raise PermissionError("Authenticated manual retry is required")
        with self._connect() as connection:
            row = _require_row(connection, request_id)
            current = RequestStatus(row["status"])
            if current not in REQUEUEABLE_STATUSES:
                raise InvalidTransition(f"Cannot requeue {current.value}")
            if not _compare_and_update(
                connection,
                request_id,
                current,
                {
                    "status": RequestStatus.NEW.value,
                    "error": None,
                    "reply_status": ReplyStatus.PENDING.value,
                    "reply_id": None,
                    "replied_at": None,
                    "attempt_count": row["attempt_count"] + 1,
                    "updated_at": _format_datetime(now or _utc_now()),
                },
            ):
                raced = _require_row(connection, request_id)
                raise InvalidTransition(
                    f"Cannot requeue {RequestStatus(raced['status']).value}"
                )
            updated = _require_row(connection, request_id)
        return _request_from_row(updated)

    def retry_interrupted(
        self, request_id: int, *, now: datetime | None = None
    ) -> StoredRequest:
        """Return generation-cancelled in-progress work to its retryable state."""
        with self._connect() as connection:
            row = _require_row(connection, request_id)
            current = RequestStatus(row["status"])
            if current is RequestStatus.NEW:
                return _request_from_row(row)
            if current not in RECOVERABLE_STATUSES:
                return _request_from_row(row)
            if not _compare_and_update(
                connection,
                request_id,
                current,
                {
                    "status": RequestStatus.NEW.value,
                    "error": None,
                    "attempt_count": row["attempt_count"] + 1,
                    "updated_at": _format_datetime(now or _utc_now()),
                },
            ):
                return _request_from_row(_require_row(connection, request_id))
            return _request_from_row(_require_row(connection, request_id))

    def mark_reply(
        self,
        request_id: int,
        reply_id: str | None = None,
        *,
        status: ReplyStatus = ReplyStatus.SENT,
        business_notifications_enabled: bool = False,
        now: datetime | None = None,
    ) -> StoredRequest:
        """Atomically record one reply delivery outcome from the pending state."""
        if not isinstance(status, ReplyStatus):
            raise TypeError("status must be a ReplyStatus")
        if status not in {ReplyStatus.SENT, ReplyStatus.FAILED}:
            raise ValueError("status must be ReplyStatus.SENT or ReplyStatus.FAILED")
        if status is ReplyStatus.SENT and (
            not isinstance(reply_id, str) or not reply_id.strip()
        ):
            raise ValueError("reply_id must be a non-empty string when reply is sent")
        if status is ReplyStatus.FAILED and reply_id is not None:
            raise ValueError("reply_id must be omitted when reply failed")
        if not isinstance(business_notifications_enabled, bool):
            raise TypeError("business_notifications_enabled must be a bool")
        with self._connect() as connection:
            row = _require_row(connection, request_id)
            current = ReplyStatus(row["reply_status"])
            if current is ReplyStatus.SENT:
                if status is ReplyStatus.SENT and row["reply_id"] == reply_id:
                    return _request_from_row(row)
                raise InvalidTransition("Reply delivery is already final")
            if current is ReplyStatus.FAILED:
                raise InvalidTransition("Reply delivery is already final")
            event_time = now or _utc_now()
            timestamp = _format_datetime(event_time)
            values = {
                "reply_status": status.value,
                "reply_id": reply_id,
                "replied_at": timestamp,
                "updated_at": timestamp,
            }
            if not _compare_and_update_reply(connection, request_id, values):
                raced = _require_row(connection, request_id)
                if (
                    status is ReplyStatus.SENT
                    and ReplyStatus(raced["reply_status"]) is ReplyStatus.SENT
                    and raced["reply_id"] == reply_id
                ):
                    return _request_from_row(raced)
                raise InvalidTransition("Reply delivery was recorded concurrently")
            updated = _require_row(connection, request_id)
            if business_notifications_enabled and status is ReplyStatus.FAILED:
                _enqueue_notification(
                    connection,
                    "REPLY_FAILURE",
                    request_id=request_id,
                    code="REPLY_FAILED",
                    dedupe_key=f"REPLY_FAILURE:{request_id}:{updated['attempt_count']}",
                    now=event_time,
                )
        return _request_from_row(updated)

    def get_runtime_state(self) -> RuntimeState:
        """Return the sole browser-health state row."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runtime_state WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("Runtime state was not initialized")
        return _runtime_state_from_row(row)

    def set_runtime_state(
        self,
        browser_state: BrowserState,
        *,
        pause_code: str | None = None,
        paused_at: datetime | None = None,
        pause_notified: bool = False,
    ) -> RuntimeState:
        """Replace browser health fields without accepting session credentials."""
        if not isinstance(browser_state, BrowserState):
            raise TypeError("browser_state must be a BrowserState")
        if not isinstance(pause_notified, bool):
            raise TypeError("pause_notified must be a bool")
        if paused_at is not None:
            _require_aware_datetime(paused_at, "paused_at")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runtime_state
                SET browser_state = ?, pause_code = ?, paused_at = ?, pause_notified = ?
                WHERE id = 1
                """,
                (
                    browser_state.value,
                    _safe_persisted_code(pause_code),
                    _format_datetime(paused_at) if paused_at else None,
                    int(pause_notified),
                ),
            )
            row = connection.execute("SELECT * FROM runtime_state WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("Runtime state was not initialized")
        return _runtime_state_from_row(row)

    def enqueue_notification(
        self,
        kind: str,
        *,
        request_id: int | None,
        code: str,
        dedupe_key: str,
        now: datetime | None = None,
    ) -> OutboxNotification:
        """Persist one metadata-only notification event exactly once."""
        if kind not in NOTIFICATION_KINDS:
            raise ValueError("unsupported notification kind")
        if request_id is not None and (
            isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1
        ):
            raise ValueError("request_id must be a positive integer or None")
        if kind == "PAUSE" and request_id is not None:
            raise ValueError("pause notifications cannot reference a request")
        if kind != "PAUSE" and request_id is None:
            raise ValueError("request notification requires request_id")
        if not isinstance(dedupe_key, str) or not dedupe_key:
            raise ValueError("dedupe_key must be a non-empty string")
        timestamp = now or _utc_now()
        _require_aware_datetime(timestamp, "now")
        with self._connect() as connection:
            event = _enqueue_notification(
                connection,
                kind,
                request_id=request_id,
                code=code,
                dedupe_key=dedupe_key,
                now=timestamp,
            )
        return event

    def pending_notifications(
        self, limit: int = 20, *, business_enabled: bool = True
    ) -> list[OutboxNotification]:
        """Return undelivered metadata with safety events before business events."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(business_enabled, bool):
            raise TypeError("business_enabled must be a bool")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM notification_outbox
                WHERE delivered_at IS NULL AND (? OR kind = 'PAUSE')
                ORDER BY CASE kind WHEN 'PAUSE' THEN 0 ELSE 1 END, id ASC LIMIT ?
                """,
                (business_enabled, limit),
            ).fetchall()
        return [_notification_from_row(row) for row in rows]

    def record_notification_attempt(
        self,
        notification_id: int,
        *,
        delivered: bool,
        now: datetime | None = None,
    ) -> OutboxNotification:
        """Record one delivery attempt without reopening delivered events."""
        if (
            isinstance(notification_id, bool)
            or not isinstance(notification_id, int)
            or notification_id < 1
        ):
            raise ValueError("notification_id must be a positive integer")
        if not isinstance(delivered, bool):
            raise TypeError("delivered must be a bool")
        timestamp = now or _utc_now()
        _require_aware_datetime(timestamp, "now")
        formatted = _format_datetime(timestamp)
        with self._connect() as connection:
            row = _require_notification_row(connection, notification_id)
            if row["delivered_at"] is not None:
                return _notification_from_row(row)
            connection.execute(
                """
                UPDATE notification_outbox
                SET attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    delivered_at = ?
                WHERE id = ? AND delivered_at IS NULL
                """,
                (formatted, formatted if delivered else None, notification_id),
            )
            updated = _require_notification_row(connection, notification_id)
        return _notification_from_row(updated)

    def recover_interrupted(self, *, now: datetime | None = None) -> int:
        """Return stale in-progress work to NEW after a process interruption."""
        current_time = now or _utc_now()
        _require_aware_datetime(current_time, "now")
        stale_before = _format_datetime(current_time - timedelta(minutes=15))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE xhs_requests
                SET status = ?, attempt_count = attempt_count + 1, updated_at = ?
                WHERE status IN (?, ?, ?) AND updated_at < ?
                """,
                (
                    RequestStatus.NEW.value,
                    _format_datetime(current_time),
                    *(status.value for status in RECOVERABLE_STATUSES),
                    stale_before,
                ),
            )
        return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _ensure_database_file(self) -> None:
        try:
            descriptor = os.open(
                self._database_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._database_path.chmod(0o600)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS xhs_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id TEXT NOT NULL,
                    note_url TEXT NOT NULL,
                    mention_id TEXT NOT NULL,
                    sender_user_id TEXT NOT NULL,
                    comment_id TEXT,
                    comment_text TEXT NOT NULL,
                    request_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    status TEXT NOT NULL,
                    note_snapshot TEXT,
                    title TEXT,
                    original_title TEXT,
                    media_type TEXT,
                    year INTEGER,
                    season INTEGER,
                    confidence REAL,
                    resolution_reason TEXT,
                    media_source TEXT,
                    media_source_id TEXT,
                    tmdb_id INTEGER,
                    score REAL,
                    subscription_id TEXT,
                    error TEXT,
                    reply_status TEXT NOT NULL DEFAULT 'PENDING',
                    reply_id TEXT,
                    replied_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            existing_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(xhs_requests)")
            }
            is_phase1_schema = "detected_title" in existing_columns
            if is_phase1_schema:
                connection.execute("DROP INDEX IF EXISTS ux_xhs_requests_request_key")
            _migrate_request_columns(connection)
            if is_phase1_schema:
                _rekey_phase1_requests(connection)
            _sanitize_stored_note_urls(connection)
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_xhs_requests_mention_id "
                "ON xhs_requests (mention_id)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_xhs_requests_request_key "
                "ON xhs_requests (request_key)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    browser_state TEXT NOT NULL,
                    pause_code TEXT,
                    paused_at TEXT,
                    pause_notified INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO runtime_state (id, browser_state, pause_notified)
                VALUES (1, ?, 0)
                """,
                (BrowserState.READY.value,),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    request_id INTEGER,
                    code TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    delivered_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_outbox_dedupe_key
                ON notification_outbox (dedupe_key)
                """
            )


_MIGRATION_COLUMNS = {
    "comment_id": "TEXT",
    "request_key": "TEXT",
    "updated_at": "TEXT",
    "note_snapshot": "TEXT",
    "title": "TEXT",
    "original_title": "TEXT",
    "media_type": "TEXT",
    "year": "INTEGER",
    "season": "INTEGER",
    "confidence": "REAL",
    "resolution_reason": "TEXT",
    "media_source": "TEXT",
    "media_source_id": "TEXT",
    "tmdb_id": "INTEGER",
    "score": "REAL",
    "subscription_id": "TEXT",
    "error": "TEXT",
    "reply_status": "TEXT NOT NULL DEFAULT 'PENDING'",
    "reply_id": "TEXT",
    "replied_at": "TEXT",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
}


def _migrate_request_columns(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute("PRAGMA table_info(xhs_requests)")}
    for name, definition in _MIGRATION_COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE xhs_requests ADD COLUMN {name} {definition}")
    connection.execute(
        "UPDATE xhs_requests SET updated_at = created_at WHERE updated_at IS NULL"
    )
    connection.execute(
        "UPDATE xhs_requests SET reply_status = ? WHERE reply_status IS NULL",
        (ReplyStatus.PENDING.value,),
    )
    connection.execute(
        "UPDATE xhs_requests SET attempt_count = 0 WHERE attempt_count IS NULL"
    )


def _rekey_phase1_requests(connection: sqlite3.Connection) -> None:
    """Replace Phase 1's JSON serialization hash before restoring its unique index."""
    rows = connection.execute(
        """
        SELECT id, note_id, sender_user_id, comment_text
        FROM xhs_requests ORDER BY id ASC
        """
    ).fetchall()
    seen_keys: set[str] = set()
    surviving: list[tuple[str, int]] = []
    for row in rows:
        key = _request_key_values(
            row["note_id"], row["sender_user_id"], row["comment_text"]
        )
        if key in seen_keys:
            raise RuntimeError("phase 1 request key collision detected")
        else:
            seen_keys.add(key)
            surviving.append((key, row["id"]))
    connection.executemany(
        "UPDATE xhs_requests SET request_key = ? WHERE id = ?", surviving
    )


def _sanitize_stored_note_urls(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT id, note_url FROM xhs_requests").fetchall()
    sanitized: list[tuple[str, int]] = []
    for row in rows:
        note_url = _safe_note_url(row["note_url"])
        if note_url != row["note_url"]:
            sanitized.append((note_url, row["id"]))
    if sanitized:
        connection.executemany(
            "UPDATE xhs_requests SET note_url = ? WHERE id = ?", sanitized
        )


def _compare_and_update(
    connection: sqlite3.Connection,
    request_id: int,
    expected_status: RequestStatus,
    values: dict[str, Any],
) -> bool:
    assignments = ", ".join(f"{column} = ?" for column in values)
    cursor = connection.execute(
        f"UPDATE xhs_requests SET {assignments} WHERE id = ? AND status = ?",
        (*values.values(), request_id, expected_status.value),
    )
    return cursor.rowcount == 1


def _compare_and_update_reply(
    connection: sqlite3.Connection, request_id: int, values: dict[str, Any]
) -> bool:
    assignments = ", ".join(f"{column} = ?" for column in values)
    cursor = connection.execute(
        f"UPDATE xhs_requests SET {assignments} WHERE id = ? AND reply_status = ?",
        (*values.values(), request_id, ReplyStatus.PENDING.value),
    )
    return cursor.rowcount == 1


def _require_row(connection: sqlite3.Connection, request_id: int) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM xhs_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise RequestNotFound(f"Request {request_id} was not found")
    return row


def _require_notification_row(
    connection: sqlite3.Connection, notification_id: int
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM notification_outbox WHERE id = ?", (notification_id,)
    ).fetchone()
    if row is None:
        raise RequestNotFound(f"Notification {notification_id} was not found")
    return row


def _enqueue_notification(
    connection: sqlite3.Connection,
    kind: str,
    *,
    request_id: int | None,
    code: str,
    dedupe_key: str,
    now: datetime,
) -> OutboxNotification:
    """Insert one metadata-only event using the caller's active transaction."""
    if kind not in NOTIFICATION_KINDS:
        raise ValueError("unsupported notification kind")
    if request_id is not None and (
        isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1
    ):
        raise ValueError("request_id must be a positive integer or None")
    if kind == "PAUSE" and request_id is not None:
        raise ValueError("pause notifications cannot reference a request")
    if kind != "PAUSE" and request_id is None:
        raise ValueError("request notification requires request_id")
    if not isinstance(dedupe_key, str) or not dedupe_key:
        raise ValueError("dedupe_key must be a non-empty string")
    _require_aware_datetime(now, "now")
    safe_code = _safe_notification_code(code)
    stored_key = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()
    if request_id is not None:
        _require_row(connection, request_id)
    connection.execute(
        """
        INSERT OR IGNORE INTO notification_outbox (
            kind, request_id, code, dedupe_key, created_at, attempt_count
        ) VALUES (?, ?, ?, ?, ?, 0)
        """,
        (kind, request_id, safe_code, stored_key, _format_datetime(now)),
    )
    row = connection.execute(
        "SELECT * FROM notification_outbox WHERE dedupe_key = ?", (stored_key,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Notification event could not be read back")
    if (
        row["kind"] != kind
        or row["request_id"] != request_id
        or row["code"] != safe_code
    ):
        raise NotificationIdempotencyConflict(
            "notification dedupe key identifies a different event"
        )
    return _notification_from_row(row)


def _validate_mention(mention: NewMention) -> None:
    if not isinstance(mention, NewMention):
        raise TypeError("mention must be a NewMention")
    for field_name in (
        "note_id", "note_url", "mention_id", "sender_user_id", "comment_id", "comment_text"
    ):
        value = getattr(mention, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
    _require_aware_datetime(mention.created_at, "created_at")


def _request_key(mention: NewMention) -> str:
    return _request_key_values(
        mention.note_id, mention.sender_user_id, mention.comment_text
    )


def _request_key_values(note_id: str, sender_user_id: str, comment_text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", comment_text).split()).casefold()
    payload = json.dumps(
        [note_id, sender_user_id, normalized], ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_note_url(note_url: str) -> str:
    try:
        parts = urlsplit(note_url)
    except ValueError as error:
        raise ValueError("note_url must be a valid URL") from error
    if "@" in parts.netloc:
        raise ValueError("note_url must not contain credentials")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _safe_persisted_code(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("persisted code must be a string")
    return value if value in SAFE_PERSISTED_CODES else "REDACTED"


def _safe_notification_code(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("notification code must be a string")
    return value if value in SAFE_NOTIFICATION_CODES else "REDACTED"


def _serialize_note(note: NoteContext) -> str:
    safe_note = note.model_copy(update={"url": _safe_note_url(note.url)})
    return safe_note.model_dump_json()


def _resolution_values(resolution: Resolution) -> dict[str, Any]:
    return {
        "title": resolution.title or None,
        "original_title": resolution.original_title or None,
        "media_type": resolution.media_type if resolution.media_type != "unknown" else None,
        "year": resolution.year,
        "season": resolution.season,
        "confidence": resolution.confidence,
        "resolution_reason": resolution.reason or None,
    }


def _match_values(match: MediaMatch) -> dict[str, Any]:
    return {
        "title": match.title,
        "original_title": match.original_title or None,
        "media_type": match.media_type,
        "year": match.year,
        "season": match.season,
        "media_source": match.source,
        "media_source_id": match.source_id,
        "tmdb_id": match.tmdb_id,
        "score": match.score,
    }


def _request_from_row(row: sqlite3.Row) -> StoredRequest:
    return StoredRequest(
        id=row["id"], note_id=row["note_id"], note_url=row["note_url"],
        mention_id=row["mention_id"], sender_user_id=row["sender_user_id"],
        comment_id=row["comment_id"], comment_text=row["comment_text"],
        request_key=row["request_key"], created_at=_parse_datetime(row["created_at"], "created_at"),
        updated_at=_parse_datetime(row["updated_at"], "updated_at"),
        status=RequestStatus(row["status"]), note=_note_from_row(row["note_snapshot"]),
        title=row["title"],
        original_title=row["original_title"], media_type=row["media_type"], year=row["year"],
        season=row["season"], confidence=row["confidence"],
        resolution_reason=row["resolution_reason"], media_source=row["media_source"],
        media_source_id=row["media_source_id"], tmdb_id=row["tmdb_id"], score=row["score"],
        subscription_id=row["subscription_id"], error=row["error"],
        reply_status=ReplyStatus(row["reply_status"]), reply_id=row["reply_id"],
        replied_at=_parse_datetime(row["replied_at"], "replied_at", required=False),
        attempt_count=row["attempt_count"],
    )


def _note_from_row(value: Any) -> NoteContext | None:
    if value is None:
        return None
    try:
        return NoteContext.model_validate_json(value)
    except Exception:
        raise RuntimeError("Persisted request has an invalid note snapshot") from None


def _runtime_state_from_row(row: sqlite3.Row) -> RuntimeState:
    return RuntimeState(
        browser_state=BrowserState(row["browser_state"]), pause_code=row["pause_code"],
        paused_at=_parse_datetime(row["paused_at"], "paused_at", required=False),
        pause_notified=bool(row["pause_notified"]),
    )


def _notification_from_row(row: sqlite3.Row) -> OutboxNotification:
    return OutboxNotification(
        id=row["id"],
        kind=row["kind"],
        request_id=row["request_id"],
        code=row["code"],
        dedupe_key=row["dedupe_key"],
        created_at=_parse_datetime(row["created_at"], "created_at"),
        last_attempt_at=_parse_datetime(
            row["last_attempt_at"], "last_attempt_at", required=False
        ),
        delivered_at=_parse_datetime(
            row["delivered_at"], "delivered_at", required=False
        ),
        attempt_count=row["attempt_count"],
    )


def _parse_datetime(value: Any, field_name: str, *, required: bool = True) -> datetime | None:
    if value is None:
        if required:
            raise RuntimeError(f"Persisted request has no {field_name}")
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _format_datetime(value: datetime) -> str:
    _require_aware_datetime(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

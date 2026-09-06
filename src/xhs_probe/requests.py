"""SQLite persistence for Xiaohongshu media requests."""

import hashlib
import json
import os
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class RequestStatus(StrEnum):
    """Processing state of a persisted media request."""

    NEW = "NEW"


@dataclass(frozen=True)
class NewXhsRequest:
    """An authorized Xiaohongshu request ready to be persisted."""

    note_id: str
    note_url: str
    mention_id: str
    sender_user_id: str
    comment_id: str
    comment_text: str
    created_at: datetime
    note_title: str | None = None
    note_content: str | None = None
    note_author_id: str | None = None
    note_author_name: str | None = None


@dataclass(frozen=True)
class XhsRequest:
    """A Xiaohongshu request stored in SQLite."""

    id: int
    note_id: str
    note_url: str
    mention_id: str
    sender_user_id: str
    comment_id: str
    comment_text: str
    created_at: datetime
    status: RequestStatus
    note_title: str | None
    note_content: str | None
    note_author_id: str | None
    note_author_name: str | None
    detected_title: str | None
    media_type: str | None
    year: int | None
    season: int | None
    mp_id: str | None
    confidence: float | None
    error: str | None
    processed_at: datetime | None


@dataclass(frozen=True)
class SaveResult:
    """Outcome of persisting one request."""

    request: XhsRequest
    created: bool


class XhsRequestRepository:
    """Persist and retrieve Xiaohongshu requests from one SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_database_file()
        self._initialize()

    def save(self, request: NewXhsRequest) -> SaveResult:
        """Persist a new request."""
        _validate_request(request)
        created_at = _format_datetime(request.created_at)
        request_key = _build_request_key(request)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO xhs_requests (
                    note_id,
                    note_url,
                    mention_id,
                    sender_user_id,
                    comment_id,
                    comment_text,
                    created_at,
                    status,
                    request_key,
                    note_title,
                    note_content,
                    note_author_id,
                    note_author_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.note_id,
                    request.note_url,
                    request.mention_id,
                    request.sender_user_id,
                    request.comment_id,
                    request.comment_text,
                    created_at,
                    RequestStatus.NEW.value,
                    request_key,
                    request.note_title,
                    request.note_content,
                    request.note_author_id,
                    request.note_author_name,
                ),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    """
                    SELECT * FROM xhs_requests
                    WHERE mention_id = ? OR request_key = ?
                    ORDER BY CASE WHEN mention_id = ? THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (request.mention_id, request_key, request.mention_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Duplicate request could not be read back")
                return SaveResult(request=_request_from_row(row), created=False)

            row = connection.execute(
                "SELECT * FROM xhs_requests WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Persisted request could not be read back")
        return SaveResult(request=_request_from_row(row), created=True)

    def get_by_mention_id(self, mention_id: str) -> XhsRequest | None:
        """Return a request by its source mention identifier."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM xhs_requests WHERE mention_id = ?",
                (mention_id,),
            ).fetchone()
        return _request_from_row(row) if row is not None else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_database_file(self) -> None:
        try:
            descriptor = os.open(
                self._database_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._database_path.chmod(0o600)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS xhs_requests (
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
                    processed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_xhs_requests_mention_id
                ON xhs_requests (mention_id)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_xhs_requests_request_key
                ON xhs_requests (request_key)
                """
            )


def _format_datetime(value: datetime) -> str:
    utc_value = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc_value.isoformat().replace("+00:00", "Z")


def _validate_request(request: NewXhsRequest) -> None:
    for field_name in (
        "note_id",
        "note_url",
        "mention_id",
        "sender_user_id",
        "comment_id",
        "comment_text",
    ):
        value = getattr(request, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must not be empty")
    if request.created_at.tzinfo is None or request.created_at.utcoffset() is None:
        raise ValueError("created_at must include a timezone")


def _build_request_key(request: NewXhsRequest) -> str:
    normalized_comment = " ".join(
        unicodedata.normalize("NFKC", request.comment_text).split()
    ).casefold()
    key_input = json.dumps(
        [request.note_id, request.sender_user_id, normalized_comment],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(key_input.encode("utf-8")).hexdigest()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _request_from_row(row: sqlite3.Row) -> XhsRequest:
    created_at = _parse_datetime(row["created_at"])
    if created_at is None:
        raise RuntimeError("Persisted request has no creation time")
    return XhsRequest(
        id=row["id"],
        note_id=row["note_id"],
        note_url=row["note_url"],
        mention_id=row["mention_id"],
        sender_user_id=row["sender_user_id"],
        comment_id=row["comment_id"],
        comment_text=row["comment_text"],
        created_at=created_at,
        status=RequestStatus(row["status"]),
        note_title=row["note_title"],
        note_content=row["note_content"],
        note_author_id=row["note_author_id"],
        note_author_name=row["note_author_name"],
        detected_title=row["detected_title"],
        media_type=row["media_type"],
        year=row["year"],
        season=row["season"],
        mp_id=row["mp_id"],
        confidence=row["confidence"],
        error=row["error"],
        processed_at=_parse_datetime(row["processed_at"]),
    )

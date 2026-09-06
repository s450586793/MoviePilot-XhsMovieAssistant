"""Convert captured mention messages into authorized media requests."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from xhs_probe.contracts import MentionsPayloadError
from xhs_probe.requests import NewXhsRequest


@dataclass(frozen=True)
class ExtractionResult:
    """Authorized requests and counts for messages that were not accepted."""

    requests: tuple[NewXhsRequest, ...]
    ignored_count: int
    invalid_count: int


def extract_requests(
    payload: Mapping[str, Any],
    *,
    authorized_user_id: str,
    site_url: str,
    captured_at: datetime,
) -> ExtractionResult:
    """Extract valid requests sent by exactly one authorized account ID."""
    authorized_user_id = authorized_user_id.strip()
    if not authorized_user_id:
        raise ValueError("authorized_user_id must be configured")

    data = payload.get("data")
    messages = data.get("message_list") if isinstance(data, Mapping) else None
    if not isinstance(messages, list):
        raise MentionsPayloadError("mentions response is missing data.message_list")

    accepted: list[NewXhsRequest] = []
    ignored_count = 0
    invalid_count = 0
    for raw_message in messages:
        if not isinstance(raw_message, Mapping):
            invalid_count += 1
            continue

        user_info = _mapping(raw_message.get("user_info"))
        sender_user_id = _text(user_info.get("userid") or user_info.get("user_id"))
        if sender_user_id != authorized_user_id:
            ignored_count += 1
            continue

        comment_info = _mapping(raw_message.get("comment_info"))
        item_info = _mapping(raw_message.get("item_info"))
        required = {
            "mention_id": _text(raw_message.get("id")),
            "comment_id": _text(comment_info.get("id")),
            "comment_text": _text(comment_info.get("content")),
            "note_id": _text(item_info.get("id")),
        }
        if not all(required.values()):
            invalid_count += 1
            continue

        note_user_info = _mapping(item_info.get("user_info"))
        accepted.append(
            NewXhsRequest(
                note_id=required["note_id"],
                note_url=_build_note_url(site_url, required["note_id"]),
                mention_id=required["mention_id"],
                sender_user_id=sender_user_id,
                comment_id=required["comment_id"],
                comment_text=required["comment_text"],
                created_at=_message_created_at(raw_message.get("time"), captured_at),
                note_title=_text(item_info.get("content")) or None,
                note_author_id=_text(
                    note_user_info.get("userid") or note_user_info.get("user_id")
                )
                or None,
                note_author_name=_text(note_user_info.get("nickname")) or None,
            )
        )

    return ExtractionResult(
        requests=tuple(accepted),
        ignored_count=ignored_count,
        invalid_count=invalid_count,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _build_note_url(site_url: str, note_id: str) -> str:
    hostname = (urlsplit(site_url).hostname or "").lower()
    encoded_note_id = quote(note_id, safe="")
    if hostname == "rednote.com" or hostname.endswith(".rednote.com"):
        return f"https://www.rednote.com/discovery/item/{encoded_note_id}"
    if hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com"):
        return f"https://www.xiaohongshu.com/explore/{encoded_note_id}"
    raise ValueError("site_url must use a RedNote or Xiaohongshu hostname")


def _message_created_at(value: object, fallback: datetime) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    timestamp = float(value)
    if timestamp >= 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return fallback

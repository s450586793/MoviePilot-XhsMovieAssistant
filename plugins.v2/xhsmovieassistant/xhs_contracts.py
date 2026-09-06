"""Normalize untrusted Xiaohongshu mention payloads into transient values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
import unicodedata


@dataclass(frozen=True)
class TransientMention:
    """One authorized-candidate mention; its token is never durable or loggable."""

    mention_id: str
    sender_user_id: str
    comment_id: str
    comment_text: str
    note_id: str
    xsec_token: str = field(repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parent_comment_id: str | None = None


def parse_authorized_ids(raw: object) -> frozenset[str]:
    """Parse configured stable sender IDs separated by commas or lines."""
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(
        identifier
        for value in raw.replace(",", "\n").splitlines()
        if (identifier := _identifier(value))
    )


def parse_mentions_payload(payload: object) -> tuple[TransientMention, ...]:
    """Return valid ``mention/comment`` messages without retaining raw payloads."""
    data = _mapping(payload).get("data")
    messages = _mapping(data).get("message_list")
    if not isinstance(messages, list):
        return ()

    mentions: list[TransientMention] = []
    for raw in messages:
        message = _mapping(raw)
        if message.get("type") != "mention/comment":
            continue
        user = _mapping(message.get("user_info"))
        comment = _mapping(message.get("comment_info"))
        item = _mapping(message.get("item_info"))
        mention_id = _identifier(message.get("id"))
        sender_user_id = _identifier(user.get("userid") or user.get("user_id"))
        comment_id = _identifier(comment.get("id") or comment.get("comment_id"))
        comment_text = _text(comment.get("content"), 1000)
        note_id = _identifier(item.get("id") or item.get("note_id"))
        if not all((mention_id, sender_user_id, comment_id, comment_text, note_id)):
            continue
        parent = _mapping(comment.get("target_comment"))
        parent_comment_id = _identifier(parent.get("id") or parent.get("comment_id")) or None
        mentions.append(
            TransientMention(
                mention_id=mention_id,
                sender_user_id=sender_user_id,
                comment_id=comment_id,
                comment_text=comment_text,
                note_id=note_id,
                xsec_token=_text(item.get("xsec_token"), 2048),
                created_at=_timestamp(message.get("time", message.get("timestamp"))),
                parent_comment_id=parent_comment_id,
            )
        )
    return tuple(mentions)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _identifier(value: object) -> str:
    return _text(value, 512).replace("\n", "").replace("\t", "")


def _text(value: object, limit: int) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(
        character
        for character in text
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    return text.strip()[:limit]


def _timestamp(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return datetime.now(timezone.utc)
    timestamp = float(value)
    if timestamp >= 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc)

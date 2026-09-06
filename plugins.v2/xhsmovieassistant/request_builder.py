"""Build sanitized media-resolution requests from transient XHS data."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol
import unicodedata

from .models import MediaRequest, NoteContext
from .xhs_contracts import TransientMention


TEXT_LIMITS = {"title": 300, "content": 6000, "comment": 1000, "author": 100}
_BRACKETED_TITLE = re.compile(r"《[^》]+》")
_YEAR = re.compile(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)")
_TITLE_TOKEN = re.compile(r"[\u4e00-\u9fffA-Za-z]{2,}")


class NoteDetailLike(Protocol):
    """Structured note data supplied by the future XHS gateway."""

    url: object
    type: object
    title: object
    content: object
    author: object
    comments: object


def sanitize_text(value: object, limit: int) -> str:
    """Normalize and bound untrusted text before it enters a domain model."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(
        character
        for character in text
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    return text.strip()[:limit]


def build_media_request(mention: TransientMention, detail: NoteDetailLike) -> MediaRequest:
    """Create the only serializable request shape consumed by media resolution."""
    note_type = sanitize_text(detail.type, 16)
    return MediaRequest(
        request_id=f"xhs_{mention.mention_id}",
        source="xiaohongshu",
        intent="subscribe",
        trigger_comment=sanitize_text(mention.comment_text, TEXT_LIMITS["comment"]),
        note=NoteContext(
            id=mention.note_id,
            url=sanitize_text(detail.url, 2048),
            type=note_type if note_type in {"normal", "video", "unknown"} else "unknown",
            title=sanitize_text(detail.title, TEXT_LIMITS["title"]),
            content=sanitize_text(detail.content, TEXT_LIMITS["content"]),
            author=sanitize_text(detail.author, TEXT_LIMITS["author"]),
            relevant_comments=_relevant_comments(detail.comments),
        ),
    )


def _relevant_comments(values: object) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return []
    unique: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for position, value in enumerate(values):
        comment = sanitize_text(value, TEXT_LIMITS["comment"])
        deduplication_key = comment.casefold()
        if not comment or deduplication_key in seen:
            continue
        seen.add(deduplication_key)
        unique.append((-_relevance_score(comment), position, comment))
    unique.sort()
    return [comment for _, _, comment in unique[:10]]


def _relevance_score(comment: str) -> int:
    score = 3 if _BRACKETED_TITLE.search(comment) else 0
    score += 2 if _YEAR.search(comment) else 0
    tokens = _TITLE_TOKEN.findall(comment)
    if len(tokens) != len(set(tokens)):
        score += 1
    return score

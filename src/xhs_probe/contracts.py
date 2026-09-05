"""Pure response-contract helpers for the Phase 1 probe."""

from collections import Counter
from typing import Any, Mapping
from urllib.parse import urlsplit


MENTIONS_API_PATH = "/api/sns/web/v1/you/mentions"


class MentionsPayloadError(ValueError):
    """Raised when the intercepted response no longer matches the known contract."""


def _has_value(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def is_mentions_api_url(url: str) -> bool:
    """Return whether a browser response is the mentions endpoint."""
    return urlsplit(url).path == MENTIONS_API_PATH


def summarize_mentions_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    """Return a value-free summary of fields needed by the next phase."""
    data = payload.get("data")
    messages = data.get("message_list") if isinstance(data, Mapping) else None
    if not isinstance(messages, list):
        raise MentionsPayloadError("mentions response is missing data.message_list")

    relation_types: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    for raw_message in messages:
        if not isinstance(raw_message, Mapping):
            continue

        relation_type = raw_message.get("type")
        if _has_value(relation_type):
            relation_types[str(relation_type)] += 1

        user_info = raw_message.get("user_info")
        comment_info = raw_message.get("comment_info")
        item_info = raw_message.get("item_info")
        user_info = user_info if isinstance(user_info, Mapping) else {}
        comment_info = comment_info if isinstance(comment_info, Mapping) else {}
        item_info = item_info if isinstance(item_info, Mapping) else {}

        fields = {
            "mention_id": raw_message.get("id"),
            "sender_user_id": user_info.get("userid") or user_info.get("user_id"),
            "comment_id": comment_info.get("id"),
            "comment_text": comment_info.get("content"),
            "note_id": item_info.get("id"),
            "xsec_token": item_info.get("xsec_token"),
        }
        field_counts.update(name for name, value in fields.items() if _has_value(value))

    return {
        "api_success": bool(payload.get("success") or payload.get("code") == 0),
        "message_count": len(messages),
        "relation_types": dict(sorted(relation_types.items())),
        "required_field_counts": {
            name: field_counts[name]
            for name in (
                "mention_id",
                "sender_user_id",
                "comment_id",
                "comment_text",
                "note_id",
                "xsec_token",
            )
        },
    }

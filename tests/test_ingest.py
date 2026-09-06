from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from xhs_probe import ingest


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _payload() -> dict:
    return json.loads((FIXTURE_DIR / "mentions_success.json").read_text())


def test_extract_requests_keeps_only_the_authorized_sender() -> None:
    payload = _payload()
    captured_at = datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc)

    result = ingest.extract_requests(
        payload,
        authorized_user_id="user-main",
        site_url="https://www.rednote.com/notification",
        captured_at=captured_at,
    )

    assert result.ignored_count == 1
    assert result.invalid_count == 0
    assert len(result.requests) == 1
    request = result.requests[0]
    assert request.mention_id == "mention-1"
    assert request.sender_user_id == "user-main"
    assert request.comment_id == "comment-1"
    assert request.comment_text == "@assistant want to watch"
    assert request.note_id == "note-1"
    assert request.note_url == "https://www.rednote.com/discovery/item/note-1"
    assert request.note_title == "A movie recommendation"
    assert request.created_at == captured_at
    assert "xsec_token" not in asdict(request)


@pytest.mark.parametrize("timestamp", [1788662983, 1788662983000])
def test_extract_requests_uses_the_notification_timestamp(timestamp: int) -> None:
    payload = _payload()
    payload["data"]["message_list"][0]["time"] = timestamp

    result = ingest.extract_requests(
        payload,
        authorized_user_id="user-main",
        site_url="https://www.rednote.com/notification",
        captured_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )

    assert result.requests[0].created_at == datetime(
        2026, 9, 6, 2, 49, 43, tzinfo=timezone.utc
    )


def test_extract_requests_rejects_an_empty_authorized_user_id() -> None:
    with pytest.raises(ValueError, match="authorized_user_id must be configured"):
        ingest.extract_requests(
            _payload(),
            authorized_user_id="   ",
            site_url="https://www.rednote.com/notification",
            captured_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        )

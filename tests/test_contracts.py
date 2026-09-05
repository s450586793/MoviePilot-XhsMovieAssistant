import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xhs_probe import contracts
from xhs_probe import storage


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_summary_reports_field_presence_without_exposing_values() -> None:
    payload = json.loads((FIXTURE_DIR / "mentions_success.json").read_text())

    summarize = getattr(contracts, "summarize_mentions_payload", None)
    assert summarize is not None, "summarize_mentions_payload is not implemented"

    summary = summarize(payload)

    assert summary == {
        "api_success": True,
        "message_count": 2,
        "relation_types": {
            "comment/comment": 1,
            "mention/comment": 1,
        },
        "required_field_counts": {
            "mention_id": 2,
            "sender_user_id": 2,
            "comment_id": 2,
            "comment_text": 2,
            "note_id": 2,
            "xsec_token": 1,
        },
    }
    serialized = json.dumps(summary)
    assert "user-main" not in serialized
    assert "xsec-secret" not in serialized


def test_mentions_url_matches_only_the_expected_api_path() -> None:
    matches = getattr(contracts, "is_mentions_api_url", None)
    assert matches is not None, "is_mentions_api_url is not implemented"

    assert matches(
        "https://www.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20&cursor="
    )
    assert not matches("https://www.xiaohongshu.com/notification")
    assert not matches("https://www.xiaohongshu.com/api/sns/web/v1/you/mentions/archive")


def test_summary_rejects_payload_without_message_list() -> None:
    error_type = getattr(contracts, "MentionsPayloadError", ValueError)

    with pytest.raises(error_type, match="data.message_list"):
        contracts.summarize_mentions_payload({"success": True, "data": {}})


def test_raw_capture_is_created_with_restricted_permissions(tmp_path: Path) -> None:
    writer = getattr(storage, "write_raw_capture", None)
    assert writer is not None, "write_raw_capture is not implemented"

    capture_path = writer(
        output_dir=tmp_path / "probe",
        response_url="https://www.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20",
        payload={"success": True, "secret": "raw-value"},
        captured_at=datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert capture_path.name == "mentions-20260906T010203Z.json"
    assert stat.S_IMODE(capture_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(capture_path.stat().st_mode) == 0o600
    assert json.loads(capture_path.read_text()) == {
        "captured_at": "2026-09-06T01:02:03Z",
        "response_url": "https://www.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20",
        "payload": {"success": True, "secret": "raw-value"},
    }

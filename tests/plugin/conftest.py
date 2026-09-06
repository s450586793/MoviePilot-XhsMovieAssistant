import pytest


@pytest.fixture
def media_request_values() -> dict[str, object]:
    return {
        "request_id": "xhs_mention_123",
        "source": "xiaohongshu",
        "intent": "subscribe",
        "trigger_comment": "想看",
        "note": {
            "id": "note-1",
            "url": "https://www.xiaohongshu.com/explore/note-1",
        },
    }

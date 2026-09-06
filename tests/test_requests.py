from datetime import datetime, timezone
import stat

import pytest

from xhs_probe.requests import (
    NewXhsRequest,
    RequestStatus,
    XhsRequestRepository,
)


def _incoming_request(**overrides) -> NewXhsRequest:
    values = {
        "note_id": "note-1",
        "note_url": "https://www.rednote.com/discovery/item/note-1",
        "mention_id": "mention-1",
        "sender_user_id": "user-main",
        "comment_id": "comment-1",
        "comment_text": "@assistant want to watch",
        "created_at": datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc),
        "note_title": "A movie recommendation",
        "note_author_id": "author-1",
        "note_author_name": "Author",
    }
    values.update(overrides)
    return NewXhsRequest(**values)


def test_save_persists_a_new_xhs_request(tmp_path) -> None:
    repository = XhsRequestRepository(tmp_path / "app.db")
    incoming = _incoming_request()

    result = repository.save(incoming)
    stored = repository.get_by_mention_id("mention-1")

    assert result.created is True
    assert stored == result.request
    assert stored.id == 1
    assert stored.status == RequestStatus.NEW
    assert stored.note_id == "note-1"
    assert stored.note_url == "https://www.rednote.com/discovery/item/note-1"
    assert stored.sender_user_id == "user-main"
    assert stored.comment_id == "comment-1"
    assert stored.comment_text == "@assistant want to watch"
    assert stored.created_at == datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc)
    assert stored.note_title == "A movie recommendation"
    assert stored.note_author_id == "author-1"
    assert stored.note_author_name == "Author"
    assert stored.detected_title is None
    assert stored.media_type is None
    assert stored.year is None
    assert stored.season is None
    assert stored.mp_id is None
    assert stored.confidence is None
    assert stored.error is None
    assert stored.processed_at is None


def test_save_treats_the_same_mention_as_a_duplicate(tmp_path) -> None:
    repository = XhsRequestRepository(tmp_path / "app.db")
    incoming = _incoming_request()

    first = repository.save(incoming)
    second = repository.save(incoming)

    assert first.created is True
    assert second.created is False
    assert second.request == first.request


def test_save_deduplicates_the_same_normalized_note_request(tmp_path) -> None:
    repository = XhsRequestRepository(tmp_path / "app.db")
    first_request = _incoming_request(
        comment_text="\N{NO-BREAK SPACE}@Assistant  想看 ",
    )
    repeated_request = _incoming_request(
        mention_id="mention-2",
        comment_id="comment-2",
        comment_text="@assistant 想看",
    )

    first = repository.save(first_request)
    second = repository.save(repeated_request)

    assert first.created is True
    assert second.created is False
    assert second.request.id == first.request.id
    assert repository.get_by_mention_id("mention-2") is None


def test_repository_restricts_database_file_permissions(tmp_path) -> None:
    database_path = tmp_path / "app.db"

    XhsRequestRepository(database_path)

    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "field_name",
    [
        "note_id",
        "note_url",
        "mention_id",
        "sender_user_id",
        "comment_id",
        "comment_text",
    ],
)
def test_save_rejects_empty_required_fields(tmp_path, field_name: str) -> None:
    repository = XhsRequestRepository(tmp_path / "app.db")

    with pytest.raises(ValueError, match=field_name):
        repository.save(_incoming_request(**{field_name: "   "}))


def test_save_rejects_a_created_at_without_timezone(tmp_path) -> None:
    repository = XhsRequestRepository(tmp_path / "app.db")

    with pytest.raises(ValueError, match="created_at must include a timezone"):
        repository.save(
            _incoming_request(created_at=datetime(2026, 9, 6, 1, 2, 3))
        )

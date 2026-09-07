import pytest
from pydantic import ValidationError

from xhsmovieassistant.models import (
    MediaMatch,
    MediaRequest,
    NoteContext,
    ProcessingResult,
    RequestStatus,
    Resolution,
)


def test_media_request_accepts_a_sanitized_request_shape(
    media_request_values: dict[str, object],
) -> None:
    request = MediaRequest(**media_request_values)

    assert request.request_id == "xhs_mention_123"
    assert request.note.id == "note-1"
    assert request.note.relevant_comments == []


@pytest.mark.parametrize("field_name", ["request_id", "source", "intent", "trigger_comment"])
def test_media_request_rejects_empty_required_text(
    media_request_values: dict[str, object], field_name: str
) -> None:
    values = {**media_request_values, field_name: ""}

    with pytest.raises(ValidationError):
        MediaRequest(**values)


def test_note_context_limits_relevant_comments_and_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        NoteContext(
            id="note-1",
            url="https://www.xiaohongshu.com/explore/note-1",
            relevant_comments=[str(number) for number in range(11)],
        )

    with pytest.raises(ValidationError):
        NoteContext(
            id="note-1",
            url="https://www.xiaohongshu.com/explore/note-1",
            xsec_token="must-not-be-modeled",
        )


def test_resolution_accepts_a_concrete_media_result() -> None:
    resolution = Resolution(
        status="resolved",
        title="Interstellar",
        original_title="Interstellar",
        media_type="movie",
        year=2014,
        confidence=0.98,
    )

    assert resolution.status == "resolved"
    assert resolution.season is None


def test_resolution_allows_a_season_for_tv() -> None:
    resolution = Resolution(
        status="resolved",
        title="The Last of Us",
        media_type="tv",
        season=2,
        confidence=0.98,
    )

    assert resolution.season == 2


def test_resolution_rejects_a_season_for_movie() -> None:
    with pytest.raises(ValidationError, match="movie media cannot include a season"):
        Resolution(
            status="resolved",
            title="Interstellar",
            media_type="movie",
            season=1,
            confidence=0.98,
        )


@pytest.mark.parametrize(
    "values",
    [
        {"status": "resolved", "media_type": "movie", "confidence": 0.9},
        {"status": "resolved", "title": "Test", "media_type": "unknown", "confidence": 0.9},
        {"status": "resolved", "title": "Test", "media_type": "book", "confidence": 0.9},
        {"status": "not_media", "confidence": 1.1},
        {"status": "need_confirmation", "confidence": 0.5, "year": 1873},
        {"status": "need_confirmation", "confidence": 0.5, "season": 0},
        {"status": "need_confirmation", "confidence": 0.5, "year": "2014"},
        {"status": "need_confirmation", "confidence": True},
    ],
)
def test_resolution_rejects_invalid_status_dependent_or_bounded_values(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Resolution(**values)


def test_media_match_requires_a_stable_identity_and_valid_score() -> None:
    match = MediaMatch(
        title="Interstellar",
        original_title="Interstellar",
        media_type="movie",
        source="tmdb",
        source_id="157336",
        tmdb_id=157336,
        score=0.99,
    )

    assert match.source_id == "157336"
    assert match.tmdb_id == 157336

    with pytest.raises(ValidationError):
        MediaMatch(
            title="Interstellar",
            media_type="movie",
            source="tmdb",
            source_id="",
            score=1.1,
        )


def test_media_match_allows_a_season_for_tv() -> None:
    match = MediaMatch(
        title="The Last of Us",
        media_type="tv",
        season=2,
        source="tmdb",
        source_id="100088",
    )

    assert match.season == 2


def test_media_match_rejects_a_season_for_movie() -> None:
    with pytest.raises(ValidationError, match="movie media cannot include a season"):
        MediaMatch(
            title="Interstellar",
            media_type="movie",
            season=1,
            source="tmdb",
            source_id="157336",
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("year", "2014"), ("score", True)],
)
def test_media_match_rejects_coerced_numeric_inputs(
    field_name: str, value: object
) -> None:
    values = {
        "title": "Interstellar",
        "media_type": "movie",
        "source": "tmdb",
        "source_id": "157336",
        field_name: value,
    }

    with pytest.raises(ValidationError):
        MediaMatch(**values)


def test_processing_result_preserves_nested_models_and_terminal_status() -> None:
    result = ProcessingResult(
        status=RequestStatus.SUBSCRIBED,
        message="Subscription created",
        resolution=Resolution(
            status="resolved",
            title="Interstellar",
            media_type="movie",
            confidence=0.98,
        ),
        match=MediaMatch(
            title="Interstellar",
            media_type="movie",
            source="tmdb",
            source_id="157336",
            score=0.99,
        ),
        subscription_id="subscription-1",
    )

    assert result.status is RequestStatus.SUBSCRIBED
    assert result.match is not None
    assert result.match.source == "tmdb"
    assert result.subscription_id == "subscription-1"

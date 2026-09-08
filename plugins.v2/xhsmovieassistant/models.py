"""MoviePilot-independent domain models for the Xiaohongshu movie assistant."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestStatus(StrEnum):
    """Processing state of a Xiaohongshu media request."""

    NEW = "NEW"
    FETCHED = "FETCHED"
    RESOLVING = "RESOLVING"
    NEED_CONFIRMATION = "NEED_CONFIRMATION"
    NOT_MEDIA = "NOT_MEDIA"
    MATCHED = "MATCHED"
    DRY_RUN_MATCHED = "DRY_RUN_MATCHED"
    ALREADY_IN_LIBRARY = "ALREADY_IN_LIBRARY"
    ALREADY_SUBSCRIBED = "ALREADY_SUBSCRIBED"
    SUBSCRIBED = "SUBSCRIBED"
    FAILED = "FAILED"
    IGNORED = "IGNORED"


class ReplyStatus(StrEnum):
    """Delivery state of an optional Xiaohongshu comment reply."""

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class BrowserState(StrEnum):
    """Health state of the Xiaohongshu browser session."""

    READY = "READY"
    PAUSED = "PAUSED"


class _DomainModel(BaseModel):
    """Common validation settings for serializable domain values."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


def _validate_media_season(media_type: str, season: int | None) -> None:
    if media_type == "movie" and season is not None:
        raise ValueError("movie media cannot include a season")


class NoteContext(_DomainModel):
    """Sanitized note data available to media resolution."""

    id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    type: Literal["normal", "video", "unknown"] = "unknown"
    title: str = ""
    content: str = ""
    author: str = ""
    relevant_comments: list[str] = Field(default_factory=list, max_length=10)


class MediaRequest(_DomainModel):
    """An authorized, sanitized request to identify media from a note."""

    request_id: str = Field(min_length=1)
    source: Literal["xiaohongshu"]
    intent: Literal["subscribe"]
    trigger_comment: str = Field(min_length=1)
    note: NoteContext


class ResolutionCandidate(_DomainModel):
    """One concrete work named in an otherwise ambiguous media request."""

    title: str = Field(min_length=1)
    original_title: str = ""
    media_type: Literal["movie", "tv"]
    year: int | None = Field(default=None, ge=1874, le=2100)
    season: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_media_season(self) -> "ResolutionCandidate":
        _validate_media_season(self.media_type, self.season)
        return self


class Resolution(_DomainModel):
    """Structured media-identification output."""

    status: Literal["resolved", "need_confirmation", "not_media"]
    title: str = ""
    original_title: str = ""
    media_type: Literal["movie", "tv", "unknown"] = "unknown"
    year: int | None = Field(default=None, ge=1874, le=2100)
    season: int | None = Field(default=None, ge=1)
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    candidates: tuple[ResolutionCandidate, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_resolved_media(self) -> "Resolution":
        if self.status == "resolved" and (
            not self.title or self.media_type not in {"movie", "tv"}
        ):
            raise ValueError("resolved status requires a title and movie or tv media type")
        if self.status != "need_confirmation" and self.candidates:
            raise ValueError("candidates require need_confirmation status")
        _validate_media_season(self.media_type, self.season)
        return self


class MediaMatch(_DomainModel):
    """A deterministic media match with a stable source identity."""

    title: str = Field(min_length=1)
    original_title: str = ""
    media_type: Literal["movie", "tv"]
    year: int | None = Field(default=None, ge=1874, le=2100)
    season: int | None = Field(default=None, ge=1)
    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    tmdb_id: int | None = Field(default=None, ge=1)
    score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_media_season(self) -> "MediaMatch":
        _validate_media_season(self.media_type, self.season)
        return self


class ProcessingResult(_DomainModel):
    """Outcome returned by the request-processing pipeline."""

    status: RequestStatus
    message: str = ""
    resolution: Resolution | None = None
    match: MediaMatch | None = None
    subscription_id: str | None = None

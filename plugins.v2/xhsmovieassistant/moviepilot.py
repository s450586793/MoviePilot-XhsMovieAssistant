"""MoviePilot-native media matching and subscription gateway."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import MediaMatch, RequestStatus, Resolution


_MEDIA_SOURCE_ALIASES = {
    "tmdb": "themoviedb",
    "themoviedb": "themoviedb",
    "douban": "douban",
    "bangumi": "bangumi",
    "anilist": "anilist",
}
_MEDIA_SOURCE_ID_FIELDS = {
    "themoviedb": ("tmdb_id", "tmdbid"),
    "douban": ("douban_id", "doubanid"),
    "bangumi": ("bangumi_id", "bangumiid"),
    "anilist": ("anilist_id", "anilistid"),
}


class MatchDecision(BaseModel):
    """A deterministic candidate selection with its native MoviePilot media."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    match: MediaMatch | None = None
    media_info: Any | None = Field(default=None, exclude=True, repr=False)
    reason_code: str


class SubscriptionOutcome(BaseModel):
    """A sanitized subscription result suitable for request state handling."""

    model_config = ConfigDict(strict=True)

    status: RequestStatus
    subscription_id: str | None = None


@dataclass(frozen=True)
class _Candidate:
    media_info: Any
    match: MediaMatch
    score: float
    exact_title: bool
    exact_type: bool


def _load_moviepilot_runtime() -> Any:
    """Load MoviePilot objects only when an operation needs them."""
    from app.chain.media import MediaChain
    from app.chain.mediaserver import MediaServerChain
    from app.chain.subscribe import SubscribeChain
    from app.core.metainfo import MetaInfo
    from app.schemas.types import MediaType

    return type(
        "MoviePilotRuntime",
        (),
        {
            "MediaChain": MediaChain,
            "MediaServerChain": MediaServerChain,
            "SubscribeChain": SubscribeChain,
            "MetaInfo": MetaInfo,
            "MediaType": MediaType,
        },
    )


def _normalize(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _value(candidate: Any, *names: str) -> Any:
    for name in names:
        value = getattr(candidate, name, None)
        if value is not None:
            return value
    return None


def _type_name(value: Any) -> str:
    to_agent = getattr(value, "to_agent", None)
    if callable(to_agent):
        return _normalize(to_agent())
    return _normalize(getattr(value, "value", value))


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _media_identity(media_info: Any) -> tuple[str, str]:
    source = _normalize(_value(media_info, "source", "media_source"))
    source = _MEDIA_SOURCE_ALIASES.get(source, source)
    explicit_id = _value(media_info, "media_id")
    if source and explicit_id is not None and str(explicit_id).strip():
        return source, str(explicit_id).strip()

    if source in _MEDIA_SOURCE_ID_FIELDS:
        source_id = _value(media_info, *_MEDIA_SOURCE_ID_FIELDS[source])
        if source_id is not None and str(source_id).strip():
            return source, str(source_id).strip()

    for fallback_source, fields in _MEDIA_SOURCE_ID_FIELDS.items():
        source_id = _value(media_info, *fields)
        if source_id is not None and str(source_id).strip():
            return fallback_source, str(source_id).strip()
    return "", ""


def _requested_media_exists(existing: Any, match: MediaMatch) -> bool:
    if not existing:
        return False
    if match.media_type != "tv" or match.season is None:
        return True
    seasons = getattr(existing, "seasons", None)
    if not isinstance(seasons, Mapping):
        return False
    return any(_optional_int(season) == match.season for season in seasons)


class MoviePilotGateway:
    """Match resolved media with MoviePilot's native media search."""

    def match(self, resolution: Resolution) -> MatchDecision:
        """Return one high-confidence candidate, or an explicit non-match outcome."""
        runtime = _load_moviepilot_runtime()
        media_chain = runtime.MediaChain()
        results = self._search(media_chain, resolution.title)
        if (
            resolution.original_title
            and _normalize(resolution.original_title) != _normalize(resolution.title)
        ):
            results.extend(self._search(media_chain, resolution.original_title))

        candidates = self._candidates(results, resolution)
        if not candidates:
            return MatchDecision(reason_code="NO_MATCH")

        candidates.sort(
            key=lambda candidate: (
                -candidate.score,
                candidate.match.source,
                candidate.match.source_id,
            )
        )
        top = candidates[0]
        exact_title_type_candidates = [
            candidate
            for candidate in candidates
            if candidate.exact_title and candidate.exact_type
        ]
        if (
            resolution.year is None and len(exact_title_type_candidates) == 1
        ):
            top = exact_title_type_candidates[0]
            top = _Candidate(
                top.media_info,
                top.match.model_copy(update={"score": 0.8}),
                0.8,
                True,
                True,
            )

        if top.score < 0.8:
            return MatchDecision(
                reason_code="AMBIGUOUS_RESULTS" if len(candidates) > 1 else "NO_MATCH"
            )
        if len(candidates) > 1 and top.score - candidates[1].score < 0.15:
            return MatchDecision(reason_code="AMBIGUOUS_RESULTS")
        return MatchDecision(
            match=top.match,
            media_info=top.media_info,
            reason_code="MATCHED",
        )

    def submit(
        self, decision: MatchDecision, enable_subscription: bool
    ) -> SubscriptionOutcome:
        """Check existing media and submit only through MoviePilot's native chain."""
        if decision.match is None or decision.media_info is None:
            raise ValueError("match decision requires match and media_info")

        try:
            runtime = _load_moviepilot_runtime()
            match = decision.match
            meta = runtime.MetaInfo(match.title)
            if match.year is not None:
                meta.year = str(match.year)
            media_type = runtime.MediaType.from_agent(match.media_type)
            meta.type = media_type
            if match.season is not None:
                meta.begin_season = match.season

            existing = runtime.MediaServerChain().media_exists(decision.media_info)
            if _requested_media_exists(existing, match):
                return SubscriptionOutcome(status=RequestStatus.ALREADY_IN_LIBRARY)
            subscribe_chain = runtime.SubscribeChain()
            if subscribe_chain.exists(decision.media_info, meta):
                return SubscriptionOutcome(status=RequestStatus.ALREADY_SUBSCRIBED)
            if not enable_subscription:
                return SubscriptionOutcome(status=RequestStatus.DRY_RUN_MATCHED)

            subscription_id, _message = subscribe_chain.add(
                title=match.title,
                year=str(match.year or ""),
                mtype=media_type,
                tmdbid=match.tmdb_id,
                media_source=match.source,
                media_id=match.source_id,
                season=match.season,
                username="小红书影视助手",
                message=False,
                exist_ok=True,
            )
        except Exception:
            return SubscriptionOutcome(status=RequestStatus.FAILED)
        if subscription_id is None:
            return SubscriptionOutcome(status=RequestStatus.FAILED)
        return SubscriptionOutcome(
            status=RequestStatus.SUBSCRIBED,
            subscription_id=str(subscription_id),
        )

    @staticmethod
    def _search(media_chain: Any, title: str) -> list[Any]:
        result = media_chain.search(title=title)
        if isinstance(result, tuple):
            result = result[1] if len(result) > 1 else []
        return list(result or [])

    def _candidates(self, results: list[Any], resolution: Resolution) -> list[_Candidate]:
        deduplicated: dict[tuple[str, str], Any] = {}
        for media_info in results:
            source, source_id = _media_identity(media_info)
            if source and source_id:
                deduplicated.setdefault((source, source_id), media_info)

        candidates: list[_Candidate] = []
        requested_title = _normalize(resolution.title)
        requested_original = _normalize(resolution.original_title)
        for media_info in deduplicated.values():
            candidate_type = _type_name(_value(media_info, "type", "media_type"))
            candidate_year = str(_value(media_info, "year") or "")
            if candidate_type and candidate_type != resolution.media_type:
                continue
            if resolution.year is not None and candidate_year != str(resolution.year):
                continue

            title = str(_value(media_info, "title") or "").strip()
            original_title = str(_value(media_info, "original_title") or "").strip()
            normalized_title_match = _normalize(title) == requested_title
            normalized_original_match = bool(requested_original) and (
                _normalize(original_title) == requested_original
            )
            score = 0.55 * normalized_title_match + 0.20 * normalized_original_match
            score += 0.15 if resolution.year is not None and candidate_year == str(resolution.year) else 0.0
            score += 0.10 if candidate_type == resolution.media_type else 0.0
            source, source_id = _media_identity(media_info)
            match = MediaMatch(
                title=title,
                original_title=original_title,
                media_type=resolution.media_type,
                year=_optional_int(_value(media_info, "year")),
                season=resolution.season,
                source=source,
                source_id=source_id,
                tmdb_id=_optional_int(_value(media_info, "tmdb_id", "tmdbid")),
                score=score,
            )
            candidates.append(
                _Candidate(
                    media_info,
                    match,
                    score,
                    normalized_title_match,
                    candidate_type == resolution.media_type,
                )
            )
        return candidates

from types import SimpleNamespace

import pytest

from xhsmovieassistant.models import MediaMatch, RequestStatus, Resolution
from xhsmovieassistant.moviepilot import MatchDecision, MoviePilotGateway


def resolution(
    *,
    title: str,
    year: int | None = None,
    media_type: str = "movie",
) -> Resolution:
    return Resolution(
        status="resolved",
        title=title,
        media_type=media_type,  # type: ignore[arg-type]
        year=year,
        confidence=0.95,
    )


def media(title: str, year: int, media_type: str = "movie") -> SimpleNamespace:
    media_id = 157336 if year == 2014 else year
    return SimpleNamespace(
        title=title,
        original_title="",
        year=str(year),
        type=media_type,
        source="tmdb",
        id=str(media_id),
        tmdb_id=media_id,
    )


class FakeMediaChain:
    def __init__(self, results: list[SimpleNamespace]) -> None:
        self._results = results

    def search(self, *, title: str) -> tuple[SimpleNamespace, list[SimpleNamespace]]:
        return SimpleNamespace(name=title), self._results


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> MoviePilotGateway:
    import xhsmovieassistant.moviepilot as moviepilot_module

    results: list[SimpleNamespace] = []
    monkeypatch.setattr(
        moviepilot_module,
        "_load_moviepilot_runtime",
        lambda: SimpleNamespace(MediaChain=lambda: FakeMediaChain(results)),
    )
    instance = MoviePilotGateway()
    instance.search_results = results
    return instance


def test_same_title_different_year_needs_confirmation(gateway: MoviePilotGateway) -> None:
    gateway.search_results.extend([media("同名", 1990), media("同名", 2024)])

    decision = gateway.match(resolution(title="同名", year=None))

    assert decision.match is None
    assert decision.reason_code == "AMBIGUOUS_RESULTS"


def test_exact_title_year_and_type_selects_one_result(gateway: MoviePilotGateway) -> None:
    gateway.search_results.extend(
        [media("星际穿越", 2014, "movie"), media("星际穿越", 2020, "tv")]
    )

    decision = gateway.match(
        resolution(title="星际穿越", year=2014, media_type="movie")
    )

    assert decision.match is not None
    assert decision.match.tmdb_id == 157336
    assert "media_info" not in decision.model_dump()
    assert "media_info" not in repr(decision)


def test_match_normalizes_moviepilot_media_type_enum(gateway: MoviePilotGateway) -> None:
    class NativeMovieType:
        def to_agent(self) -> str:
            return "movie"

    candidate = media("星际穿越", 2014)
    candidate.type = NativeMovieType()
    gateway.search_results.append(candidate)

    decision = gateway.match(resolution(title="星际穿越", year=2014))

    assert decision.reason_code == "MATCHED"


def test_single_exact_title_type_match_ignores_unrelated_same_type_result(
    gateway: MoviePilotGateway,
) -> None:
    gateway.search_results.extend(
        [media("唯一标题", 2014), media("无关标题", 2024)]
    )

    decision = gateway.match(resolution(title="唯一标题", year=None))

    assert decision.reason_code == "MATCHED"
    assert decision.match is not None
    assert decision.match.title == "唯一标题"


class FakeMetaInfo:
    def __init__(self, title: str) -> None:
        self.title = title
        self.year: str | None = None
        self.type: object | None = None
        self.begin_season: int | None = None


class FakeMediaType:
    @staticmethod
    def from_agent(media_type: str) -> str:
        return f"native-{media_type}"


@pytest.fixture
def subscription_gateway(monkeypatch: pytest.MonkeyPatch) -> tuple[MoviePilotGateway, SimpleNamespace]:
    import xhsmovieassistant.moviepilot as moviepilot_module

    state = SimpleNamespace(
        in_library=False,
        already_subscribed=False,
        add_result=(42, "created"),
        media_exists_calls=[],
        subscribe_exists_calls=[],
        add_calls=[],
    )

    class FakeMediaServerChain:
        def media_exists(self, media_info: object) -> bool:
            state.media_exists_calls.append(media_info)
            if isinstance(state.in_library, Exception):
                raise state.in_library
            return state.in_library

    class FakeSubscribeChain:
        def exists(self, media_info: object, meta: FakeMetaInfo) -> bool:
            state.subscribe_exists_calls.append((media_info, meta))
            if isinstance(state.already_subscribed, Exception):
                raise state.already_subscribed
            return state.already_subscribed

        def add(self, **kwargs: object) -> tuple[int | None, str]:
            state.add_calls.append(kwargs)
            if isinstance(state.add_result, Exception):
                raise state.add_result
            return state.add_result

    monkeypatch.setattr(
        moviepilot_module,
        "_load_moviepilot_runtime",
        lambda: SimpleNamespace(
            MediaServerChain=FakeMediaServerChain,
            SubscribeChain=FakeSubscribeChain,
            MetaInfo=FakeMetaInfo,
            MediaType=FakeMediaType,
        ),
    )
    return MoviePilotGateway(), state


def matched_decision() -> MatchDecision:
    media_info = SimpleNamespace(title="星际穿越")
    return MatchDecision(
        match=MediaMatch(
            title="星际穿越",
            original_title="Interstellar",
            media_type="movie",
            year=2014,
            season=1,
            source="tmdb",
            source_id="157336",
            tmdb_id=157336,
            score=0.95,
        ),
        media_info=media_info,
        reason_code="MATCHED",
    )


def test_submit_stops_when_media_already_exists(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway
    state.in_library = True

    outcome = gateway.submit(matched_decision(), enable_subscription=True)

    assert outcome.status is RequestStatus.ALREADY_IN_LIBRARY
    assert len(state.media_exists_calls) == 1
    assert state.subscribe_exists_calls == []
    assert state.add_calls == []


def test_submit_stops_when_subscription_already_exists(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway
    state.already_subscribed = True

    outcome = gateway.submit(matched_decision(), enable_subscription=True)

    assert outcome.status is RequestStatus.ALREADY_SUBSCRIBED
    assert len(state.subscribe_exists_calls) == 1
    assert state.add_calls == []


def test_submit_dry_run_checks_duplicates_before_skipping_add(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway

    outcome = gateway.submit(matched_decision(), enable_subscription=False)

    assert outcome.status is RequestStatus.DRY_RUN_MATCHED
    assert len(state.media_exists_calls) == 1
    assert len(state.subscribe_exists_calls) == 1
    assert state.add_calls == []


def test_submit_uses_stable_identifiers_without_custom_subscription_options(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway

    outcome = gateway.submit(matched_decision(), enable_subscription=True)

    assert outcome.status is RequestStatus.SUBSCRIBED
    assert outcome.subscription_id == "42"
    assert state.add_calls == [
        {
            "title": "星际穿越",
            "year": "2014",
            "mtype": "native-movie",
            "tmdbid": 157336,
            "media_source": "tmdb",
            "media_id": "157336",
            "season": 1,
            "username": "小红书影视助手",
            "message": False,
            "exist_ok": True,
        }
    ]


def test_submit_maps_native_add_failure_without_exposing_exception_text(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway
    state.add_result = RuntimeError("https://provider.invalid?api_key=secret")

    outcome = gateway.submit(matched_decision(), enable_subscription=True)

    assert outcome.status is RequestStatus.FAILED
    assert "secret" not in repr(outcome)


def test_submit_maps_media_library_check_failure_without_exposing_exception_text(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway
    state.in_library = RuntimeError("https://provider.invalid?api_key=secret")

    outcome = gateway.submit(matched_decision(), enable_subscription=True)

    assert outcome.status is RequestStatus.FAILED
    assert "secret" not in repr(outcome)


def test_submit_maps_existing_subscription_check_failure_without_exposing_exception_text(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway
    state.already_subscribed = RuntimeError("https://provider.invalid?api_key=secret")

    outcome = gateway.submit(matched_decision(), enable_subscription=True)

    assert outcome.status is RequestStatus.FAILED
    assert "secret" not in repr(outcome)

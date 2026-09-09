from types import SimpleNamespace

import pytest

from xhsmovieassistant.models import MediaMatch, RequestStatus, Resolution
from xhsmovieassistant.moviepilot import MatchDecision, MoviePilotGateway


def resolution(
    *,
    title: str,
    year: int | None = None,
    media_type: str = "movie",
    season: int | None = None,
) -> Resolution:
    return Resolution(
        status="resolved",
        title=title,
        media_type=media_type,  # type: ignore[arg-type]
        year=year,
        season=season,
        confidence=0.95,
    )


def media(title: str, year: int, media_type: str = "movie") -> SimpleNamespace:
    media_id = 157336 if year == 2014 else year
    return SimpleNamespace(
        source="themoviedb",
        scrape_source=None,
        type=media_type,
        title=title,
        en_title="",
        original_title="",
        year=str(year),
        season=None,
        tmdb_id=media_id,
        douban_id=None,
        bangumi_id=None,
        anilist_id=None,
        media_id=None,
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
    assert [(item.title, item.year) for item in decision.candidates] == [
        ("同名", 1990),
        ("同名", 2024),
    ]


def test_ambiguous_results_exclude_candidates_matching_only_media_type(
    gateway: MoviePilotGateway,
) -> None:
    gateway.search_results.extend(
        [
            media("开庭", 2013, "tv"),
            media("开庭", 2026, "tv"),
            media("Night Court", 1984, "tv"),
            media("Court Sort VR", 2023, "tv"),
        ]
    )

    decision = gateway.match(resolution(title="开庭", year=None, media_type="tv"))

    assert decision.reason_code == "AMBIGUOUS_RESULTS"
    assert [(item.title, item.year) for item in decision.candidates] == [
        ("开庭", 2013),
        ("开庭", 2026),
    ]


def test_cross_source_records_for_the_same_work_are_one_match(
    gateway: MoviePilotGateway,
) -> None:
    tmdb = media("掉链子刑警", 2019, "tv")
    douban = media("掉链子刑警", 2019, "tv")
    douban.source = "douban"
    douban.tmdb_id = None
    douban.douban_id = "30474691"
    gateway.search_results.extend([douban, tmdb])

    decision = gateway.match(
        resolution(title="掉链子刑警", year=2019, media_type="tv")
    )

    assert decision.reason_code == "MATCHED"
    assert decision.match is not None
    assert decision.match.source == "themoviedb"
    assert decision.match.source_id == "2019"


def test_select_candidate_rehydrates_the_exact_stable_identity(
    gateway: MoviePilotGateway,
) -> None:
    older = media("同名", 1990)
    newer = media("同名", 2024)
    gateway.search_results.extend([older, newer])
    selected = MediaMatch(
        title="同名",
        media_type="movie",
        year=2024,
        source="themoviedb",
        source_id="2024",
        tmdb_id=2024,
        score=0.65,
    )

    decision = gateway.select(selected)

    assert decision.reason_code == "SELECTED"
    assert decision.match is not None
    assert decision.match.source_id == "2024"
    assert decision.media_info is newer


def test_exact_title_year_and_type_selects_one_result(gateway: MoviePilotGateway) -> None:
    gateway.search_results.extend(
        [media("星际穿越", 2014, "movie"), media("星际穿越", 2020, "tv")]
    )

    decision = gateway.match(
        resolution(title="星际穿越", year=2014, media_type="movie")
    )

    assert decision.match is not None
    assert decision.match.tmdb_id == 157336
    assert decision.match.source == "themoviedb"
    assert decision.match.source_id == "157336"
    assert "media_info" not in decision.model_dump()
    assert "media_info" not in repr(decision)


def test_match_uses_real_moviepilot_douban_identity_shape(
    gateway: MoviePilotGateway,
) -> None:
    candidate = media("霸王别姬", 1993)
    candidate.source = "douban"
    candidate.tmdb_id = None
    candidate.douban_id = "1291546"
    gateway.search_results.append(candidate)

    decision = gateway.match(resolution(title="霸王别姬", year=1993))

    assert decision.reason_code == "MATCHED"
    assert decision.match is not None
    assert decision.match.source == "douban"
    assert decision.match.source_id == "1291546"


def test_match_propagates_requested_season_instead_of_candidate_season(
    gateway: MoviePilotGateway,
) -> None:
    candidate = media("最后生还者", 2023, "tv")
    candidate.season = 1
    gateway.search_results.append(candidate)

    decision = gateway.match(
        resolution(title="最后生还者", year=2023, media_type="tv", season=2)
    )

    assert decision.reason_code == "MATCHED"
    assert decision.match is not None
    assert decision.match.season == 2


def test_match_normalizes_moviepilot_media_type_enum(gateway: MoviePilotGateway) -> None:
    class NativeMovieType:
        def to_agent(self) -> str:
            return "movie"

    candidate = media("星际穿越", 2014)
    candidate.type = NativeMovieType()
    gateway.search_results.append(candidate)

    decision = gateway.match(resolution(title="星际穿越", year=2014))

    assert decision.reason_code == "MATCHED"


@pytest.mark.parametrize("candidate_type", [None, "", "   ", "unknown", "unsupported"])
def test_match_rejects_candidate_without_exact_supported_type(
    gateway: MoviePilotGateway, candidate_type: str | None
) -> None:
    candidate = media("星际穿越", 2014)
    candidate.original_title = "Interstellar"
    if candidate_type is None:
        del candidate.type
    else:
        candidate.type = candidate_type
    gateway.search_results.append(candidate)
    requested = resolution(title="星际穿越", year=2014).model_copy(
        update={"original_title": "Interstellar"}
    )

    decision = gateway.match(requested)

    assert decision.match is None
    assert decision.reason_code == "NO_MATCH"


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


def test_submit_rejects_bypassed_movie_season_before_loading_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xhsmovieassistant.moviepilot as moviepilot_module

    load_calls: list[bool] = []

    def load_runtime() -> object:
        load_calls.append(True)
        raise AssertionError("runtime must not load")

    monkeypatch.setattr(moviepilot_module, "_load_moviepilot_runtime", load_runtime)
    invalid_match = MediaMatch.model_construct(
        title="星际穿越",
        original_title="Interstellar",
        media_type="movie",
        year=2014,
        season=1,
        source="tmdb",
        source_id="157336",
        tmdb_id=157336,
        score=0.95,
    )
    decision = MatchDecision.model_construct(
        match=invalid_match,
        media_info=SimpleNamespace(title="星际穿越"),
        reason_code="MATCHED",
    )

    outcome = MoviePilotGateway().submit(decision, enable_subscription=True)

    assert outcome.status is RequestStatus.FAILED
    assert load_calls == []


def test_submit_only_treats_requested_tv_season_as_present_in_library(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway
    state.in_library = SimpleNamespace(seasons={1: [1, 2, 3]})
    decision = matched_decision()
    decision.match = decision.match.model_copy(
        update={"media_type": "tv", "season": 2}
    )

    outcome = gateway.submit(decision, enable_subscription=True)

    assert outcome.status is RequestStatus.SUBSCRIBED
    assert len(state.subscribe_exists_calls) == 1
    assert state.subscribe_exists_calls[0][1].begin_season == 2
    assert state.add_calls[0]["season"] == 2


def test_submit_stops_when_requested_tv_season_is_in_library_collection(
    subscription_gateway: tuple[MoviePilotGateway, SimpleNamespace],
) -> None:
    gateway, state = subscription_gateway
    state.in_library = SimpleNamespace(seasons={2: []})
    decision = matched_decision()
    decision.match = decision.match.model_copy(
        update={"media_type": "tv", "season": 2}
    )

    outcome = gateway.submit(decision, enable_subscription=True)

    assert outcome.status is RequestStatus.ALREADY_IN_LIBRARY
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
            "season": None,
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

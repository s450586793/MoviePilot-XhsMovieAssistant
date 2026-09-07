from contextlib import contextmanager
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

import pytest

from xhsmovieassistant.browser import BrowserManager, OperationResult
from xhsmovieassistant.request_builder import build_media_request
from xhsmovieassistant.xhs import (
    NoteDetail,
    ReplyOutcome,
    XhsContractError,
    XhsGateway,
    XhsPausedError,
)
from xhsmovieassistant.xhs_contracts import TransientMention


MENTIONS_API_URL = (
    "https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20&cursor="
)
REPLY_API_URL = "https://edith.xiaohongshu.com/api/sns/web/v1/comment/post"


def mention_payload(count: int = 1) -> dict[str, object]:
    return {
        "success": True,
        "code": 0,
        "data": {
            "message_list": [
                {
                    "id": f"mention-{index}",
                    "type": "mention/comment",
                    "time": 1_700_000_000 + index,
                    "user_info": {"userid": f"user-{index}"},
                    "comment_info": {
                        "id": f"comment-{index}",
                        "content": "请订阅《星际穿越》",
                    },
                    "item_info": {
                        "id": f"note-{index}",
                        "xsec_token": f"transient-token-{index}",
                    },
                }
                for index in range(count)
            ]
        },
    }


def make_mention(**overrides: object) -> TransientMention:
    values: dict[str, object] = {
        "mention_id": "mention-1",
        "sender_user_id": "sender-1",
        "comment_id": "target-1",
        "comment_text": "请订阅《星际穿越》",
        "note_id": "note /一",
        "xsec_token": "token +/secret",
    }
    values.update(overrides)
    return TransientMention(**values)


def note_state() -> dict[str, object]:
    return {
        "note /一": {
            "_value": {
                "note": {
                    "_value": {
                        "noteId": "note /一",
                        "title": "星际穿越",
                        "desc": "诺兰科幻电影",
                        "type": "video",
                        "time": 1_700_000_001,
                        "user": {"nickname": "电影作者"},
                        "comments": [
                            {"content": "第一条评论"},
                            {"content": "第二条评论"},
                        ],
                    }
                }
            }
        }
    }


def _fake_unwrap(value: object) -> Mapping[str, object] | None:
    seen: set[int] = set()
    while isinstance(value, Mapping) and id(value) not in seen:
        seen.add(id(value))
        wrapped = value.get("_value")
        if not isinstance(wrapped, Mapping):
            return value
        value = wrapped
    return None


def _fake_note_record(value: object) -> Mapping[str, object] | None:
    entry = _fake_unwrap(value)
    if entry is None:
        return None
    note = _fake_unwrap(entry.get("note", entry))
    if note is None:
        return None
    return note if _fake_note_has_data(note) else None


def _fake_note_id(note: Mapping[str, object]) -> str:
    return str(note.get("noteId") or note.get("note_id") or note.get("id") or "")


def _fake_note_has_data(note: Mapping[str, object]) -> bool:
    if _fake_note_id(note):
        return True
    if any(str(note.get(field) or "").strip() for field in ("title", "desc", "type")):
        return True
    if isinstance(note.get("user"), Mapping) and bool(note["user"]):
        return True
    time = note.get("time")
    if isinstance(time, (int, float)) and not isinstance(time, bool):
        return True
    return isinstance(note.get("comments"), list) or isinstance(
        note.get("imageList"), list
    )


def _fake_note_state_ready(value: object, target_id: str) -> bool:
    root = _fake_unwrap(value)
    if root is None:
        return False
    matches = []
    for key, raw in root.items():
        note = _fake_note_record(raw)
        if note is None:
            continue
        declared = _fake_note_id(note)
        if (key == target_id and not declared) or declared == target_id:
            matches.append(note)
    return len(matches) == 1


class FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        status: int = 200,
        payload: object = None,
        json_error: Exception | None = None,
    ) -> None:
        self.url = url
        self.status = status
        self.payload = payload
        self.json_error = json_error

    def json(self) -> object:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


def successful_reply_response(reply_id: str = "reply-123") -> FakeResponse:
    return FakeResponse(
        REPLY_API_URL,
        status=200,
        payload={
            "success": True,
            "code": 0,
            "data": {"comment": {"id": reply_id}},
        },
    )


class FakeMouse:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def wheel(self, delta_x: int, delta_y: int) -> None:
        assert delta_x == 0
        assert 0 < delta_y <= 2_000
        self.page.scroll_count += 1


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def insert_text(self, text: str) -> None:
        if self.page.keyboard_error is not None:
            raise self.page.keyboard_error
        self.page.inserted_text = text
        self.page.input_text = text


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str, *, kind: str = "root") -> None:
        self.page = page
        self.selector = selector
        self.kind = kind

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        if self.selector.startswith("#comment-"):
            comment_id = self.selector.removeprefix("#comment-")
            threshold = self.page.comment_ids.get(comment_id)
            return int(threshold is not None and self.page.scroll_count >= threshold)
        if self.selector == ".el-message--success:visible, [data-reply-success]:visible":
            return int(self.page.success_visible)
        return int(self.selector in self.page.present_selectors)

    def locator(self, selector: str) -> "FakeLocator":
        assert self.selector.startswith("#comment-")
        return FakeLocator(self.page, selector, kind="reply")

    def inner_text(self, **kwargs: object) -> str:
        if self.selector == "body":
            return self.page.body_text
        return self.page.dom_text.get(self.selector, "")

    def all_inner_texts(self) -> list[str]:
        return list(self.page.dom_text_lists.get(self.selector, []))

    def fill(self, text: str, **kwargs: object) -> None:
        if self.page.fill_error is not None:
            raise self.page.fill_error
        self.page.filled_text = text
        self.page.input_text = text

    def text_content(self, **kwargs: object) -> str:
        if self.selector == "div.input-box div.content-edit p.content-input":
            return self.page.input_text
        return ""

    def click(self, **kwargs: object) -> None:
        if self.kind == "reply":
            self.page.reply_clicks += 1
            return
        if self.selector == 'a[href="/notification"], a[href^="/notification?"]':
            self.page.actions.append("click_notification")
            origin = urlsplit(self.page.url)
            self.page.url = f"{origin.scheme}://{origin.netloc}/notification"
            self.page.emit_responses()
            return
        if self.selector == "div.bottom button.submit":
            self.page.submit_clicks += 1
            if self.page.submit_error is not None:
                raise self.page.submit_error
            if self.page.post_submit_risk_code is not None:
                self.page.active_risk_code = self.page.post_submit_risk_code
            if self.page.reply_clears_on_click:
                self.page.input_text = ""
            if self.page.submit_response_status is not None:
                response = FakeResponse(
                    REPLY_API_URL,
                    status=self.page.submit_response_status,
                    payload=self.page.submit_response_payload,
                )
                for callback in tuple(self.page.listeners):
                    callback(response)

    def is_enabled(self, **kwargs: object) -> bool:
        return self.page.submit_enabled

    def is_visible(self, **kwargs: object) -> bool:
        return any(
            selector.strip().removesuffix(":visible")
            in self.page.visible_selectors
            for selector in self.selector.split(",")
        )


class FakePage:
    def __init__(self) -> None:
        self.url = "https://www.xiaohongshu.com/notification"
        self.actions: list[str] = []
        self.responses: list[FakeResponse] = []
        self.delayed_responses: list[tuple[int, FakeResponse]] = []
        self.event_pumps = 0
        self.listeners: list[object] = []
        self.goto_status = 200
        self.reload_status = 200
        self.goto_url = ""
        self.wait_timeouts: list[int] = []
        self.initial_state: object = note_state()
        self.note_state_sequence: list[object] = []
        self.note_state_samples = 0
        self.initial_state_timeout = False
        self.wait_for_function_timeout: int | None = None
        self.wait_for_function_arg: object = None
        self.body_text = ""
        self.visible_selectors: set[str] = set()
        self.dom_text: dict[str, str] = {}
        self.dom_text_lists: dict[str, list[str]] = {}
        self.present_selectors: set[str] = {
            "div.input-box div.content-edit p.content-input",
            "div.bottom button.submit",
        }
        self.comment_ids: dict[str, int] = {"target-1": 0}
        self.comment_disabled = False
        self.scroll_count = 0
        self.reply_clicks = 0
        self.submit_clicks = 0
        self.submit_enabled = True
        self.fill_error: Exception | None = None
        self.keyboard_error: Exception | None = None
        self.submit_error: Exception | None = None
        self.submit_response_status: int | None = None
        self.submit_response_payload: object = None
        self.post_submit_risk_code: str | None = None
        self.active_risk_code: str | None = None
        self.reply_success_after_pumps: int | None = 1
        self.reply_clears_on_click = False
        self.post_submit_pumps = 0
        self.success_visible = False
        self.input_text = ""
        self.filled_text: str | None = None
        self.inserted_text: str | None = None
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)

    def on(self, event: str, callback: object) -> None:
        assert event == "response"
        self.actions.append("register_response")
        self.listeners.append(callback)

    def remove_listener(self, event: str, callback: object) -> None:
        assert event == "response"
        self.actions.append("remove_response")
        self.listeners.remove(callback)

    def goto(self, url: str, **kwargs: object) -> FakeResponse:
        self.goto_url = url
        self.url = url
        return FakeResponse(url, status=self.goto_status)

    def reload(self, **kwargs: object) -> FakeResponse:
        self.actions.append("reload")
        self.emit_responses()
        return FakeResponse(self.url, status=self.reload_status)

    def emit_responses(self) -> None:
        for response in self.responses:
            for callback in tuple(self.listeners):
                callback(response)

    def wait_for_timeout(self, timeout: int) -> None:
        self.wait_timeouts.append(timeout)
        self.event_pumps += 1
        due = [
            response
            for pump, response in self.delayed_responses
            if pump == self.event_pumps
        ]
        for response in due:
            for callback in tuple(self.listeners):
                callback(response)
        if self.submit_clicks:
            self.post_submit_pumps += 1
            if (
                self.reply_success_after_pumps is not None
                and self.post_submit_pumps >= self.reply_success_after_pumps
            ):
                self.input_text = ""

    def wait_for_function(self, expression: str, **kwargs: object) -> None:
        assert "noteDetailMap" in expression
        self.wait_for_function_timeout = int(kwargs["timeout"])
        self.wait_for_function_arg = kwargs.get("arg")
        if self.initial_state_timeout:
            raise TimeoutError("initial state timed out")
        target_id = str(self.wait_for_function_arg or "")
        snapshots = self.note_state_sequence or [self.initial_state]
        for state in snapshots:
            self.note_state_samples += 1
            self.initial_state = state
            if _fake_note_state_ready(state, target_id):
                return
        raise TimeoutError("requested note did not become ready")

    def evaluate(self, expression: str) -> object:
        if "commentDisabled" in expression:
            return self.comment_disabled
        if "noteDetailMap" in expression:
            return self.initial_state
        raise AssertionError(f"unexpected evaluate expression: {expression}")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


class FakeManager:
    def __init__(self, page: FakePage, base_url: str = "https://www.xiaohongshu.com") -> None:
        self.page = page
        self.base_url = base_url
        self.risk_results: dict[int | None, OperationResult] = {}
        self.detected_statuses: list[int | None] = []
        self.session_count = 0

    @contextmanager
    def session(self):
        self.session_count += 1
        yield self.page

    def detect_risk(
        self, page: FakePage, response_status: int | None = None
    ) -> OperationResult:
        assert page is self.page
        self.detected_statuses.append(response_status)
        if response_status is None and page.active_risk_code is not None:
            return OperationResult(
                success=False,
                code=page.active_risk_code,
                message="post-submit risk",
                should_pause=True,
            )
        return self.risk_results.get(response_status, OperationResult(success=True))

    def detect_login_page(self, page: FakePage) -> OperationResult:
        assert page is self.page
        return OperationResult(success=True)


@pytest.fixture
def fake_page() -> FakePage:
    return FakePage()


@pytest.fixture
def manager(fake_page: FakePage) -> FakeManager:
    return FakeManager(fake_page)


@pytest.fixture
def gateway(manager: FakeManager) -> XhsGateway:
    return XhsGateway(manager, replies_enabled=True)


def _gateway_with_real_page_classification(
    tmp_path, monkeypatch, page: FakePage
) -> XhsGateway:
    manager = BrowserManager(tmp_path, "xiaohongshu", None, lambda: None)

    @contextmanager
    def session():
        yield page

    monkeypatch.setattr(manager, "session", session)
    return XhsGateway(manager, replies_enabled=True)


def test_fetch_mentions_registers_listener_before_reload_and_removes_it(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.responses = [
        FakeResponse("https://example.test/api/sns/web/v1/you/mentions-extra", payload=[]),
        FakeResponse(MENTIONS_API_URL, payload=mention_payload()),
    ]

    mentions = gateway.fetch_mentions(limit=20)

    assert fake_page.actions == ["register_response", "reload", "remove_response"]
    assert mentions[0].xsec_token == "transient-token-0"
    assert fake_page.listeners == []


def test_fetch_mentions_uses_rednote_in_app_notification_navigation(
    fake_page: FakePage,
) -> None:
    fake_page.url = "https://www.rednote.com/notification"
    fake_page.responses = [FakeResponse(MENTIONS_API_URL, payload=mention_payload())]
    gateway = XhsGateway(
        FakeManager(fake_page, base_url="https://www.rednote.com"),
        replies_enabled=True,
    )

    mentions = gateway.fetch_mentions()

    assert mentions[0].mention_id == "mention-0"
    assert fake_page.goto_url == "https://www.rednote.com"
    assert fake_page.actions == [
        "register_response",
        "click_notification",
        "remove_response",
    ]
    assert fake_page.listeners == []


def test_fetch_mentions_ignores_non_target_response_and_times_out_boundedly(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.responses = [
        FakeResponse(
            "https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions/next",
            payload=mention_payload(),
        )
    ]

    with pytest.raises(XhsContractError, match="not observed"):
        gateway.fetch_mentions()

    assert sum(fake_page.wait_timeouts) <= 20_000
    assert max(fake_page.wait_timeouts) <= 100
    assert fake_page.listeners == []


def test_fetch_mentions_returns_after_a_delayed_response_without_full_timeout(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.delayed_responses = [
        (2, FakeResponse(MENTIONS_API_URL, payload=mention_payload()))
    ]

    mentions = gateway.fetch_mentions()

    assert mentions[0].mention_id == "mention-0"
    assert fake_page.wait_timeouts == [100, 100]
    assert sum(fake_page.wait_timeouts) < 20_000
    assert fake_page.listeners == []


@pytest.mark.parametrize(
    "payload",
    [[], {"data": None}, {"data": {"message_list": "not-a-list"}}],
)
def test_fetch_mentions_rejects_malformed_mapping_and_removes_listener(
    gateway: XhsGateway, fake_page: FakePage, payload: object
) -> None:
    fake_page.responses = [FakeResponse(MENTIONS_API_URL, payload=payload)]

    with pytest.raises(XhsContractError, match="malformed"):
        gateway.fetch_mentions()

    assert fake_page.listeners == []


def test_fetch_mentions_converts_invalid_json_to_contract_error(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.responses = [
        FakeResponse(MENTIONS_API_URL, json_error=ValueError("secret upstream body"))
    ]

    with pytest.raises(XhsContractError, match="malformed") as error:
        gateway.fetch_mentions()

    assert "secret upstream body" not in str(error.value)
    assert fake_page.listeners == []


@pytest.mark.parametrize("limit", [0, -1, 21, True, 1.5])
def test_fetch_mentions_enforces_limit_before_opening_browser(
    gateway: XhsGateway, manager: FakeManager, limit: object
) -> None:
    with pytest.raises(ValueError, match="between 1 and 20"):
        gateway.fetch_mentions(limit=limit)

    assert manager.session_count == 0


def test_fetch_mentions_applies_limit_to_first_page_only(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.responses = [FakeResponse(MENTIONS_API_URL, payload=mention_payload(3))]

    mentions = gateway.fetch_mentions(limit=2)

    assert [mention.mention_id for mention in mentions] == ["mention-0", "mention-1"]
    assert fake_page.actions.count("reload") == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "AUTH_REQUIRED"), (429, "RATE_LIMITED")],
)
def test_fetch_mentions_pauses_on_http_risk_with_stable_code(
    gateway: XhsGateway,
    manager: FakeManager,
    fake_page: FakePage,
    status: int,
    code: str,
) -> None:
    fake_page.reload_status = status
    fake_page.responses = [FakeResponse(MENTIONS_API_URL, payload=mention_payload())]
    manager.risk_results[status] = OperationResult(
        success=False, code=code, message="pause", should_pause=True
    )

    with pytest.raises(XhsPausedError) as error:
        gateway.fetch_mentions()

    assert error.value.code == code
    assert manager.detected_statuses[-1] == status
    assert fake_page.listeners == []


def test_fetch_mentions_pauses_on_login_page_without_dom_fallback(
    gateway: XhsGateway, manager: FakeManager, fake_page: FakePage
) -> None:
    fake_page.responses = []
    manager.risk_results[200] = OperationResult(
        success=False,
        code="SESSION_EXPIRED",
        message="Login required",
        should_pause=True,
    )

    with pytest.raises(XhsPausedError) as error:
        gateway.fetch_mentions()

    assert error.value.code == "SESSION_EXPIRED"
    assert fake_page.listeners == []


def test_fetch_mentions_uses_real_visible_login_classification(
    tmp_path, monkeypatch, fake_page: FakePage
) -> None:
    fake_page.visible_selectors.add(".login-container")
    fake_page.responses = [FakeResponse(MENTIONS_API_URL, payload=mention_payload())]
    gateway = _gateway_with_real_page_classification(tmp_path, monkeypatch, fake_page)

    with pytest.raises(XhsPausedError) as error:
        gateway.fetch_mentions()

    assert error.value.code == "LOGIN_REQUIRED"
    assert fake_page.listeners == []


def test_fetch_mentions_treats_non_pause_browser_failure_as_contract_error(
    gateway: XhsGateway, manager: FakeManager
) -> None:
    manager.risk_results[200] = OperationResult(
        success=False,
        code="TEMPORARY_FAILURE",
        message="DOM was temporarily unavailable",
        should_pause=False,
    )

    with pytest.raises(XhsContractError, match="risk state"):
        gateway.fetch_mentions()


def test_fetch_note_unwraps_vue_state_and_extracts_fields(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    detail = gateway.fetch_note(make_mention())

    assert isinstance(detail, NoteDetail)
    assert detail.title == "星际穿越"
    assert detail.content == "诺兰科幻电影"
    assert detail.type == "video"
    assert detail.author == "电影作者"
    assert detail.published_at == 1_700_000_001
    assert detail.comments == ("第一条评论", "第二条评论")
    assert fake_page.wait_for_function_timeout == 15_000


def test_fetch_note_recursively_unwraps_root_vue_value(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {"_value": {"_value": note_state()}}

    detail = gateway.fetch_note(make_mention())

    assert detail.title == "星际穿越"
    assert detail.note_id == "note /一"


def test_fetch_note_waits_for_the_requested_note_after_an_empty_map(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {"note /一": {}}
    fake_page.note_state_sequence = [
        {"note /一": {}},
        {
            "note /一": {
                "note": {"noteId": "note /一", "title": "延迟就绪的笔记"}
            }
        },
    ]

    detail = gateway.fetch_note(make_mention())

    assert detail.title == "延迟就绪的笔记"
    assert fake_page.wait_for_function_arg == "note /一"
    assert fake_page.note_state_samples == 2


@pytest.mark.parametrize("placeholder", [{}, {"note": {"title": "", "desc": None}}])
def test_fetch_note_rejects_an_exact_key_with_only_placeholder_data(
    gateway: XhsGateway, fake_page: FakePage, placeholder: object
) -> None:
    fake_page.initial_state = {"note /一": placeholder}

    with pytest.raises(XhsContractError) as error:
        gateway.fetch_note(make_mention())

    assert "token +/secret" not in str(error.value)


def test_fetch_note_rejects_a_misleading_unicode_prefix_without_matching_id(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {
        "note /一_other": {
            "note": {"id": "different-note", "title": "前缀误导笔记"}
        }
    }

    with pytest.raises(XhsContractError) as error:
        gateway.fetch_note(make_mention())

    assert "token +/secret" not in str(error.value)


def test_fetch_note_selects_only_the_matching_note_from_multiple_entries(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {
        "other-note": {"note": {"noteId": "other-note", "title": "错误笔记"}},
        "note /一:cache": {
            "_value": {
                "note": {
                    "_value": {
                        "noteId": "note /一",
                        "title": "目标笔记",
                        "desc": "目标正文",
                    }
                }
            }
        },
    }

    detail = gateway.fetch_note(make_mention())

    assert detail.title == "目标笔记"
    assert detail.content == "目标正文"


def test_fetch_note_rejects_state_without_the_requested_note(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {
        "other-note": {"note": {"noteId": "other-note", "title": "错误笔记"}}
    }

    with pytest.raises(XhsContractError, match="note detail state") as error:
        gateway.fetch_note(make_mention())

    assert "token +/secret" not in str(error.value)


def test_fetch_note_rejects_ambiguous_matching_entries(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {
        "note /一:first": {"note": {"noteId": "note /一", "title": "版本一"}},
        "note /一:second": {"note": {"noteId": "note /一", "title": "版本二"}},
    }

    with pytest.raises(XhsContractError, match="note detail state") as error:
        gateway.fetch_note(make_mention())

    assert "token +/secret" not in str(error.value)


def test_fetch_note_rejects_exact_and_declared_duplicate_as_ambiguous(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {
        "note /一": {"note": {"noteId": "note /一", "title": "精确键版本"}},
        "unrelated-cache-key": {
            "note": {"id": "note /一", "title": "内部 ID 版本"}
        },
    }

    with pytest.raises(XhsContractError) as error:
        gateway.fetch_note(make_mention())

    assert "token +/secret" not in str(error.value)


@pytest.mark.parametrize(
    ("base_url", "expected_path"),
    [
        ("https://www.xiaohongshu.com", "/explore/note%20%2F%E4%B8%80"),
        ("https://www.rednote.com", "/discovery/item/note%20%2F%E4%B8%80"),
    ],
)
def test_fetch_note_uses_encoded_transient_navigation_and_canonical_url(
    fake_page: FakePage, base_url: str, expected_path: str
) -> None:
    gateway = XhsGateway(FakeManager(fake_page, base_url), replies_enabled=True)

    detail = gateway.fetch_note(make_mention())

    navigation = urlsplit(fake_page.goto_url)
    assert navigation.path == expected_path
    assert parse_qs(navigation.query) == {"xsec_token": ["token +/secret"]}
    assert detail.url == f"{base_url}{expected_path}"
    assert urlsplit(detail.url).query == ""
    assert urlsplit(detail.url).fragment == ""
    assert "token +/secret" not in repr(detail)
    assert "xsec" not in repr(detail).lower()


def test_fetch_note_passes_navigation_status_to_risk_detection(
    gateway: XhsGateway, manager: FakeManager, fake_page: FakePage
) -> None:
    fake_page.goto_status = 429
    manager.risk_results[429] = OperationResult(
        success=False,
        code="RATE_LIMITED",
        message="limited",
        should_pause=True,
    )

    with pytest.raises(XhsPausedError) as error:
        gateway.fetch_note(make_mention())

    assert error.value.code == "RATE_LIMITED"
    assert manager.detected_statuses == [429]


def test_fetch_note_uses_real_visible_login_classification(
    tmp_path, monkeypatch, fake_page: FakePage
) -> None:
    fake_page.visible_selectors.add(".login-container")
    gateway = _gateway_with_real_page_classification(tmp_path, monkeypatch, fake_page)

    with pytest.raises(XhsPausedError) as error:
        gateway.fetch_note(make_mention())

    assert error.value.code == "LOGIN_REQUIRED"
    assert fake_page.wait_for_function_timeout is None


def test_fetch_note_uses_dom_only_for_content_fallback(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state = {"note /一": {"note": {"noteId": "note /一"}}}
    fake_page.dom_text = {
        "#detail-title, .title": "DOM 标题",
        "#detail-desc, .desc": "DOM 正文",
        ".author-wrapper .name, .user-info .name": "伪造发送者",
    }
    fake_page.dom_text_lists = {
        ".comment-item .content, .parent-comment .content": ["可见评论"]
    }

    detail = gateway.fetch_note(make_mention(sender_user_id="authorized-user"))

    assert detail.title == "DOM 标题"
    assert detail.content == "DOM 正文"
    assert detail.author == ""
    assert detail.comments == ("可见评论",)
    assert not hasattr(detail, "sender_user_id")


@pytest.mark.parametrize("comment_source", ["state", "dom"])
def test_fetch_note_preserves_all_loaded_comments_until_relevance_ranking(
    gateway: XhsGateway,
    fake_page: FakePage,
    comment_source: str,
) -> None:
    comments = [f"普通评论 {index}" for index in range(12)] + ["《星际穿越》 2014"]
    note_comments = (
        [{"content": comment} for comment in comments]
        if comment_source == "state"
        else []
    )
    fake_page.initial_state = {
        "note /一": {
            "note": {
                "noteId": "note /一",
                "title": "影视笔记",
                "comments": note_comments,
            }
        }
    }
    if comment_source == "dom":
        fake_page.dom_text_lists[
            ".comment-item .content, .parent-comment .content"
        ] = comments

    detail = gateway.fetch_note(make_mention())
    request = build_media_request(make_mention(), detail)

    assert request.note.relevant_comments[0] == "《星际穿越》 2014"
    assert len(request.note.relevant_comments) == 10


def test_fetch_note_timeout_is_bounded_and_does_not_leak_token(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.initial_state_timeout = True

    with pytest.raises(XhsContractError, match="timed out") as error:
        gateway.fetch_note(make_mention())

    assert fake_page.wait_for_function_timeout == 15_000
    assert "token +/secret" not in str(error.value)


def test_reply_is_disabled_by_default_without_opening_browser(
    manager: FakeManager,
) -> None:
    outcome = XhsGateway(manager).reply_to_comment(make_mention(), "固定模板回复")

    assert outcome == ReplyOutcome(
        success=False, code="REPLIES_DISABLED", message="Comment replies are disabled"
    )
    assert manager.session_count == 0


def test_reply_uses_real_visible_login_classification_without_submitting(
    tmp_path, monkeypatch, fake_page: FakePage
) -> None:
    fake_page.visible_selectors.add(".login-container")
    gateway = _gateway_with_real_page_classification(tmp_path, monkeypatch, fake_page)

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "LOGIN_REQUIRED"
    assert fake_page.submit_clicks == 0


def test_reply_fails_after_ten_bounded_scrolls_when_exact_comment_is_missing(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.comment_ids = {"parent-1": 0}

    outcome = gateway.reply_to_comment(
        make_mention(parent_comment_id="parent-1"), "固定模板回复"
    )

    assert outcome.success is False
    assert outcome.code == "COMMENT_NOT_FOUND"
    assert fake_page.scroll_count == 10
    assert fake_page.reply_clicks == 0
    assert fake_page.submit_clicks == 0


def test_reply_finds_exact_comment_after_bounded_scrolling(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.comment_ids = {"target-1": 3}
    fake_page.submit_response_status = 200
    fake_page.submit_response_payload = successful_reply_response().payload

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is True
    assert outcome.reply_id == "reply-123"
    assert fake_page.scroll_count == 3
    assert fake_page.reply_clicks == 1
    assert fake_page.submit_clicks == 1


def test_reply_reports_success_only_after_non_risk_response_with_real_id(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.reply_success_after_pumps = None
    fake_page.delayed_responses = [(2, successful_reply_response("reply-456"))]

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is True
    assert outcome.reply_id == "reply-456"
    assert fake_page.event_pumps == 2
    assert fake_page.submit_clicks == 1


@pytest.mark.parametrize(
    ("pump", "status", "code"),
    [(1, 403, "AUTH_REQUIRED"), (3, 429, "RATE_LIMITED")],
)
def test_reply_prefers_delayed_http_risk_over_synchronous_input_clear(
    gateway: XhsGateway,
    manager: FakeManager,
    fake_page: FakePage,
    pump: int,
    status: int,
    code: str,
) -> None:
    fake_page.reply_clears_on_click = True
    fake_page.reply_success_after_pumps = None
    fake_page.delayed_responses = [
        (
            pump,
            FakeResponse(
                "https://edith.xiaohongshu.com/api/sns/web/v1/comment/post",
                status=status,
            ),
        )
    ]
    manager.risk_results[status] = OperationResult(
        success=False, code=code, message="delayed risk", should_pause=True
    )

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == code
    assert fake_page.event_pumps >= pump
    assert fake_page.submit_clicks == 1


def test_reply_does_not_accept_input_clear_without_success_response(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.reply_clears_on_click = True
    fake_page.reply_success_after_pumps = None

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "SUBMIT_UNCONFIRMED"
    assert sum(fake_page.wait_timeouts) == 3_000
    assert fake_page.submit_clicks == 1


def test_reply_returns_failure_when_click_triggers_captcha(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.post_submit_risk_code = "XHS_RISK_CONTROL"

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "XHS_RISK_CONTROL"
    assert fake_page.submit_clicks == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "AUTH_REQUIRED"), (429, "RATE_LIMITED")],
)
def test_reply_returns_failure_when_click_response_is_http_risk(
    gateway: XhsGateway,
    fake_page: FakePage,
    status: int,
    code: str,
) -> None:
    fake_page.submit_response_status = status

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == code
    assert fake_page.submit_clicks == 1


def test_reply_returns_uncertain_failure_when_no_success_signal_arrives(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.reply_success_after_pumps = None

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "SUBMIT_UNCONFIRMED"
    assert sum(fake_page.wait_timeouts) <= 3_000
    assert fake_page.submit_clicks == 1


def test_reply_returns_failure_for_captcha_without_clicking_submit(
    gateway: XhsGateway, manager: FakeManager, fake_page: FakePage
) -> None:
    manager.risk_results[200] = OperationResult(
        success=False,
        code="XHS_RISK_CONTROL",
        message="captcha",
        should_pause=True,
    )

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "XHS_RISK_CONTROL"
    assert fake_page.submit_clicks == 0


def test_reply_returns_failure_when_note_comments_are_disabled(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.comment_disabled = True

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "COMMENTS_DISABLED"
    assert fake_page.submit_clicks == 0


def test_reply_falls_back_to_keyboard_insert_and_submits_exactly_once(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.fill_error = RuntimeError("contenteditable fill unsupported")
    fake_page.submit_response_status = 200
    fake_page.submit_response_payload = successful_reply_response().payload

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is True
    assert outcome.reply_id == "reply-123"
    assert fake_page.filled_text is None
    assert fake_page.inserted_text == "固定模板回复"
    assert fake_page.submit_clicks == 1


def test_reply_checks_once_more_after_the_tenth_scroll(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.comment_ids = {"target-1": 10}
    fake_page.submit_response_status = 200
    fake_page.submit_response_payload = successful_reply_response(
        "reply-final"
    ).payload

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is True
    assert outcome.reply_id == "reply-final"
    assert fake_page.scroll_count == 10
    assert fake_page.submit_clicks == 1


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(
            REPLY_API_URL,
            status=200,
            payload={"success": True, "code": 0, "data": {}},
        ),
        FakeResponse(
            REPLY_API_URL,
            status=200,
            payload={
                "success": False,
                "code": 1,
                "data": {"comment": {"id": "reply-untrusted"}},
            },
        ),
        FakeResponse(
            REPLY_API_URL,
            status=500,
            payload={
                "success": True,
                "code": 0,
                "data": {"comment": {"id": "reply-untrusted"}},
            },
        ),
    ],
)
def test_reply_rejects_response_without_complete_non_risk_success_evidence(
    gateway: XhsGateway,
    fake_page: FakePage,
    response: FakeResponse,
) -> None:
    fake_page.reply_success_after_pumps = None
    fake_page.delayed_responses = [(1, response)]

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.reply_id is None
    assert fake_page.submit_clicks == 1


def test_reply_does_not_click_disabled_submit(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.submit_enabled = False

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "SUBMIT_DISABLED"
    assert fake_page.submit_clicks == 0


def test_reply_never_retries_submit_after_click_failure(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.submit_error = RuntimeError("uncertain upstream result")

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "SUBMIT_FAILED"
    assert fake_page.submit_clicks == 1


def test_reply_converts_timeout_to_failure_without_retrying_submit(
    gateway: XhsGateway, fake_page: FakePage
) -> None:
    fake_page.fill_error = TimeoutError("fill timed out")
    fake_page.keyboard_error = TimeoutError("insert timed out")

    outcome = gateway.reply_to_comment(make_mention(), "固定模板回复")

    assert outcome.success is False
    assert outcome.code == "TIMEOUT"
    assert fake_page.submit_clicks == 0

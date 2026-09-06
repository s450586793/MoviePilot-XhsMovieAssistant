"""Bounded Xiaohongshu browser operations for mentions, notes, and replies."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .xhs_contracts import TransientMention, parse_mentions_payload


_MENTIONS_PATH = "/api/sns/web/v1/you/mentions"
_MENTIONS_TIMEOUT_MS = 20_000
_NOTE_TIMEOUT_MS = 15_000
_REPLY_SCROLL_ROUNDS = 10
_REPLY_SCROLL_PIXELS = 900
_REPLY_WAIT_MS = 250
_REPLY_CONFIRM_TIMEOUT_MS = 3_000
_EVENT_PUMP_MS = 100
_PAUSE_CODES = {
    "AUTH_REQUIRED",
    "LOGIN_REQUIRED",
    "RATE_LIMITED",
    "SESSION_EXPIRED",
    "TEMPORARY_FAILURE",
    "XHS_RISK_CONTROL",
}


@dataclass(frozen=True)
class NoteDetail:
    """Sanitized note fields without transient navigation credentials."""

    note_id: str
    url: str
    type: str = "unknown"
    title: str = ""
    content: str = ""
    author: str = ""
    published_at: int | float | None = None
    comments: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplyOutcome:
    """Result of one non-retried comment reply attempt."""

    success: bool
    code: str | None = None
    message: str = ""


class XhsContractError(RuntimeError):
    """Raised when an expected Xiaohongshu browser contract is unavailable."""


class XhsPausedError(RuntimeError):
    """Raised when browser work must pause for login or risk control."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _PAUSE_CODES else "TEMPORARY_FAILURE"
        super().__init__(f"Xiaohongshu browser operation paused: {self.code}")


class XhsGateway:
    """Perform narrowly scoped XHS operations through a ``BrowserManager``."""

    def __init__(self, manager: Any, *, replies_enabled: bool = False) -> None:
        self._manager = manager
        self._replies_enabled = replies_enabled

    def fetch_mentions(self, limit: int = 20) -> tuple[TransientMention, ...]:
        """Capture and parse the first mentions response after one page reload."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")

        captured: dict[str, object] = {}

        def on_response(response: Any) -> None:
            if captured or not _is_mentions_response(response):
                return
            captured["status"] = _response_status(response)
            try:
                captured["payload"] = response.json()
            except Exception:
                captured["error"] = True

        try:
            with self._manager.session() as page:
                notification_url = f"{self._manager.base_url}/notification"
                navigation = page.goto(notification_url, wait_until="domcontentloaded")
                _ensure_risk_ok(self._manager, page, _response_status(navigation))
                page.on("response", on_response)
                try:
                    reload_response = page.reload(wait_until="domcontentloaded")
                    _ensure_risk_ok(
                        self._manager, page, _response_status(reload_response)
                    )
                    _wait_for_mentions_response(page, captured)
                    if not captured:
                        raise XhsContractError("mentions response was not observed")
                    _ensure_risk_ok(
                        self._manager, page, _optional_status(captured.get("status"))
                    )
                    if captured.get("error"):
                        raise XhsContractError("mentions response was malformed")
                    payload = captured.get("payload")
                    _validate_mentions_payload(payload)
                    return parse_mentions_payload(payload)[:limit]
                finally:
                    page.remove_listener("response", on_response)
        except (XhsContractError, XhsPausedError):
            raise
        except Exception as error:
            if _is_timeout(error):
                raise XhsContractError("mentions response was not observed") from None
            raise XhsContractError("mentions browser operation failed") from None

    def fetch_note(self, mention: TransientMention) -> NoteDetail:
        """Read one note from initial state using a transient navigation token."""
        navigation_url, canonical_url = _note_urls(self._manager.base_url, mention)
        try:
            with self._manager.session() as page:
                response = page.goto(navigation_url, wait_until="domcontentloaded")
                _ensure_risk_ok(self._manager, page, _response_status(response))
                page.wait_for_function(
                    """(noteId) => {
                        const unwrap = (value) => {
                            const seen = new Set();
                            while (
                                value && typeof value === 'object' &&
                                value._value && typeof value._value === 'object' &&
                                !seen.has(value)
                            ) {
                                seen.add(value);
                                value = value._value;
                            }
                            return value;
                        };
                        const root = unwrap(
                            window.__INITIAL_STATE__?.note?.noteDetailMap
                        );
                        if (!root || typeof root !== 'object' || Array.isArray(root)) {
                            return false;
                        }
                        const entries = Object.entries(root);
                        const read = ([key, raw]) => {
                            const entry = unwrap(raw);
                            const note = unwrap(entry?.note ?? entry);
                            if (!note || typeof note !== 'object') return null;
                            const declared = String(note.noteId ?? note.note_id ?? '');
                            return { key, declared };
                        };
                        const exact = entries.find(([key]) => key === noteId);
                        if (exact) {
                            const candidate = read(exact);
                            return Boolean(
                                candidate &&
                                (!candidate.declared || candidate.declared === noteId)
                            );
                        }
                        const candidates = entries.map(read).filter(Boolean);
                        const declared = candidates.filter(
                            (candidate) => candidate.declared === noteId
                        );
                        if (declared.length) return declared.length === 1;
                        const suffixes = candidates.filter(
                            (candidate) =>
                                !candidate.declared &&
                                candidate.key.startsWith(noteId) &&
                                ':_@.'.includes(candidate.key.charAt(noteId.length))
                        );
                        return suffixes.length === 1;
                    }""",
                    arg=mention.note_id,
                    timeout=_NOTE_TIMEOUT_MS,
                )
                raw_map = page.evaluate(
                    "() => window.__INITIAL_STATE__?.note?.noteDetailMap ?? null"
                )
                if not isinstance(raw_map, Mapping):
                    raise XhsContractError("note detail state was malformed")
                note = _note_from_map(raw_map, mention.note_id)
                return _build_note_detail(
                    page,
                    note_id=mention.note_id,
                    canonical_url=canonical_url,
                    note=note,
                )
        except (XhsContractError, XhsPausedError):
            raise
        except Exception as error:
            if _is_timeout(error):
                raise XhsContractError("note detail state timed out") from None
            raise XhsContractError("note browser operation failed") from None

    def reply_to_comment(self, mention: TransientMention, text: str) -> ReplyOutcome:
        """Attempt one reply to the exact source comment without submit retries."""
        if not self._replies_enabled:
            return ReplyOutcome(
                success=False,
                code="REPLIES_DISABLED",
                message="Comment replies are disabled",
            )

        navigation_url, _ = _note_urls(self._manager.base_url, mention)
        try:
            with self._manager.session() as page:
                response = page.goto(navigation_url, wait_until="domcontentloaded")
                risk = _risk_outcome(
                    self._manager, page, _response_status(response)
                )
                if risk is not None:
                    return risk
                if _comments_disabled(page):
                    return _reply_failure(
                        "COMMENTS_DISABLED", "Note comments are disabled"
                    )

                comment = _find_comment(page, mention.comment_id)
                if comment is None:
                    return _reply_failure(
                        "COMMENT_NOT_FOUND", "The source comment was not found"
                    )

                comment.locator(
                    ".right .interactions .reply, .reply-btn"
                ).first.click(timeout=2_000)
                input_locator = page.locator(
                    "div.input-box div.content-edit p.content-input"
                ).first
                try:
                    input_locator.fill(text, timeout=2_000)
                except Exception:
                    input_locator.click(timeout=2_000)
                    page.keyboard.insert_text(text)

                risk = _risk_outcome(self._manager, page, None)
                if risk is not None:
                    return risk

                submit = page.locator("div.bottom button.submit").first
                if submit.count() == 0:
                    return _reply_failure(
                        "SUBMIT_NOT_FOUND", "The reply submit control was not found"
                    )
                if not submit.is_enabled(timeout=2_000):
                    return _reply_failure(
                        "SUBMIT_DISABLED", "The reply submit control is disabled"
                    )
                submit_statuses: list[int] = []

                def on_submit_response(response: Any) -> None:
                    status = _response_status(response)
                    if status in {403, 429}:
                        submit_statuses.append(status)

                page.on("response", on_submit_response)
                try:
                    try:
                        submit.click(timeout=2_000)
                    except Exception:
                        return _reply_failure(
                            "SUBMIT_FAILED", "The reply submission result is uncertain"
                        )
                    return _confirm_reply_submission(
                        self._manager, page, input_locator, submit_statuses
                    )
                finally:
                    page.remove_listener("response", on_submit_response)
        except Exception as error:
            if _is_timeout(error):
                return _reply_failure("TIMEOUT", "The reply operation timed out")
            return _reply_failure("REPLY_FAILED", "The reply operation failed")


def _is_mentions_response(response: Any) -> bool:
    try:
        return urlsplit(str(response.url)).path == _MENTIONS_PATH
    except ValueError:
        return False


def _wait_for_mentions_response(page: Any, captured: Mapping[str, object]) -> None:
    deadline = monotonic() + (_MENTIONS_TIMEOUT_MS / 1_000)
    remaining_ms = _MENTIONS_TIMEOUT_MS
    while not captured and remaining_ms > 0 and monotonic() < deadline:
        wait_ms = min(_EVENT_PUMP_MS, remaining_ms)
        page.wait_for_timeout(wait_ms)
        remaining_ms -= wait_ms


def _validate_mentions_payload(payload: object) -> None:
    if not isinstance(payload, Mapping):
        raise XhsContractError("mentions response was malformed")
    pause_code = _payload_pause_code(payload)
    if pause_code is not None:
        raise XhsPausedError(pause_code)
    data = payload.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("message_list"), list):
        raise XhsContractError("mentions response was malformed")


def _payload_pause_code(payload: Mapping[object, object]) -> str | None:
    code = payload.get("code")
    if code == 300012:
        return "XHS_RISK_CONTROL"
    if code in {401, 403}:
        return "AUTH_REQUIRED"
    if code == 429:
        return "RATE_LIMITED"
    if payload.get("success") is False and code not in {0, None}:
        return "SESSION_EXPIRED"
    return None


def _note_urls(base_url: str, mention: TransientMention) -> tuple[str, str]:
    origin = urlsplit(base_url)
    host = (origin.hostname or "").lower()
    if origin.scheme != "https" or origin.port is not None:
        raise ValueError("unsupported Xiaohongshu site")
    if host == "www.xiaohongshu.com":
        prefix = "/explore/"
    elif host == "www.rednote.com":
        prefix = "/discovery/item/"
    else:
        raise ValueError("unsupported Xiaohongshu site")
    path = f"{prefix}{quote(mention.note_id, safe='')}"
    canonical = urlunsplit(("https", host, path, "", ""))
    navigation = urlunsplit(
        ("https", host, path, urlencode({"xsec_token": mention.xsec_token}), "")
    )
    return navigation, canonical


def _note_from_map(raw_map: Mapping[object, object], note_id: str) -> Mapping[str, Any]:
    root = _unwrap_mapping(raw_map)
    exact = root.get(note_id)
    if isinstance(exact, Mapping):
        note = _note_from_entry(exact)
        declared = _declared_note_id(note)
        if not declared or declared == note_id:
            return note
        raise XhsContractError("requested note was not present in note detail state")

    declared_matches = []
    suffix_matches = []
    for raw_key, raw_entry in root.items():
        if not isinstance(raw_key, str) or not isinstance(raw_entry, Mapping):
            continue
        note = _note_from_entry(raw_entry)
        declared = _declared_note_id(note)
        if declared == note_id:
            declared_matches.append(note)
        elif not declared and _is_note_key_suffix(raw_key, note_id):
            suffix_matches.append(note)

    matches = declared_matches or suffix_matches
    if len(matches) > 1:
        raise XhsContractError("requested note was ambiguous in note detail state")
    if not matches:
        raise XhsContractError("requested note was not present in note detail state")
    return matches[0]


def _note_from_entry(entry: Mapping[object, object]) -> Mapping[str, Any]:
    unwrapped = _unwrap_mapping(entry)
    return _unwrap_mapping(unwrapped.get("note", unwrapped))


def _declared_note_id(note: Mapping[str, Any]) -> str:
    return _sanitize_text(note.get("noteId") or note.get("note_id"), 512)


def _is_note_key_suffix(key: str, note_id: str) -> bool:
    if not key.startswith(note_id) or len(key) <= len(note_id):
        return False
    return key[len(note_id)] in {":", "_", "@", "."}


def _unwrap_mapping(value: object) -> Mapping[str, Any]:
    current = value
    seen: set[int] = set()
    while isinstance(current, Mapping) and id(current) not in seen:
        seen.add(id(current))
        wrapped = current.get("_value")
        if not isinstance(wrapped, Mapping):
            return current
        current = wrapped
    return {}


def _build_note_detail(
    page: Any,
    *,
    note_id: str,
    canonical_url: str,
    note: Mapping[str, Any],
) -> NoteDetail:
    title = _sanitize_text(note.get("title"), 300)
    content = _sanitize_text(note.get("desc"), 6_000)
    author_data = note.get("user")
    author = ""
    if isinstance(author_data, Mapping):
        author = _sanitize_text(
            author_data.get("nickname") or author_data.get("name"), 100
        )
    raw_type = _sanitize_text(note.get("type"), 16)
    note_type = raw_type if raw_type in {"normal", "video"} else "unknown"
    published_at = note.get("time")
    if isinstance(published_at, bool) or not isinstance(published_at, (int, float)):
        published_at = None
    comments = _structured_comments(note.get("comments"))

    if not title:
        title = _locator_text(page, "#detail-title, .title", 300)
    if not content:
        content = _locator_text(page, "#detail-desc, .desc", 6_000)
    if not comments:
        comments = _visible_comment_texts(page)

    return NoteDetail(
        note_id=note_id,
        url=canonical_url,
        type=note_type,
        title=title,
        content=content,
        author=author,
        published_at=published_at,
        comments=comments,
    )


def _structured_comments(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    comments = []
    for item in value:
        raw = item.get("content") if isinstance(item, Mapping) else item
        text = _sanitize_text(raw, 1_000)
        if text:
            comments.append(text)
    return tuple(comments[:10])


def _visible_comment_texts(page: Any) -> tuple[str, ...]:
    try:
        values = page.locator(
            ".comment-item .content, .parent-comment .content"
        ).all_inner_texts()
    except Exception:
        return ()
    return tuple(
        text
        for value in values[:10]
        if (text := _sanitize_text(value, 1_000))
    )


def _locator_text(page: Any, selector: str, limit: int) -> str:
    try:
        return _sanitize_text(
            page.locator(selector).first.inner_text(timeout=2_000), limit
        )
    except Exception:
        return ""


def _comments_disabled(page: Any) -> bool:
    return bool(
        page.evaluate(
            """() => {
                const map = window.__INITIAL_STATE__?.note?.noteDetailMap;
                if (!map || typeof map !== 'object') return false;
                let entry = Object.values(map)[0];
                entry = entry?._value ?? entry;
                let note = entry?.note ?? entry;
                note = note?._value ?? note;
                return Boolean(
                    note?.interactInfo?.commentDisabled ||
                    note?.noteControls?.disableComment
                );
            }"""
        )
    )


def _find_comment(page: Any, comment_id: str) -> Any | None:
    selector = f"#comment-{_css_identifier(comment_id)}"
    for _ in range(_REPLY_SCROLL_ROUNDS):
        locator = page.locator(selector).first
        if locator.count() > 0:
            return locator
        page.mouse.wheel(0, _REPLY_SCROLL_PIXELS)
        page.wait_for_timeout(_REPLY_WAIT_MS)
    return None


def _css_identifier(value: str) -> str:
    escaped = []
    for character in value:
        if character.isascii() and (character.isalnum() or character in {"-", "_"}):
            escaped.append(character)
        else:
            escaped.append(f"\\{ord(character):x} ")
    return "".join(escaped)


def _risk_outcome(manager: Any, page: Any, status: int | None) -> ReplyOutcome | None:
    result = manager.detect_risk(page, status)
    if result.success:
        return None
    code = result.code if result.code in _PAUSE_CODES else "TEMPORARY_FAILURE"
    return _reply_failure(code, "Browser operation paused")


def _confirm_reply_submission(
    manager: Any,
    page: Any,
    input_locator: Any,
    response_statuses: list[int],
) -> ReplyOutcome:
    deadline = monotonic() + (_REPLY_CONFIRM_TIMEOUT_MS / 1_000)
    remaining_ms = _REPLY_CONFIRM_TIMEOUT_MS
    while True:
        while response_statuses:
            risk = _risk_outcome(manager, page, response_statuses.pop(0))
            if risk is not None:
                return risk
        risk = _risk_outcome(manager, page, None)
        if risk is not None:
            return risk
        if _reply_was_confirmed(page, input_locator):
            return ReplyOutcome(success=True, message="Reply submitted")
        if remaining_ms <= 0 or monotonic() >= deadline:
            return _reply_failure(
                "SUBMIT_UNCONFIRMED", "The reply submission could not be confirmed"
            )
        wait_ms = min(_EVENT_PUMP_MS, remaining_ms)
        page.wait_for_timeout(wait_ms)
        remaining_ms -= wait_ms


def _reply_was_confirmed(page: Any, input_locator: Any) -> bool:
    try:
        if input_locator.text_content(timeout=500) == "":
            return True
    except Exception:
        pass
    try:
        return (
            page.locator(
                ".el-message--success:visible, [data-reply-success]:visible"
            ).count()
            > 0
        )
    except Exception:
        return False


def _ensure_risk_ok(manager: Any, page: Any, status: int | None) -> None:
    result = manager.detect_risk(page, status)
    if not result.success:
        raise XhsPausedError(result.code or "TEMPORARY_FAILURE")


def _reply_failure(code: str, message: str) -> ReplyOutcome:
    return ReplyOutcome(success=False, code=code, message=message)


def _sanitize_text(value: object, limit: int) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(
        character
        for character in text
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    return text.strip()[:limit]


def _response_status(response: Any) -> int | None:
    return _optional_status(getattr(response, "status", None))


def _optional_status(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _is_timeout(error: Exception) -> bool:
    return isinstance(error, TimeoutError) or error.__class__.__name__ == "TimeoutError"

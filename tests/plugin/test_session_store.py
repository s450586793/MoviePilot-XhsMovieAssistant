from __future__ import annotations

import json
import stat

import pytest
import xhsmovieassistant.browser as browser_module


def test_cookie_import_persists_private_current_site_storage_state(tmp_path) -> None:
    store = browser_module.SessionStore(
        tmp_path,
        "https://www.xiaohongshu.com",
    )

    result = store.import_cookie_header("a1=secret-a1; web_session=secret-session")

    assert result == browser_module.OperationResult(
        success=True,
        data={"credential_type": "cookie", "cookie_count": 2},
    )
    state = json.loads(store.path.read_text(encoding="utf-8"))
    assert state == {
        "cookies": [
            {
                "name": "a1",
                "value": "secret-a1",
                "domain": ".xiaohongshu.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "web_session",
                "value": "secret-session",
                "domain": ".xiaohongshu.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            },
        ],
        "origins": [],
    }
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert "secret-a1" not in repr(result)
    assert "secret-session" not in repr(result)


def test_storage_state_import_keeps_only_current_site_credentials(tmp_path) -> None:
    store = browser_module.SessionStore(tmp_path, "https://www.rednote.com")
    state = {
        "cookies": [
            {
                "name": "web_session",
                "value": "rednote-secret",
                "domain": ".rednote.com",
                "path": "/",
                "expires": 2_000_000_000,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "unrelated",
                "value": "other-secret",
                "domain": ".example.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            },
        ],
        "origins": [
            {
                "origin": "https://www.rednote.com",
                "localStorage": [{"name": "locale", "value": "zh-CN"}],
            },
            {
                "origin": "https://accounts.example.com",
                "localStorage": [{"name": "token", "value": "other-secret"}],
            },
        ],
    }

    result = store.import_storage_state(state)

    assert result == browser_module.OperationResult(
        success=True,
        data={"credential_type": "storage_state", "cookie_count": 1},
    )
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert persisted == {
        "cookies": [state["cookies"][0]],
        "origins": [state["origins"][0]],
    }
    assert "rednote-secret" not in repr(result)
    assert "other-secret" not in store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "cookie",
    [
        {"name": "a1", "domain": ".xiaohongshu.com", "path": "/"},
        {
            "name": "a1",
            "value": "secret",
            "domain": ".xiaohongshu.com",
            "path": "relative",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "a1",
            "value": "secret",
            "domain": ".xiaohongshu.com",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Invalid",
        },
    ],
)
def test_invalid_storage_state_does_not_replace_existing_credentials(
    tmp_path,
    cookie,
) -> None:
    store = browser_module.SessionStore(tmp_path, "https://www.xiaohongshu.com")
    store.import_cookie_header("a1=known-good")
    before = store.path.read_bytes()

    result = store.import_storage_state({"cookies": [cookie], "origins": []})

    assert result.success is False
    assert result.code == "INVALID_CREDENTIALS"
    assert store.path.read_bytes() == before
    assert "secret" not in repr(result)


def test_session_store_reports_presence_and_clears_credentials(tmp_path) -> None:
    store = browser_module.SessionStore(tmp_path, "https://www.xiaohongshu.com")

    assert store.status() == "MISSING"
    store.import_cookie_header("a1=secret")
    assert store.status() == "PRESENT"

    store.clear()

    assert store.status() == "MISSING"
    assert store.path.exists() is False


def test_cookie_import_accepts_a_copied_cookie_request_header(tmp_path) -> None:
    store = browser_module.SessionStore(tmp_path, "https://www.rednote.com")

    result = store.import_cookie_header("Cookie: a1=secret; web_session=session")

    assert result.success is True
    state = json.loads(store.path.read_text(encoding="utf-8"))
    assert [cookie["name"] for cookie in state["cookies"]] == [
        "a1",
        "web_session",
    ]


def test_invalid_current_site_local_storage_does_not_replace_credentials(tmp_path) -> None:
    store = browser_module.SessionStore(tmp_path, "https://www.xiaohongshu.com")
    store.import_cookie_header("a1=known-good")
    before = store.path.read_bytes()
    cookie = {
        "name": "a1",
        "value": "new-secret",
        "domain": ".xiaohongshu.com",
        "path": "/",
        "expires": -1,
        "httpOnly": False,
        "secure": True,
        "sameSite": "Lax",
    }

    result = store.import_storage_state(
        {
            "cookies": [cookie],
            "origins": [
                {
                    "origin": "https://www.xiaohongshu.com",
                    "localStorage": [{"name": "session-without-value"}],
                }
            ],
        }
    )

    assert result.code == "INVALID_CREDENTIALS"
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "cookie_header",
    [
        "a1=secret\rweb_session=other",
        "a1=secret\x00",
        "a1=secret\x7f",
    ],
)
def test_cookie_import_rejects_control_characters(
    tmp_path,
    cookie_header: str,
) -> None:
    store = browser_module.SessionStore(tmp_path, "https://www.xiaohongshu.com")

    result = store.import_cookie_header(cookie_header)

    assert result.success is False
    assert result.code == "INVALID_CREDENTIALS"
    assert store.status() == "MISSING"

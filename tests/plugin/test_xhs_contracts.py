from datetime import datetime, timezone

from xhsmovieassistant.xhs_contracts import (
    TransientMention,
    parse_authorized_ids,
    parse_mentions_payload,
)


def _message(**overrides: object) -> dict[str, object]:
    message: dict[str, object] = {
        "id": "m1",
        "type": "mention/comment",
        "time": 1_700_000_000,
        "user_info": {"userid": "u-main", "nickname": "untrusted nickname"},
        "comment_info": {"id": "c1", "content": "请订阅《星际穿越》"},
        "item_info": {"id": "note-1", "xsec_token": "transient-token"},
    }
    message.update(overrides)
    return message


def test_only_mention_comment_with_stable_user_id_is_accepted() -> None:
    payload = {
        "data": {
            "message_list": [
                _message(),
                _message(id="m2", type="comment/item"),
                _message(id="m3", user_info={"nickname": "u-main"}),
            ]
        }
    }

    mentions = parse_mentions_payload(payload)

    assert [(item.mention_id, item.sender_user_id) for item in mentions] == [("m1", "u-main")]


def test_rednote_track_type_eight_accepts_mentions_but_rejects_normal_comments() -> None:
    payload = {
        "data": {
            "message_list": [
                _message(id="m-item", type="comment/item", track_type="8"),
                _message(id="m-comment", type="comment/comment", track_type="8"),
                _message(id="normal-item", type="comment/item", track_type="41"),
                _message(id="normal-comment", type="comment/comment", track_type="26"),
            ]
        }
    }

    mentions = parse_mentions_payload(payload)

    assert [item.mention_id for item in mentions] == ["m-item", "m-comment"]


def test_authorized_ids_support_lines_commas_and_whitespace() -> None:
    assert parse_authorized_ids(" u1\nu2, u3 , u1 ") == frozenset({"u1", "u2", "u3"})


def test_dom_only_notification_without_user_id_cannot_authorize() -> None:
    assert parse_mentions_payload({"data": {"message_list": [{"type": "mention/comment"}]}}) == ()


def test_parser_accepts_documented_id_aliases_and_parent_comment() -> None:
    mention = parse_mentions_payload(
        {
            "data": {
                "message_list": [
                    _message(
                        user_info={"user_id": "u-alt"},
                        comment_info={
                            "comment_id": "c-alt",
                            "content": "想看",
                            "target_comment": {"comment_id": "parent-1"},
                        },
                    )
                ]
            }
        }
    )[0]

    assert (mention.sender_user_id, mention.comment_id, mention.parent_comment_id) == (
        "u-alt",
        "c-alt",
        "parent-1",
    )


def test_parser_accepts_second_and_millisecond_timestamps() -> None:
    seconds = parse_mentions_payload({"data": {"message_list": [_message(time=1_700_000_000)]}})[0]
    milliseconds = parse_mentions_payload(
        {"data": {"message_list": [_message(id="m2", time=1_700_000_000_000)]}}
    )[0]

    assert seconds.created_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert milliseconds.created_at == seconds.created_at


def test_parser_uses_utc_now_for_invalid_timestamp() -> None:
    mention = parse_mentions_payload({"data": {"message_list": [_message(time="not-a-time")]}})[0]

    assert mention.created_at.tzinfo is timezone.utc


def test_parser_rejects_unknown_types_and_missing_required_fields() -> None:
    payload = {
        "data": {
            "message_list": [
                _message(type="comment/comment"),
                _message(id=""),
                _message(comment_info={"id": "", "content": "想看"}),
                _message(item_info={"id": "", "xsec_token": "token"}),
            ]
        }
    }

    assert parse_mentions_payload(payload) == ()


def test_transient_mention_does_not_reveal_xsec_token_in_repr() -> None:
    mention = parse_mentions_payload({"data": {"message_list": [_message()]}})[0]

    assert isinstance(mention, TransientMention)
    assert "transient-token" not in repr(mention)

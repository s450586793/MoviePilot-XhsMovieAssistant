from dataclasses import dataclass

from xhsmovieassistant.request_builder import build_media_request, sanitize_text
from xhsmovieassistant.xhs_contracts import parse_mentions_payload


@dataclass
class Detail:
    url: str = "https://www.xiaohongshu.com/explore/note-1"
    type: str = "video"
    title: object = "星际穿越"
    content: object = "诺兰科幻电影"
    author: object = "作者"
    comments: object = ()


def _mention():
    return parse_mentions_payload(
        {
            "data": {
                "message_list": [
                    {
                        "id": "mention-1",
                        "type": "mention/comment",
                        "user_info": {"userid": "user-1"},
                        "comment_info": {"id": "comment-1", "content": "请订阅《星际穿越》"},
                        "item_info": {"id": "note-1", "xsec_token": "secret-token"},
                    }
                ]
            }
        }
    )[0]


def test_build_media_request_serializes_only_sanitized_model_fields() -> None:
    request = build_media_request(
        _mention(),
        Detail(
            title="Ａ\x00" + "标题" * 200,
            content="正文\x1b\t保留" + "内" * 7000,
            author="作者\x07" + "名" * 200,
            comments=["《星际穿越》 2014"],
        ),
    )
    serialized = request.model_dump_json()

    assert request.request_id == "xhs_mention-1"
    assert request.note.title.startswith("A")
    assert len(request.note.title) == 300
    assert len(request.note.content) == 6000
    assert len(request.note.author) == 100
    assert "\u0000" not in serialized
    assert "secret-token" not in serialized


def test_sanitize_text_normalizes_removes_controls_and_limits_length() -> None:
    assert sanitize_text(" Ａ\x00\x1b\n\tB ", 4) == "A\n\tB"
    assert sanitize_text(None, 10) == ""


def test_build_media_request_prioritizes_deduplicates_and_limits_comments() -> None:
    comments = [
        "普通评论",
        "《盗梦空间》",
        "上映于 2014 年",
        "星际 穿越 星际 穿越",
        "《盗梦空间》",
        "\x00",
        *[f"无关评论 {index}" for index in range(12)],
    ]

    request = build_media_request(_mention(), Detail(comments=comments))

    assert request.note.relevant_comments[:3] == [
        "《盗梦空间》",
        "上映于 2014 年",
        "星际 穿越 星际 穿越",
    ]
    assert len(request.note.relevant_comments) == 10
    assert len(set(request.note.relevant_comments)) == len(request.note.relevant_comments)


def test_build_media_request_rejects_non_iterable_comment_collection() -> None:
    request = build_media_request(_mention(), Detail(comments="not a collection"))

    assert request.note.relevant_comments == []


def test_build_media_request_clamps_unknown_note_type_to_unknown() -> None:
    request = build_media_request(_mention(), Detail(type="article"))

    assert request.note.type == "unknown"

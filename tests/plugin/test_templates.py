import pytest

from xhsmovieassistant.models import (
    MediaMatch,
    ProcessingResult,
    RequestStatus,
    Resolution,
)
from xhsmovieassistant.templates import ReplyTemplates


def resolved_result(status: RequestStatus) -> ProcessingResult:
    return ProcessingResult(
        status=status,
        message="completed",
        resolution=Resolution(
            status="resolved",
            title="星际穿越",
            original_title="Interstellar",
            media_type="movie",
            year=2014,
            confidence=0.98,
        ),
        match=MediaMatch(
            title="星际穿越",
            original_title="Interstellar",
            media_type="movie",
            year=2014,
            source="tmdb",
            source_id="157336",
        ),
    )


def test_confirmation_uses_the_fixed_template() -> None:
    result = ProcessingResult(status=RequestStatus.NEED_CONFIRMATION)

    assert ReplyTemplates().render(result) == (
        "暂时无法确定这篇笔记中的具体影视作品，请人工确认。"
    )


def test_subscribed_template_renders_only_deterministic_media_fields() -> None:
    assert ReplyTemplates().render(resolved_result(RequestStatus.SUBSCRIBED)) == (
        "检测到电影《星际穿越》（2014），已推送订阅。"
    )


def test_custom_template_supports_known_fields_and_caps_reply_length() -> None:
    templates = ReplyTemplates(
        {"SUBSCRIBED": "{title}|{original_title}|{year}|{season}|{result}" + "字" * 300}
    )

    reply = templates.render(resolved_result(RequestStatus.SUBSCRIBED))

    assert reply is not None
    assert reply.startswith("星际穿越|Interstellar|2014||SUBSCRIBED")
    assert len(reply) == 200


def test_unknown_template_variable_is_rejected_without_reading_result_internals() -> None:
    with pytest.raises(ValueError, match="unknown template variable"):
        ReplyTemplates({"SUBSCRIBED": "{subscription_id}"})


def test_unsupported_status_has_no_public_reply() -> None:
    assert ReplyTemplates().render(
        ProcessingResult(status=RequestStatus.NOT_MEDIA)
    ) is None


def test_render_rejects_non_result_values() -> None:
    with pytest.raises(TypeError, match="ProcessingResult"):
        ReplyTemplates().render(object())  # type: ignore[arg-type]

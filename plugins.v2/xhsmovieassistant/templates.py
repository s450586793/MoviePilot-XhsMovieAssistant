"""Deterministic public-reply templates for processing outcomes."""

from __future__ import annotations

from collections.abc import Mapping
from string import Formatter

from .models import ProcessingResult


DEFAULT_TEMPLATES = {
    "SUBSCRIBED": "检测到{media_type}《{title}》{year_text}{season_text}，已推送订阅。",
    "ALREADY_SUBSCRIBED": "《{title}》{year_text}{season_text}已经订阅，无需重复添加。",
    "ALREADY_IN_LIBRARY": "《{title}》{year_text}{season_text}已经在媒体库中。",
    "NEED_CONFIRMATION": "暂时无法确定这篇笔记中的具体影视作品，请人工确认。",
    "FAILED": "本次订阅处理失败，详情已通过 MoviePilot 通知发送。",
}

_KNOWN_VARIABLES = frozenset(
    {
        "title",
        "original_title",
        "year",
        "year_text",
        "media_type",
        "season",
        "season_text",
        "result",
    }
)
_MEDIA_TYPE_TEXT = {"movie": "电影", "tv": "剧集", "unknown": "影视作品"}


class ReplyTemplates:
    """Render fixed reply text for supported processing outcomes."""

    def __init__(self, templates: Mapping[str, str] | None = None) -> None:
        self._templates = dict(DEFAULT_TEMPLATES)
        if templates is not None:
            for status, template in templates.items():
                if status not in DEFAULT_TEMPLATES:
                    raise ValueError(f"unsupported reply status: {status}")
                if not isinstance(template, str):
                    raise TypeError("reply template must be a string")
                _validate_template(template)
                self._templates[status] = template

    def render(self, result: ProcessingResult) -> str | None:
        """Return the configured fixed reply for a supported status."""
        if not isinstance(result, ProcessingResult):
            raise TypeError("result must be a ProcessingResult")
        template = self._templates.get(result.status.value)
        if template is None:
            return None

        media = result.match or result.resolution
        title = media.title if media is not None else ""
        original_title = media.original_title if media is not None else ""
        year = media.year if media is not None else None
        season = media.season if media is not None else None
        media_type = media.media_type if media is not None else "unknown"
        values = {
            "title": title,
            "original_title": original_title,
            "year": "" if year is None else str(year),
            "year_text": "" if year is None else f"（{year}）",
            "media_type": _MEDIA_TYPE_TEXT[media_type],
            "season": "" if season is None else str(season),
            "season_text": "" if season is None else f"第{season}季",
            "result": result.status.value,
        }
        return template.format_map(values)[:200]


def _validate_template(template: str) -> None:
    for _, field_name, format_spec, conversion in Formatter().parse(template):
        if field_name is None:
            continue
        if (
            field_name not in _KNOWN_VARIABLES
            or format_spec
            or conversion is not None
        ):
            raise ValueError(f"unknown template variable: {field_name}")

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


class _Response:
    def __init__(
        self,
        *,
        success: bool,
        message: str | None = None,
        data: Any = None,
    ) -> None:
        self.success = success
        self.message = message
        self.data = {} if data is None else data


class _NotificationType(Enum):
    Plugin = "plugin"


class _PluginBase:
    def get_data_path(self) -> Path:
        return Path("/config/plugins") / self.__class__.__name__

    def post_message(self, **kwargs: Any) -> None:
        self._posted_message = kwargs


def _install_moviepilot_stubs() -> None:
    app = ModuleType("app")
    core = ModuleType("app.core")
    config = ModuleType("app.core.config")
    plugins = ModuleType("app.plugins")
    schemas = ModuleType("app.schemas")
    schema_types = ModuleType("app.schemas.types")

    config.settings = SimpleNamespace(API_TOKEN="test-api-token", PROXY={})
    plugins._PluginBase = _PluginBase
    schemas.Response = _Response
    schemas.NotificationType = _NotificationType
    schema_types.NotificationType = _NotificationType

    app.core = core
    app.plugins = plugins
    app.schemas = schemas
    core.config = config

    for name, module in {
        "app": app,
        "app.core": core,
        "app.core.config": config,
        "app.plugins": plugins,
        "app.schemas": schemas,
        "app.schemas.types": schema_types,
    }.items():
        sys.modules.setdefault(name, module)


_install_moviepilot_stubs()


@pytest.fixture
def media_request_values() -> dict[str, object]:
    return {
        "request_id": "xhs_mention_123",
        "source": "xiaohongshu",
        "intent": "subscribe",
        "trigger_comment": "想看",
        "note": {
            "id": "note-1",
            "url": "https://www.xiaohongshu.com/explore/note-1",
        },
    }

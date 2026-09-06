"""Offline importer for Phase 3 SQLite and idempotency validation."""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from xhs_probe.contracts import MentionsPayloadError
from xhs_probe.ingest import extract_requests
from xhs_probe.requests import XhsRequestRepository


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import one captured mentions response into SQLite."
    )
    parser.add_argument("capture_path", type=Path)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("XHS_DATABASE_PATH", "/data/app.db")),
    )
    parser.add_argument(
        "--authorized-user-id",
        default=os.getenv("AUTHORIZED_XHS_USER_ID", ""),
    )
    parser.add_argument(
        "--site-url",
        default=os.getenv(
            "XHS_NOTIFICATION_URL",
            "https://www.xiaohongshu.com/notification",
        ),
    )
    return parser


def _mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"capture {field_name} must be an object")
    return value


def _parse_capture(path: Path) -> tuple[Mapping[str, Any], datetime]:
    document = _mapping(
        json.loads(path.read_text(encoding="utf-8")),
        field_name="document",
    )
    payload = _mapping(document.get("payload"), field_name="payload")
    captured_at_value = document.get("captured_at")
    if not isinstance(captured_at_value, str) or not captured_at_value.strip():
        raise ValueError("capture captured_at must be an ISO timestamp")
    captured_at = datetime.fromisoformat(
        captured_at_value.strip().replace("Z", "+00:00")
    )
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    return payload, captured_at


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload, captured_at = _parse_capture(args.capture_path)
        extraction = extract_requests(
            payload,
            authorized_user_id=args.authorized_user_id,
            site_url=args.site_url,
            captured_at=captured_at,
        )
        repository = XhsRequestRepository(args.database)
        created_count = 0
        duplicate_count = 0
        for request in extraction.requests:
            result = repository.save(request)
            if result.created:
                created_count += 1
            else:
                duplicate_count += 1

        output = {
            "status": "ok",
            "created_count": created_count,
            "duplicate_count": duplicate_count,
            "ignored_count": extraction.ignored_count,
            "invalid_count": extraction.invalid_count,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except json.JSONDecodeError:
        output = {
            "status": "error",
            "code": "INVALID_CAPTURE",
            "error": "Capture file is not valid JSON.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 2
    except (
        OSError,
        sqlite3.Error,
        MentionsPayloadError,
        ValueError,
    ) as error:
        output = {
            "status": "error",
            "code": "IMPORT_ERROR",
            "error": f"Capture import failed: {type(error).__name__}",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

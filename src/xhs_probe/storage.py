"""Secure local storage helpers for probe evidence."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def write_raw_capture(
    *,
    output_dir: Path,
    response_url: str,
    payload: Mapping[str, Any],
    captured_at: datetime,
) -> Path:
    """Create a non-overwriting raw capture readable only by its owner."""
    captured_utc = captured_at.astimezone(timezone.utc).replace(microsecond=0)
    timestamp = captured_utc.strftime("%Y%m%dT%H%M%SZ")
    captured_text = captured_utc.isoformat().replace("+00:00", "Z")

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    output_path = output_dir / f"mentions-{timestamp}.json"
    descriptor = os.open(
        output_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as capture_file:
        json.dump(
            {
                "captured_at": captured_text,
                "response_url": response_url,
                "payload": payload,
            },
            capture_file,
            ensure_ascii=False,
            indent=2,
        )
        capture_file.write("\n")
    return output_path

import json
from pathlib import Path

from xhs_probe import phase3_cli
from xhs_probe.requests import XhsRequestRepository


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_cli_imports_an_authorized_capture_idempotently(tmp_path, capsys) -> None:
    payload = json.loads((FIXTURE_DIR / "mentions_success.json").read_text())
    capture_path = tmp_path / "mentions.json"
    capture_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-09-06T01:02:03Z",
                "response_url": (
                    "https://webapi.rednote.com/api/sns/web/v1/you/mentions"
                ),
                "payload": payload,
            }
        ),
        encoding="utf-8",
    )
    database_path = tmp_path / "app.db"
    argv = [
        str(capture_path),
        "--database",
        str(database_path),
        "--authorized-user-id",
        "user-main",
        "--site-url",
        "https://www.rednote.com/notification",
    ]

    first_exit_code = phase3_cli.main(argv)
    first_output = json.loads(capsys.readouterr().out)
    second_exit_code = phase3_cli.main(argv)
    second_output = json.loads(capsys.readouterr().out)

    assert first_exit_code == 0
    assert first_output == {
        "status": "ok",
        "created_count": 1,
        "duplicate_count": 0,
        "ignored_count": 1,
        "invalid_count": 0,
    }
    assert second_exit_code == 0
    assert second_output == {
        "status": "ok",
        "created_count": 0,
        "duplicate_count": 1,
        "ignored_count": 1,
        "invalid_count": 0,
    }
    stored = XhsRequestRepository(database_path).get_by_mention_id("mention-1")
    assert stored is not None
    assert stored.sender_user_id == "user-main"


def test_cli_reports_invalid_json_without_echoing_its_contents(
    tmp_path, capsys
) -> None:
    capture_path = tmp_path / "mentions.json"
    capture_path.write_text('{"private": "do-not-echo"', encoding="utf-8")

    exit_code = phase3_cli.main(
        [
            str(capture_path),
            "--database",
            str(tmp_path / "app.db"),
            "--authorized-user-id",
            "user-main",
        ]
    )
    output_text = capsys.readouterr().out

    assert exit_code == 2
    assert json.loads(output_text) == {
        "status": "error",
        "code": "INVALID_CAPTURE",
        "error": "Capture file is not valid JSON.",
    }
    assert "do-not-echo" not in output_text

import re
from datetime import datetime, timezone
from pathlib import Path

from video_guestbook.media.recorder import (
    build_output_filename,
    build_output_path,
    generate_session_id,
)

SESSION_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")


def test_generate_session_id_matches_expected_pattern():
    session_id = generate_session_id()
    assert SESSION_ID_RE.match(session_id), session_id


def test_generate_session_id_uses_provided_timestamp():
    now = datetime(2026, 9, 12, 14, 30, 5, tzinfo=timezone.utc)
    session_id = generate_session_id(now)
    assert session_id.startswith("20260912_143005_")


def test_generate_session_id_is_unique_across_calls():
    ids = {generate_session_id() for _ in range(200)}
    assert len(ids) == 200


def test_build_output_filename():
    assert build_output_filename("20260912_143005_abcd1234") == "20260912_143005_abcd1234.mp4"


def test_build_output_path_joins_dir_and_filename(tmp_path):
    session_id = "20260912_143005_abcd1234"
    path = build_output_path(tmp_path, session_id)
    assert path == tmp_path / f"{session_id}.mp4"
    assert isinstance(path, Path)

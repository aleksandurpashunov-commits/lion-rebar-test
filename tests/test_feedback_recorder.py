from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.diagnostics import append_feedback_event


def test_feedback_event_is_project_local_and_machine_readable(tmp_path: Path) -> None:
    root = tmp_path / "PROJECT"
    working = root / "working"
    working.mkdir(parents=True)
    (working / "project.json").write_text(
        json.dumps(
            {
                "name": "TEST",
                "engine_id": "lion-rebar-master",
                "engine_version": "0.3.4",
                "engine_sha256": "engine-sha",
                "extensions": {"source_sha256": "source-sha"},
            }
        ),
        encoding="utf-8",
    )

    path = append_feedback_event(
        root,
        "manual_assigned",
        {
            "annotation_handle": "A1C",
            "shape_code": "F015",
            "geometry_handles": ("9C6", "9C7", "9CF", "A29"),
            "irrelevant_path": tmp_path / "outside" / "file.dxf",
        },
        occurred_at="2026-07-27T20:00:00+03:00",
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["schema"] == "lion-rebar-review-feedback-event/v1"
    assert row["event"] == "MANUAL_ASSIGNED"
    assert row["project_name"] == "TEST"
    assert row["engine"]["version"] == "0.3.4"
    assert row["payload"]["geometry_handles"] == ["9C6", "9C7", "9CF", "A29"]
    assert row["payload"]["irrelevant_path"] == "file.dxf"
    assert str(tmp_path) not in path.read_text(encoding="utf-8")

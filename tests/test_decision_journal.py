from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.diagnostics.feedback import append_feedback_event


def test_decision_events_are_written_to_named_decision_journal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    working = root / "working"
    working.mkdir(parents=True)
    (working / "project.json").write_text(
        json.dumps(
            {
                "name": "Demo",
                "engine_id": "lion-rebar-master",
                "engine_version": "0.3.13",
                "extensions": {"source_sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )
    append_feedback_event(
        root,
        "APPROVED",
        {"annotation_handle": "A1", "row_context": {"master_shape_code": "F005"}},
    )
    journal = working / "decision_journal.jsonl"
    assert journal.is_file()
    row = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
    assert row["schema"] == "lion-rebar-decision-journal-event/v1"
    assert row["event"] == "APPROVED"


def test_bulk_decision_journal_writes_one_line_per_position(tmp_path: Path) -> None:
    root = tmp_path / "project"
    working = root / "working"
    working.mkdir(parents=True)
    (working / "project.json").write_text(
        json.dumps(
            {
                "name": "Demo",
                "engine_id": "lion-rebar-master",
                "engine_version": "0.3.13",
                "extensions": {"source_sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )
    append_feedback_event(
        root,
        "BULK_APPROVED",
        {
            "mode": "GEOMETRY_SAMPLE",
            "decisions": [
                {"annotation_handle": "A1", "annotation_text": "2N12x700"},
                {"annotation_handle": "A2", "annotation_text": "3N16x500"},
            ],
        },
    )
    rows = [
        json.loads(line)
        for line in (working / "decision_journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["payload"]["annotation_handle"] for row in rows] == ["A1", "A2"]
    assert all(row["payload"]["bulk_event"] is True for row in rows)

from __future__ import annotations

from lion_rebar.session.learning_pack import _decision_rows


def _manual_event(
    handle: str,
    shape: str,
    *,
    count: int,
    diameter: float,
    length: float,
    event_id: int,
) -> dict:
    return {
        "event_id": event_id,
        "at": f"2026-08-13T18:00:0{event_id}+03:00",
        "event_type": "MANUAL_ASSIGNED",
        "project": "R6_SESSION_TEST_A-1",
        "source_sha256": "historical-dxf",
        "engine_version": "0.3.10",
        "annotation_handle": handle,
        "payload": {
            "annotation": {"handle": handle, "text": f"{count}N{diameter:g}x{length:g}"},
            "action_class": "OVERRIDE",
            "resolved": {
                "shape_code": shape,
                "count": count,
                "diameter_mm": diameter,
                "production_length_cm": length,
            },
            "dimensions": {
                "declared_length_cm": length,
                "production_segment_lengths_cm": [],
            },
            "geometry": {"handles": [f"G-{handle}"]},
        },
    }


def test_three_historical_manual_shapes_are_traceable_partial_examples() -> None:
    events = [
        _manual_event("A5CE::BJ-FI-L", "F008", count=4716, diameter=10, length=97, event_id=1),
        _manual_event("B252::BJ-FI-L", "F007", count=2050, diameter=10, length=47, event_id=2),
        _manual_event("A171::BJ-FI-L", "F001", count=350, diameter=12, length=300, event_id=3),
    ]

    rows = _decision_rows(events)

    assert {row["annotation_handle"] for row in rows} == {
        "A5CE::BJ-FI-L",
        "B252::BJ-FI-L",
        "A171::BJ-FI-L",
    }
    assert all(row["complete_production_resolution"] is False for row in rows)
    assert all(row["field_completeness"]["shape_revision"] is False for row in rows)
    assert all(row["shape_semantics_status"] == "REQUIRES_VERSIONED_SHAPE_REVIEW" for row in rows)
    assert next(row for row in rows if row["accepted_shape_code"] == "F007")["accepted_segment_lengths_cm"] == []
    assert next(row for row in rows if row["accepted_shape_code"] == "F008")["accepted_segment_lengths_cm"] == []
    straight = next(row for row in rows if row["accepted_shape_code"] == "F001")
    assert straight["accepted_segment_lengths_cm"] == [300.0]
    assert straight["field_provenance"]["accepted_segments"] == "DERIVED_FROM_EXPLICIT_ACCEPTED_TOTAL_FOR_F001"


def test_shape_correction_does_not_inherit_segments_from_old_suggestion() -> None:
    event = _manual_event("A", "F008", count=1, diameter=10, length=47, event_id=4)
    event["payload"]["original"] = {
        "shape_code": "F005",
        "segment_lengths_cm": [10, 27, 10],
    }
    event["payload"]["dimensions"] = {"declared_length_cm": 47}

    row = _decision_rows([event])[0]

    assert row["suggested_segment_lengths_cm"] == [10.0, 27.0, 10.0]
    assert row["accepted_segment_lengths_cm"] == []
    assert row["complete_production_resolution"] is False

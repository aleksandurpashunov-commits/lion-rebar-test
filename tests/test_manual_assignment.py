from __future__ import annotations

import math

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.manual_assignment import (
    F006_COMPOSITE_KIND,
    ManualGeometryError,
    summarize_selected_geometry,
    validate_manual_geometry_records,
)


def _line(handle: str, start: tuple[float, float], end: tuple[float, float]) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer="ARM_Bars",
        geometry={
            "start": [start[0], start[1], 0.0],
            "end": [end[0], end[1], 0.0],
        },
    )


def test_manual_guard_rejects_h3_schematic_frame_6fd7() -> None:
    schematic = DxfEntityRecord(
        handle="6FD7",
        entity_type="LWPOLYLINE",
        layer="Shema Blockove",
        geometry={
            "points": [[4085.576, 1253.007, 0.0], [5719.375, 1253.007, 0.0]],
            "closed": False,
        },
    )

    with pytest.raises(ManualGeometryError, match="6FD7"):
        validate_manual_geometry_records(
            [schematic],
            allowed_handles={"706D", "7070"},
        )


def test_manual_guard_rejects_shared_h3_legend_geometry_706d_7070() -> None:
    records = [
        _line("706D", (0.0, 0.0), (0.0, 360.0)),
        _line("7070", (0.0, 360.0), (20.0, 360.0)),
    ]

    with pytest.raises(ManualGeometryError, match="обща геометрия на легендата"):
        validate_manual_geometry_records(
            records,
            allowed_handles={"706D", "7070"},
            protected_handles={"706D", "7070"},
        )


def test_manual_guard_returns_only_authoritative_unprotected_rebar() -> None:
    records = [_line("A1", (0.0, 0.0), (100.0, 0.0))]

    assert validate_manual_geometry_records(
        records,
        allowed_handles={"a1"},
        protected_handles={"706D", "7070"},
    ) == tuple(records)


def test_composite_stirrup_with_gapped_corners_and_two_keys_at_same_corner():
    # Actual pattern from Alfa_Test_Project: straight portions stop 3 cm before
    # the theoretical bends.  The cage is therefore 40 x 70, while the measured
    # straight LINE lengths are 34 + 64 + 34 + 64.  Both 20 cm keys are drawn at
    # the same corner.
    records = [
        _line("TOP", (3.0, 70.0), (37.0, 70.0)),
        _line("BOTTOM", (3.0, 0.0), (37.0, 0.0)),
        _line("LEFT", (0.0, 3.0), (0.0, 67.0)),
        _line("RIGHT", (40.0, 3.0), (40.0, 67.0)),
        _line("KEY1", (0.0, 64.0), (14.1421356, 49.8578644)),
        _line("KEY2", (6.0, 70.0), (20.1421356, 55.8578644)),
    ]

    summary = summarize_selected_geometry(records)

    assert summary.connected is True
    assert summary.physical_connected is False
    assert summary.closed is True
    assert summary.branched is False
    assert summary.inferred_composite is True
    assert summary.composite_kind == F006_COMPOSITE_KIND
    assert summary.segment_count == 6
    assert sorted(round(value, 3) for value in summary.segment_lengths_cm) == [
        20.0,
        20.0,
        34.0,
        34.0,
        64.0,
        64.0,
    ]
    assert round(summary.total_length_cm, 3) == 236.0


def test_random_disconnected_six_lines_are_not_accepted_as_stirrup():
    records = [
        _line("A", (0.0, 0.0), (10.0, 0.0)),
        _line("B", (30.0, 0.0), (45.0, 0.0)),
        _line("C", (0.0, 30.0), (0.0, 50.0)),
        _line("D", (70.0, 10.0), (75.0, 35.0)),
        _line("E", (100.0, 100.0), (110.0, 108.0)),
        _line("F", (130.0, 80.0), (145.0, 83.0)),
    ]

    summary = summarize_selected_geometry(records)

    assert summary.connected is False
    assert summary.inferred_composite is False
    assert summary.composite_kind is None


def test_lwpolyline_bulge_is_preserved_as_curve_warning():
    record = DxfEntityRecord(
        handle="ARC-POLY",
        entity_type="LWPOLYLINE",
        layer="ARM-GORNA",
        geometry={
            "points": [
                [0.0, 0.0, 0.25],
                [50.0, 0.0, 0.0],
                [50.0, 40.0, 0.0],
            ],
            "closed": False,
        },
    )

    summary = summarize_selected_geometry([record])

    assert summary.connected is True
    assert summary.contains_curves is True
    assert summary.curve_segment_count == 1
    theta = 4.0 * math.atan(0.25)
    radius = 50.0 / (2.0 * math.sin(theta / 2.0))
    assert summary.total_length_cm == pytest.approx(radius * theta + 40.0)
    assert any("дъгови сегменти" in warning for warning in summary.warnings)


def test_standalone_arc_uses_radius_times_sweep_for_manual_total() -> None:
    record = DxfEntityRecord(
        handle="ARC-ENTITY",
        entity_type="ARC",
        layer="ARM-GORNA",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "radius": 10.0,
            "start_angle": 350.0,
            "end_angle": 20.0,
        },
    )

    summary = summarize_selected_geometry([record])

    assert summary.contains_curves is True
    assert summary.curve_segment_count == 1
    assert summary.total_length_cm == pytest.approx(10.0 * math.radians(30.0))
    assert summary.training_eligible is True

from __future__ import annotations

import math

import pytest

from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.batch_geometry_report import _analysis_row
from lion_rebar.dxf.line_chain_v015 import (
    NearGapEdge,
    audit_unique_one_gap_bridge,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.manual_assignment import summarize_selected_geometry


STRICT = 0.75
MAX_GAP = 2.0
REAL_GAP = 1.40588


def _edge(
    handle: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    layer: str = "ARM-GORNA",
) -> NearGapEdge:
    return NearGapEdge(handle=handle, layer=layer, start=start, end=end)


def _line(
    handle: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    layer: str = "ARM-GORNA",
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer=layer,
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
    )


def _annotation() -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text="8N12x483",
        geometry={"insert": [150.0, -30.0, 0.0], "height": 3.0},
    )


def _real_case_lines() -> list[DxfEntityRecord]:
    diagonal_start = (133.84 + REAL_GAP, 0.0)
    diagonal_end = (
        diagonal_start[0] + 286.0 * math.cos(math.radians(29.0)),
        diagonal_start[1] + 286.0 * math.sin(math.radians(29.0)),
    )
    return [
        _line("9C6", (0.0, 12.0), (0.0, 0.0)),
        _line("9C7", (0.0, 0.0), (133.84, 0.0)),
        _line("9CF", diagonal_start, diagonal_end),
        _line("A29", diagonal_end, (diagonal_end[0] + 50.0, diagonal_end[1])),
    ]


def _stair_shape(code: str) -> ShapeDefinition:
    return ShapeDefinition(
        code=code,
        name=f"СТЪЛБИЩНА ФОРМА {code}",
        state="APPROVED",
        mirror_allowed=True,
        initial_direction="DOWN",
        segments=[
            SegmentDefinition(
                index=1,
                length_code="L1",
                drawing_direction="DOWN",
                bend_after_deg=90.0,
                bend_direction="LEFT",
            ),
            SegmentDefinition(
                index=2,
                length_code="L2",
                bend_after_deg=29.0,
                bend_direction="LEFT",
            ),
            SegmentDefinition(
                index=3,
                length_code="L3",
                bend_after_deg=29.0,
                bend_direction="RIGHT",
            ),
            SegmentDefinition(index=4, length_code="L4"),
        ],
    )


def test_unique_one_gap_bridge_is_auditable() -> None:
    edges = [
        _edge("A", (0.0, 0.0), (10.0, 0.0)),
        _edge("B", (10.0 + REAL_GAP, 0.0), (20.0, 5.0)),
    ]

    bridge = audit_unique_one_gap_bridge(
        edges,
        strict_tolerance_raw=STRICT,
        max_gap_raw=MAX_GAP,
    )

    assert bridge is not None
    assert bridge.edge_handles == ("A", "B")
    assert bridge.gap_raw == pytest.approx(REAL_GAP)
    assert bridge.strict_tolerance_raw == STRICT
    assert bridge.max_gap_raw == MAX_GAP
    assert bridge.strict_component_handles == (("A",), ("B",))


def test_bridge_is_rejected_when_two_endpoint_pairs_are_possible() -> None:
    # Two short parallel edges have two equally valid endpoint bridges.
    edges = [
        _edge("A", (0.0, 0.0), (0.0, 10.0)),
        _edge("B", (1.5, 0.0), (1.5, 10.0)),
    ]

    assert (
        audit_unique_one_gap_bridge(
            edges,
            strict_tolerance_raw=STRICT,
            max_gap_raw=MAX_GAP,
        )
        is None
    )


def test_bridge_is_rejected_for_mixed_layers_or_competing_neighbour() -> None:
    selected = [
        _edge("A", (0.0, 0.0), (10.0, 0.0)),
        _edge("B", (11.4, 0.0), (20.0, 5.0)),
    ]
    mixed = [selected[0], _edge("B", (11.4, 0.0), (20.0, 5.0), layer="OTHER")]
    competitor = _edge("X", (11.6, 0.2), (30.0, 30.0))

    assert (
        audit_unique_one_gap_bridge(
            mixed,
            strict_tolerance_raw=STRICT,
            max_gap_raw=MAX_GAP,
        )
        is None
    )
    assert (
        audit_unique_one_gap_bridge(
            selected,
            strict_tolerance_raw=STRICT,
            max_gap_raw=MAX_GAP,
            neighbourhood_edges=[*selected, competitor],
        )
        is None
    )


def test_real_gap_case_becomes_manual_only_f014_f015_conflict() -> None:
    entities = [_annotation(), *_real_case_lines()]

    analysis = find_geometry_candidates(
        entities[0],
        entities,
        shape_definitions=[_stair_shape("F014"), _stair_shape("F015")],
        policy=RecognitionPolicy(),
    )

    assert analysis.candidates
    top = analysis.candidates[0]
    assert top.handles == ("9C6", "9C7", "9CF", "A29")
    assert top.near_gap_healed is True
    assert top.near_gap_bridge_gap_raw == pytest.approx(REAL_GAP)
    assert set(top.master_ambiguous_shape_codes) == {"F014", "F015"}
    assert set(top.review_reason_codes) >= {"NEAR_GAP_HEALED", "AMBIGUOUS_MASTER"}
    assert "РЪЧНА ПРОВЕРКА" in top.confidence
    assert not top.confidence.startswith("СИЛЕН")
    assert top.geometry_length_raw == pytest.approx(481.84)
    assert top.numeric_difference < RecognitionPolicy().exact_length_tolerance(483.0)

    report_row = _analysis_row(analysis, approved=None)
    assert report_row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert report_row["near_gap_healed"] is True
    assert report_row["near_gap_bridge_gap_raw"] == pytest.approx(REAL_GAP)
    assert report_row["near_gap_bridge_handles"] == ["9C7", "9CF"]
    assert len(report_row["near_gap_bridge_points"]) == 2


def test_gap_recovery_can_be_disabled_without_changing_strict_tolerance() -> None:
    entities = [_annotation(), *_real_case_lines()]
    policy = RecognitionPolicy(near_gap_healing_enabled=False)

    analysis = find_geometry_candidates(
        entities[0],
        entities,
        shape_definitions=[_stair_shape("F015")],
        policy=policy,
    )

    assert policy.endpoint_tolerance_raw == STRICT
    assert not any(candidate.near_gap_healed for candidate in analysis.candidates)
    assert not any(
        candidate.handles == ("9C6", "9C7", "9CF", "A29")
        for candidate in analysis.candidates
    )


def test_manual_assignment_accepts_bridge_but_marks_training_unsafe() -> None:
    summary = summarize_selected_geometry(_real_case_lines())

    assert summary.connected is True
    assert summary.physical_connected is False
    assert summary.component_count == 2
    assert summary.near_gap_healed is True
    assert summary.near_gap_bridge_gap_raw == pytest.approx(REAL_GAP)
    assert summary.training_eligible is False
    assert summary.total_length_cm == pytest.approx(481.84)
    assert summary.segment_count == 4
    assert any("не е подходяща за автоматично обучение" in item for item in summary.warnings)

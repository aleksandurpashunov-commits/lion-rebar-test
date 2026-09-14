from __future__ import annotations

import math
import re

import pytest

from lion_rebar.dxf.batch_geometry_report import (
    _apply_dimension_derived_production_sizes,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.export.auto_gate import (
    auto_gate_eligibility_mode,
    is_deterministic_production_source,
    validate_production_segment_evidence,
)
from lion_rebar.export.excel_export import ExportPosition, _segments_for_link
from lion_rebar.export.master_engine_v031 import (
    parameters_from_segments,
    render_master_svg,
)


F007_PROJECT_004_HANDLES = (
    "6408::BJ-FI-L",
    "6753::BJ-FI-L",
    "5A04::BJ-FI-L",
    "5A0A::BJ-FI-L",
    "5E52::BJ-FI-L",
    "5E58::BJ-FI-L",
    "6402::BJ-FI-L",
    "656C::BJ-FI-L",
    "65D5::BJ-FI-L",
    "6695::BJ-FI-L",
    "6764::BJ-FI-L",
    "6814::BJ-FI-L",
    "681B::BJ-FI-L",
    "6948::BJ-FI-L",
    "6A48::BJ-FI-L",
    "6AE4::BJ-FI-L",
    "720B::BJ-FI-L",
    "669C::BJ-FI-L",
    "68A9::BJ-FI-L",
    "69E5::BJ-FI-L",
)


def _annotation(handle: str, x: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="INSERT",
        layer="ARMTXT",
        geometry={"insert": [x, 0.0, 0.0]},
    )


def _dimension(
    handle: str,
    x: float,
    value: float,
    *,
    defpoints: tuple[tuple[float, float], tuple[float, float]] | None = None,
) -> DxfEntityRecord:
    geometry = {
        "measurement": value,
        "text_midpoint": [x + 1.0, 0.0, 0.0],
    }
    if defpoints is not None:
        geometry["defpoint2"] = [*defpoints[0], 0.0]
        geometry["defpoint3"] = [*defpoints[1], 0.0]
    return DxfEntityRecord(
        handle=handle,
        entity_type="DIMENSION",
        layer="DIM",
        geometry=geometry,
    )


def _contour(handle: str) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="ARM",
        geometry={"points": [[0.0, 0.0], [8.0, 0.0], [8.0, 24.0]]},
    )


def _f007_row(handle: str) -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": "1N6x40",
        "master_shape_code": "F007",
        "declared_length_cm": 40.0,
        "canonical_segment_lengths_cm": [10.0, 20.0, 10.0],
        "canonical_turn_angles_deg": [90.0, 90.0],
        "contextual_rule_code": "F007_ESOVE_CURVED_CENTERLINE",
        "candidate_notes": [],
    }


def test_project_004_all_twenty_f007_rows_keep_the_proven_master_vector() -> None:
    rows = [_f007_row(handle) for handle in F007_PROJECT_004_HANDLES]
    entities: list[DxfEntityRecord] = []
    mismatch_25 = {"6408::BJ-FI-L", "6753::BJ-FI-L"}
    mismatch_15 = {"669C::BJ-FI-L", "68A9::BJ-FI-L", "69E5::BJ-FI-L"}
    for index, handle in enumerate(F007_PROJECT_004_HANDLES):
        x = float(index * 200)
        entities.append(_annotation(handle, x))
        nearby = 25.0 if handle in mismatch_25 else 15.0 if handle in mismatch_15 else 20.0
        entities.append(_dimension(f"D{index}", x, nearby))

    resolved = _apply_dimension_derived_production_sizes(rows, entities)

    assert resolved == 20
    assert {
        tuple(row["production_segments_cm"])
        for row in rows
    } == {(10.0, 20.0, 10.0)}
    assert all(
        row["production_segment_evidence"]["source"]
        == "F007_CONTEXTUAL_MASTER_CANONICAL"
        for row in rows
    )
    assert all("dimension_derived_evidence" not in row for row in rows)


def test_f007_strict_owned_dimensions_remain_above_contextual_canonical() -> None:
    row = _f007_row("STRICT::BJ-FI-L")
    row.update(
        {
            "canonical_segment_lengths_cm": [8.0, 24.0, 8.0],
            "contextual_rule_code": None,
            "dimension_evidence_applied": True,
            "dimension_text_handles": ["D-ARM", "D-TOP"],
            "candidate_handles": ["C-OWNED"],
        }
    )
    entities = [
        _annotation("STRICT::BJ-FI-L", 0.0),
        _contour("C-OWNED"),
        _dimension(
            "D-ARM",
            0.0,
            8.0,
            defpoints=((0.0, 0.0), (8.0, 0.0)),
        ),
        _dimension(
            "D-TOP",
            0.0,
            24.0,
            defpoints=((8.0, 0.0), (8.0, 24.0)),
        ),
    ]

    assert _apply_dimension_derived_production_sizes([row], entities) == 1
    assert row["production_segments_cm"] == [8.0, 24.0, 8.0]
    assert (
        row["production_segment_evidence"]["source"]
        == "STRICT_LOCAL_DIMENSIONS"
    )
    assert row["production_segment_evidence"]["dimension_handles"] == [
        "D-ARM", "D-TOP"
    ]
    assert validate_production_segment_evidence(row)


def test_f007_canonical_provenance_is_a_deterministic_auto_source() -> None:
    row = _f007_row("6408::BJ-FI-L")
    assert _apply_dimension_derived_production_sizes(
        [row],
        [_annotation("6408::BJ-FI-L", 0.0)],
    ) == 1
    assert validate_production_segment_evidence(row)
    assert is_deterministic_production_source(row)
    assert auto_gate_eligibility_mode(row) == "DETERMINISTIC_BLOCK_DERIVED_DIMENSIONS"


def test_f007_contextual_master_canonicalizes_with_zero_dimension_entities() -> None:
    row = _f007_row("NO-DIMS::BJ-FI-L")

    assert _apply_dimension_derived_production_sizes(
        [row],
        [_annotation("NO-DIMS::BJ-FI-L", 0.0)],
    ) == 1
    assert row["production_segments_cm"] == [10.0, 20.0, 10.0]
    assert row["production_segment_evidence"]["source"] == (
        "F007_CONTEXTUAL_MASTER_CANONICAL"
    )
    assert validate_production_segment_evidence(row)


@pytest.mark.parametrize(
    "canonical",
    (
        [0.0, 10.0, 20.0, 10.0],
        [10.0, 0.0, 20.0, 10.0],
        [10.0, float("nan"), 10.0],
        [10.0, float("inf"), 10.0],
    ),
)
def test_f007_raw_canonical_vector_is_never_filtered_into_a_valid_triplet(
    canonical: list[float],
) -> None:
    row = _f007_row("INVALID::BJ-FI-L")
    row["canonical_segment_lengths_cm"] = canonical

    assert _apply_dimension_derived_production_sizes(
        [row],
        [_annotation("INVALID::BJ-FI-L", 0.0)],
    ) == 0
    assert "production_segments_cm" not in row


def test_truthy_or_forged_production_evidence_never_bypasses_matcher() -> None:
    row = _f007_row("FORGED::BJ-FI-L")
    row.update(
        {
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "MANUAL",
            "production_segments_cm": [10.0, 20.0, 10.0],
            "production_segment_evidence": {"source": "TRUTHY"},
        }
    )

    assert not validate_production_segment_evidence(row)
    assert not is_deterministic_production_source(row)
    assert auto_gate_eligibility_mode(row) == "INELIGIBLE"


def test_contextual_evidence_requires_exact_shape_segments_total_and_context() -> None:
    row = _f007_row("PROOF::BJ-FI-L")
    assert _apply_dimension_derived_production_sizes(
        [row],
        [_annotation("PROOF::BJ-FI-L", 0.0)],
    ) == 1
    assert validate_production_segment_evidence(row)

    for key, value in (
        ("master_shape_code", "F003"),
        ("declared_length_cm", 41.0),
        ("contextual_rule_code", "OTHER"),
        ("production_segments_cm", [10.0, 19.0, 10.0]),
    ):
        forged = dict(row)
        forged[key] = value
        assert not validate_production_segment_evidence(forged), key


def _rc32_auto_f007_link() -> dict:
    return {
        "annotation_handle": "6408::BJ-FI-L",
        "annotation_text": "378N6x40",
        "approved_by": "AUTO_BATCH_GATE",
        "auto_gate": {"passed": True, "policy_version": "AUTO_GATE_V2"},
        "contextual_rule_code": "F007_ESOVE_CURVED_CENTERLINE",
        "production_segment_lengths_cm": [7.5, 25.0, 7.5],
        "production_turn_angles_deg": [90.0, 90.0],
        "canonical_segment_lengths_cm": [10.0, 20.0, 10.0],
        "canonical_turn_angles_deg": [90.0, 90.0],
        "geometry_handles": [],
    }


def test_existing_rc32_frozen_auto_f007_exports_the_proven_canonical_vector() -> None:
    shape = ShapeDefinition(code="F007", name="S / C-ОБРАЗНА ФОРМА")
    segments, turns = _segments_for_link(
        _rc32_auto_f007_link(),
        shape=shape,
        entity_map={},
        production_length_cm=40.0,
    )
    assert segments == (10.0, 20.0, 10.0)
    assert turns == (90.0, 90.0)


def test_f007_read_repair_never_overrides_user_or_manual_dimensions() -> None:
    shape = ShapeDefinition(code="F007", name="S / C-ОБРАЗНА ФОРМА")

    user = _rc32_auto_f007_link()
    user["approved_by"] = "USER"
    segments, _turns = _segments_for_link(
        user,
        shape=shape,
        entity_map={},
        production_length_cm=40.0,
    )
    assert segments == (7.5, 25.0, 7.5)

    manual = _rc32_auto_f007_link()
    manual["manual_assignment"] = {
        "production_segment_lengths_cm": [8.0, 24.0, 8.0],
        "turn_angles_deg": [90.0, 90.0],
    }
    segments, _turns = _segments_for_link(
        manual,
        shape=shape,
        entity_map={},
        production_length_cm=40.0,
    )
    assert segments == (8.0, 24.0, 8.0)


def test_project_004_f007_variants_collapse_to_one_total_group_identity() -> None:
    shape = ShapeDefinition(code="F007", name="S / C-ОБРАЗНА ФОРМА")
    raw_vectors = (
        (7.5, 25.0, 7.5),
        (10.0, 20.0, 10.0),
        (12.5, 15.0, 12.5),
    )
    group_keys = set()
    for index, raw_vector in enumerate(raw_vectors):
        link = _rc32_auto_f007_link()
        link["production_segment_lengths_cm"] = list(raw_vector)
        segments, turns = _segments_for_link(
            link,
            shape=shape,
            entity_map={},
            production_length_cm=40.0,
        )
        position = ExportPosition(
            annotation_handle=f"F007-{index}",
            annotation_text="1N6x40",
            count=1,
            diameter_mm=6.0,
            production_length_cm=40.0,
            shape_code="F007",
            shape_name=shape.name,
            shape_revision=1,
            geometry_handles=(),
            frame_index=1,
            segment_lengths_cm=segments,
            turn_angles_deg=turns,
        )
        group_keys.add(position.group_key)

    assert len(group_keys) == 1
    # rc32 had 40 total identities only because this one F007 identity was
    # split into three; removing the two false splits yields 38.
    assert 40 - (len(raw_vectors) - 1) == 38


@pytest.mark.parametrize(
    "annotation_handle",
    ("560C::BJ-FI-L", "5E77::BJ-FI-L", "638D::BJ-FI-L"),
)
def test_project_004_f002_is_canonicalized_to_the_engine_orientation(
    annotation_handle: str,
) -> None:
    # The three position-40 annotations share legend geometry
    # 5514/5515/551C. Its DXF traversal starts at the short arm.
    params = parameters_from_segments(
        "F002",
        (110.000038, 40.574199, 259.627289),
        production_length_cm=410.201526,
        turn_angles_deg=(5.71, -5.72),
    )

    assert annotation_handle
    assert params.expanded_segments_cm == pytest.approx(
        (259.627289, 40.574199, 110.000038)
    )
    assert params.values["L1"] == pytest.approx(259.627289)
    assert params.values["L3"] == pytest.approx(110.000038)
    assert params.values["DX2"] == pytest.approx(40.372879, abs=1e-5)
    assert params.values["DY2"] == pytest.approx(4.036866, abs=1e-5)


def test_f002_loader_and_total_identity_collapse_reversed_dxf_traversal() -> None:
    shape = ShapeDefinition(code="F002", name="ПРЪТ С РЕДУКЦИЯ")
    raw_forward = [259.627289, 40.574199, 110.000038]
    raw_reversed = list(reversed(raw_forward))
    loaded: list[tuple[float, ...]] = []
    positions: list[ExportPosition] = []
    for index, raw in enumerate((raw_forward, raw_reversed)):
        raw_turns = [5.71, -5.72] if index == 0 else [-5.72, 5.71]
        segments, turns = _segments_for_link(
            {
                "annotation_handle": f"F002-{index}",
                "production_segment_lengths_cm": raw,
                "production_turn_angles_deg": raw_turns,
            },
            shape=shape,
            entity_map={},
            production_length_cm=410.201526,
        )
        loaded.append(segments)
        positions.append(
            ExportPosition(
                annotation_handle=f"F002-{index}",
                annotation_text="1N8x410.201526",
                count=1,
                diameter_mm=8.0,
                production_length_cm=410.201526,
                shape_code="F002",
                shape_name=shape.name,
                shape_revision=1,
                geometry_handles=(),
                frame_index=1,
                segment_lengths_cm=tuple(raw),
                turn_angles_deg=turns,
            )
        )

    assert loaded[0] == pytest.approx((259.627289, 40.574199, 110.000038))
    assert loaded[1] == pytest.approx(loaded[0])
    assert positions[0].group_key == positions[1].group_key


def _rebar_polyline_points(svg: str) -> tuple[tuple[float, float], ...]:
    match = re.search(r'<polyline class="rebar-line" points="([^"]+)"', svg)
    assert match
    return tuple(
        tuple(float(value) for value in point.split(","))
        for point in match.group(1).split()
    )


def _locked_f002_fitted_points(values: dict[str, float]) -> tuple[tuple[float, float], ...]:
    """Independent literal port of geometry('F002') + fitGeometry in locked HTML."""

    l1 = float(values["L1"])
    dx = float(values["DX2"])
    dy = float(values["DY2"])
    l3 = float(values["L3"])
    dy_vis = max(abs(dy), max(l1, l3, dx) * 0.08) * (-1.0 if dy < 0 else 1.0)
    raw = ((0.0, 0.0), (l1, 0.0), (l1 + dx, dy_vis), (l1 + dx + l3, dy_vis))
    min_x = min(point[0] for point in raw)
    max_x = max(point[0] for point in raw)
    min_y = min(point[1] for point in raw)
    max_y = max(point[1] for point in raw)
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    scale = min(66.0 / width, 58.0 / height)
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    return tuple(
        (50.0 + (x - center_x) * scale, 50.0 - (y - center_y) * scale)
        for x, y in raw
    )


def _polyline_turn_angle(points: tuple[tuple[float, float], ...], vertex: int) -> float:
    incoming = (
        points[vertex][0] - points[vertex - 1][0],
        points[vertex][1] - points[vertex - 1][1],
    )
    outgoing = (
        points[vertex + 1][0] - points[vertex][0],
        points[vertex + 1][1] - points[vertex][1],
    )
    cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
    dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    return math.degrees(math.atan2(abs(cross), dot))


@pytest.mark.parametrize(
    "values",
    (
        {"L1": 259.627289, "DX2": 40.372879, "DY2": 4.036866, "L3": 110.000038},
        {"L1": 400.0, "DX2": 25.0, "DY2": 8.0, "L3": 75.0},
    ),
)
def test_f002_keeps_the_canonical_reduction_silhouette(
    values: dict[str, float],
) -> None:
    """Силуетът е каноничен от 2026-09-14; пропорциите пак следват размерите.

    Собственик: „приеми формата за желязо с редукция като канонична и
    евентуално само тези 4 cm да са различни, ако някой проектант е дал
    друго." Причината е, че истинският наклон не се вижда: 4 cm при 260 cm
    рамо е под един пиксел във VID клетката, а в чертежа проектантът го
    котира отделно (``i = 1:10``) точно защото не личи.

    Дотук рендерът беше буквален порт на заключения HTML и наклонът идваше
    от DX2/DY2. Това работеше, докато проекциите са записани вярно — но при
    потвърждаване на форма ``turn_angles_deg`` идва 90° по подразбиране,
    проекциите излизат разменени (DX2=0, DY2=40) и рисунката ставаше стъпало
    с прав ъгъл вместо редукция.

    Каноничен е САМО ъгълът на качване. Дължините остават в мащаб, затова
    две различни позиции пак изглеждат различно.
    """

    points = _rebar_polyline_points(render_master_svg("F002", values))

    assert len(points) == 4
    # Одобрената ориентация: дълго рамо долу вляво, качване надясно, късо
    # рамо горе вдясно.
    assert points[0][1] == pytest.approx(points[1][1])
    assert points[2][0] > points[1][0]
    assert points[2][1] < points[1][1]
    assert points[3][1] == pytest.approx(points[2][1])
    # Един и същ ъгъл на качване, независимо от размерите.
    assert _polyline_turn_angle(points, 1) == pytest.approx(
        _polyline_turn_angle(points, 2), abs=1e-6
    )


def test_f002_project_004_has_approved_orientation_turn_and_readable_labels() -> None:
    project_values = {
        "L1": 259.627289,
        "DX2": 40.372879,
        "DY2": 4.036866,
        "L3": 110.000038,
    }
    project_004 = render_master_svg("F002", project_values)
    project_points = _rebar_polyline_points(project_004)
    other_points = _rebar_polyline_points(
        render_master_svg(
            "F002",
            {"L1": 400.0, "DX2": 25.0, "DY2": 8.0, "L3": 75.0},
        )
    )

    # Каноничен е ъгълът, не цялата картинка: различните размери пак дават
    # различна фигура, защото дължините остават в мащаб.
    assert project_points != other_points
    expected_project_points = (
        (17.0000, 50.7939),
        (58.8975, 50.7939),
        (65.2487, 49.2061),
        (83.0000, 49.2061),
    )
    # Approved Engine orientation: long lower-left arm, rise to the right,
    # then the shorter upper-right arm. Both bends show the locked 27.223 deg turn.
    assert project_points[1][0] - project_points[0][0] > (
        project_points[3][0] - project_points[2][0]
    )
    assert project_points[1][1] == pytest.approx(project_points[0][1])
    assert project_points[2][0] > project_points[1][0]
    assert project_points[2][1] < project_points[1][1]
    assert project_points[3][1] == pytest.approx(project_points[2][1])
    # Каноничният ъгъл на качване, един и същ за всяка редукция.
    assert _polyline_turn_angle(project_points, 1) == pytest.approx(
        _polyline_turn_angle(project_points, 2), abs=1e-6
    )

    # 40.57 е ДЪЛЖИНАТА на редукцията (hypot от двете проекции), не
    # хоризонталната ѝ проекция: MASTER формулата сумира точно нея и точно
    # нея реже арматуристът. 4.04 е отместването настрани.
    for label in ("259.63 cm", "40.57 cm", "4.04 cm", "110 cm"):
        assert label in project_004
    assert project_004.count('class="dimension-text"') == 4
    # The four labels have four separate anchors in the schematic profile.
    anchors = re.findall(
        r'<text class="dimension-text" x="([^"]+)" y="([^"]+)"',
        project_004,
    )
    assert len(anchors) == 4
    assert len(set(anchors)) == 4

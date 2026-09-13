from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from lion_rebar.export.excel_export import (
    BeamLabel,
    ExportPosition,
    _assign_beam_label,
    _beam_sections,
    _group_positions,
)
from lion_rebar.dxf.text_parser import parse_rebar_text


FIXTURE = Path(__file__).parent / "fixtures" / "beam_region_ground_truth_v1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_auto_beam_region_detector_matches_all_150_manual_ground_truth_positions() -> None:
    payload = _fixture()
    labels = [BeamLabel(**item) for item in payload["labels"]]
    used = Counter()
    per_beam_texts: dict[str, list[str]] = defaultdict(list)

    for item in payload["positions"]:
        assignment = _assign_beam_label(
            float(item["x"]),
            float(item["y"]),
            1,
            labels,
        )
        assert assignment is not None, item
        assert assignment.label.title == item["expected_beam"], item
        assert assignment.margin >= 0.25
        assert assignment.method == "AUTO_BEAM_REGION_V1"
        used[assignment.label.title] += 1
        per_beam_texts[assignment.label.title].append(item["annotation_text"])

    assert len(payload["positions"]) == payload["expected"]["position_count"] == 150
    assert len(used) == payload["expected"]["beam_count"] == 32
    assert dict(used) == payload["expected"]["beam_counts"]
    assert sorted(per_beam_texts["Гр. B.1 25/34"]) == sorted(
        payload["expected"]["b1_annotations"]
    )


def test_runtime_detector_does_not_require_or_accept_manual_gredi_zones() -> None:
    payload = _fixture()
    assert "zones" not in payload
    assert "Gredi" not in json.dumps(payload["labels"], ensure_ascii=False)
    labels = [BeamLabel(**item) for item in payload["labels"]]
    sample = next(item for item in payload["positions"] if item["annotation_handle"] == "3BD1::BJ-FI-L")
    assignment = _assign_beam_label(sample["x"], sample["y"], 1, labels)
    assert assignment is not None
    assert assignment.label.title == "Гр. B.1 25/34"


def test_beam_working_sheet_keeps_one_row_per_dxf_annotation() -> None:
    positions = [
        ExportPosition(
            "A1",
            "2N12x700",
            2,
            12,
            700,
            "F003",
            "ПРЪТ С ДВА ВИНКЕЛА",
            1,
            ("G1",),
            1,
            (20, 660, 20),
            (),
            beam_name="Гр. B.1 25/34",
            beam_label_handle="B1",
        ),
        ExportPosition(
            "A2",
            "3N12x700",
            3,
            12,
            700,
            "F003",
            "ПРЪТ С ДВА ВИНКЕЛА",
            1,
            ("G2",),
            1,
            (20, 660, 20),
            (),
            beam_name="Гр. B.1 25/34",
            beam_label_handle="B1",
        ),
        ExportPosition(
            "A3",
            "32N8x120",
            32,
            8,
            120,
            "F006",
            "СТРЕМЕ (БИГЛА)",
            1,
            ("G3",),
            1,
            (10, 29, 20, 29, 20, 10),
            (90, 90, 90, 90),
            beam_name="Гр. B.1 25/34",
            beam_label_handle="B1",
        ),
    ]

    sections = _beam_sections(positions)
    assert len(sections) == 1
    assert len(sections[0].groups) == 3
    # One row per annotation; stirrups are intentionally shown first.
    assert [group.positions[0].annotation_handle for group in sections[0].groups] == [
        "A3",
        "A1",
        "A2",
    ]
    assert [group.counts for group in sections[0].groups] == [[32], [2], [3]]

    # Global aggregation remains exact and happens only in "Общо".
    total_groups = _group_positions(positions)
    assert len(total_groups) == 2
    f003_group = next(group for group in total_groups if group.representative.shape_code == "F003")
    f006_group = next(group for group in total_groups if group.representative.shape_code == "F006")
    assert f003_group.counts == [2, 3]
    assert f006_group.counts == [32]


def test_beam_detector_fails_closed_when_margin_is_ambiguous() -> None:
    labels = [
        BeamLabel(1, "B1", "Гр. B.1 25/40", 0.0, 450.0, 25.0),
        BeamLabel(1, "B2", "Гр. B.2 25/40", 100.0, 450.0, 25.0),
    ]
    # Exact horizontal midpoint with identical vertical evidence.
    assert _assign_beam_label(50.0, 100.0, 1, labels) is None


def test_full_ground_truth_builds_32_complete_beam_sections_with_b1_order() -> None:
    payload = _fixture()
    labels = [BeamLabel(**item) for item in payload["labels"]]
    positions: list[ExportPosition] = []

    for item in payload["positions"]:
        parsed = parse_rebar_text(item["annotation_text"])
        assert parsed.count is not None
        assert parsed.diameter_mm is not None
        assert parsed.length_cm is not None
        assignment = _assign_beam_label(float(item["x"]), float(item["y"]), 1, labels)
        assert assignment is not None
        positions.append(
            ExportPosition(
                annotation_handle=item["annotation_handle"],
                annotation_text=item["annotation_text"],
                count=int(parsed.count),
                diameter_mm=float(parsed.diameter_mm),
                production_length_cm=float(parsed.length_cm),
                shape_code=item["shape_code"],
                shape_name=item["shape_code"],
                shape_revision=1,
                geometry_handles=(),
                frame_index=1,
                beam_name=assignment.label.title,
                beam_label_handle=assignment.label.handle,
                beam_assignment_method=assignment.method,
                beam_assignment_score=assignment.score,
                beam_assignment_margin=assignment.margin,
            )
        )

    sections = _beam_sections(positions)
    assert len(sections) == 32
    assert sum(len(section.groups) for section in sections) == 150
    assert all(section.groups for section in sections)
    b1 = next(section for section in sections if section.title == "Гр. B.1 25/34")
    assert [group.representative.annotation_text for group in b1.groups] == payload["expected"][
        "b1_annotations"
    ]

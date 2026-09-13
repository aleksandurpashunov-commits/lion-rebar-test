"""Own proxy geometry satisfies the export gate's geometry requirement (rc44).

Owner-reported 2026-09-13, running rc44 on a fresh project: the Review Center
still showed 31/82 with 51 held back. Measured against the real drawing, 47 of
those 51 were blocked by one check in ``batch_auto_output.py``:

    if not geometry_handles and not approved_link.get(
        "geometry_optional_by_engineer"
    ):
        reasons.append(("MISSING_GEOMETRY", "липсва еднозначна геометрия"))

``geometry_handles`` is read from ``candidate_handles`` -- a MATCHER result.
The arm_koloni/arm_shaibi proxy dialect proves its production shape from the
designer's own declaration and needs no matcher candidate, so that list is
empty by design and the gate reported "no unambiguous geometry".

The report is wrong about those rows: their geometry is the most unambiguous
in the file. ``PXG_<parent>_<n>_0`` is the annotation's OWN exploded proxy,
named after the very proxy that carries the annotation ``PXANN_<parent>`` --
ownership is in the handle itself, with nothing to disambiguate.

Deliberately narrow: only a deterministic proxy source qualifies, and only
handles whose parent matches this annotation's own. A matcher candidate, when
there is one, still takes precedence.
"""
from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.export.batch_auto_output import (
    _own_proxy_geometry_handles,
    build_batch_export_context,
)


def _row(**extra) -> dict:
    """The real "108N8x114" row: no candidate, own geometry, declared shape."""

    row = {
        "annotation_handle": "PXANN_98E88D",
        "annotation_text": "108N8x114",
        "diameter_mm": 8.0,
        "declared_length_cm": 114.0,
        "master_shape_code": "F006",
        "production_segments_cm": [8.0, 31.0, 18.0, 31.0, 18.0, 8.0],
        "candidate_handles": [],
        "reference_geometry_handles": [
            "PXG_98E88D_0_0",
            "PXG_98E88D_1_0",
            "PXG_98E88D_2_0",
            "PXG_98E88D_3_0",
        ],
        "production_segment_evidence": {
            "source": "PROXY_ARC_VERIFIED_DECLARED_SEGMENTS",
            "shape_code": "F006",
            "segments": [8.0, 31.0, 18.0, 31.0, 18.0, 8.0],
            "confidence": "HIGH",
            "bend_arc_count": 5,
            "declared_segments_cm": [18.0, 31.0, 8.0],
        },
        "review_reason_codes": [],
        "matcher_blocker_codes": [],
    }
    row.update(extra)
    return row


def test_the_annotations_own_proxy_geometry_is_accepted() -> None:
    assert _own_proxy_geometry_handles(_row()) == (
        "PXG_98E88D_0_0",
        "PXG_98E88D_1_0",
        "PXG_98E88D_2_0",
        "PXG_98E88D_3_0",
    )


def test_geometry_belonging_to_a_different_proxy_is_refused() -> None:
    """Someone else's exploded geometry is exactly what this gate exists to
    keep out; only the handle's own parent counts."""

    row = _row(reference_geometry_handles=["PXG_98E9DF_0_0", "PXG_98E88D_1_0"])

    assert _own_proxy_geometry_handles(row) == ()


def test_a_plain_dxf_handle_is_refused() -> None:
    row = _row(reference_geometry_handles=["9BD1FC"])

    assert _own_proxy_geometry_handles(row) == ()


def test_a_row_that_is_not_a_deterministic_proxy_source_is_refused() -> None:
    """Without the declared-shape evidence there is nothing proving the shape,
    so the own geometry alone must not clear the gate."""

    row = _row(
        production_segment_evidence={
            "source": "MATCHER_LOCAL_A_E_CANONICAL",
            "shape_code": "F003",
            "segments": [50.0, 140.0, 50.0],
        }
    )

    assert _own_proxy_geometry_handles(row) == ()


def test_a_non_proxy_annotation_is_refused() -> None:
    row = _row(annotation_handle="9AEA90")

    assert _own_proxy_geometry_handles(row) == ()


def test_an_empty_reference_list_yields_nothing() -> None:
    assert _own_proxy_geometry_handles(_row(reference_geometry_handles=[])) == ()
    assert _own_proxy_geometry_handles(_row(reference_geometry_handles=None)) == ()


# --------------------------------------------------------------------------
# Through the real gate, the one that decides what reaches the Excel.
# --------------------------------------------------------------------------


def _engine() -> EnginePackage:
    return EnginePackage(
        engine_id="test-engine",
        title="TEST",
        version="0.3.14",
        production_ready=True,
        shapes=[
            ShapeDefinition(
                code="F001",
                name="ПРАВ ПРЪТ",
                state="LOCKED",
                locked=True,
                segments=[SegmentDefinition(index=1, length_code="L1")],
            ),
            ShapeDefinition(
                code="F006",
                name="СТРЕМЕ (БИГЛА)",
                state="LOCKED",
                locked=True,
                closed=True,
                segments=[
                    SegmentDefinition(index=index, length_code=f"L{index}")
                    for index in range(1, 7)
                ],
            ),
        ],
    )


def _anchor_row() -> dict:
    """An ordinary matcher-verified position, so the gate always has at least
    one export row and reports the others as blockers instead of raising."""

    return {
        "annotation_handle": "T1",
        "annotation_text": "2N8x200",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 2,
        "diameter_mm": 8.0,
        "declared_length_cm": 200.0,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "matcher_disposition": "CONTROL",
        "global_conflict": False,
        "data_quality_flags": [],
        "quantity_review_required": False,
        "master_shape_code": "F001",
        "candidate_handles": ["G1"],
        "candidate_notes": [],
    }


def _entities() -> list[DxfEntityRecord]:
    own = [
        DxfEntityRecord(
            f"PXG_98E88D_{index}_0",
            "LINE",
            "ARM_PROXY_arm_koloni",
            {"start": [100 + index * 10, 500, 0], "end": [110 + index * 10, 500, 0]},
        )
        for index in range(4)
    ]
    return [
        DxfEntityRecord("FRAME", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord(
            "PXANN_98E88D",
            "BLOCK_ATTR_TEXT",
            "arm_koloni",
            {"insert": [100, 500, 0]},
            "108N8x114",
        ),
        *own,
        DxfEntityRecord(
            "T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [400, 500, 0]}, "2N8x200"
        ),
        DxfEntityRecord(
            "G1", "LINE", "Arm - N8", {"start": [400, 500, 0], "end": [600, 500, 0]}
        ),
    ]


def _actionable_row(**extra) -> dict:
    row = _row(
        annotation_class="ACTIONABLE",
        annotation_count=108,
        batch_status="ЗА РЪЧЕН ПРЕГЛЕД",
        matcher_disposition="MANUAL",
        canonical_segment_lengths_cm=[8.0, 31.0, 18.0, 31.0, 18.0, 8.0],
        canonical_turn_angles_deg=[90.0] * 5,
        data_quality_flags=[],
        quantity_review_required=False,
        global_conflict=False,
    )
    row.update(extra)
    return row


def _context(tmp_path: Path, row: dict):
    report = tmp_path / "batch.json"
    report.write_text(
        json.dumps(
            {"cancelled": False, "rows": [_anchor_row(), row]}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    return build_batch_export_context(
        report,
        entities=_entities(),
        engine=_engine(),
        frame_decisions={"FRAME": "VERTICALS"},
    )


def test_a_declared_shape_row_without_a_candidate_reaches_the_excel(
    tmp_path: Path,
) -> None:
    """The 47-position case: proven by the designer's own declaration, no
    matcher candidate, previously refused as "липсва еднозначна геометрия"."""

    context = _context(tmp_path, _actionable_row())

    assert [b.reason_codes for b in context.blockers] == []
    proxy = [p for p in context.positions if p.annotation_handle == "PXANN_98E88D"]
    assert len(proxy) == 1
    assert proxy[0].geometry_handles == (
        "PXG_98E88D_0_0",
        "PXG_98E88D_1_0",
        "PXG_98E88D_2_0",
        "PXG_98E88D_3_0",
    )


def test_a_conflict_over_foreign_geometry_does_not_block_own_geometry(
    tmp_path: Path,
) -> None:
    """``global_conflict`` means one DXF entity is the first candidate of
    several annotations. Real koloni case: 98E9DF is claimed by both
    "54N8x250" and "27N8x260" and owned by neither. Since the row exports its
    OWN proxy geometry rather than that contested entity, the conflict says
    nothing about what we are about to write."""

    context = _context(tmp_path, _actionable_row(global_conflict=True))

    assert [b.reason_codes for b in context.blockers] == []
    assert "PXANN_98E88D" in {p.annotation_handle for p in context.positions}


def test_own_geometry_outranks_a_foreign_matcher_candidate(tmp_path: Path) -> None:
    """Real koloni case "54N8x250": the matcher matched 98E9DF, an entity the
    annotation does not own and shares with "27N8x260".

    The exported quantities and segments come from the declaration, so pairing
    them with a stranger's geometry would put the wrong bar under the position
    in the marked DXF. When the row is a deterministic proxy source, its own
    geometry is the geometry of record and the contested candidate is not
    exported at all -- which is also why the conflict no longer applies."""

    context = _context(
        tmp_path,
        _actionable_row(global_conflict=True, candidate_handles=["98E9DF"]),
    )

    assert [b.reason_codes for b in context.blockers] == []
    proxy = [p for p in context.positions if p.annotation_handle == "PXANN_98E88D"]
    assert proxy[0].geometry_handles == (
        "PXG_98E88D_0_0",
        "PXG_98E88D_1_0",
        "PXG_98E88D_2_0",
        "PXG_98E88D_3_0",
    )


def test_an_engineers_own_choice_of_geometry_still_wins(tmp_path: Path) -> None:
    """An explicit approved link is a human decision and outranks both."""

    row = _actionable_row()
    report = tmp_path / "batch.json"
    report.write_text(
        json.dumps({"cancelled": False, "rows": [_anchor_row(), row]}, ensure_ascii=False),
        encoding="utf-8",
    )
    context = build_batch_export_context(
        report,
        entities=_entities(),
        engine=_engine(),
        frame_decisions={"FRAME": "VERTICALS"},
        approved_links={
            "PXANN_98E88D": {
                "annotation_handle": "PXANN_98E88D",
                "geometry_handles": ["PXG_98E88D_1_0"],
                "approval_status": "ОДОБРЕНО РЪЧНО",
                "approved_by": "USER",
            }
        },
    )

    proxy = [p for p in context.positions if p.annotation_handle == "PXANN_98E88D"]
    assert proxy[0].geometry_handles == ("PXG_98E88D_1_0",)


def test_a_conflict_still_blocks_an_ordinary_matcher_row(tmp_path: Path) -> None:
    """Nothing here is relaxed for a row without the declared-shape evidence:
    it exports the contested entity, so the conflict is exactly the right
    reason to hold it back."""

    contested = dict(_anchor_row())
    contested["annotation_handle"] = "T2"
    contested["global_conflict"] = True

    report = tmp_path / "batch.json"
    report.write_text(
        json.dumps(
            {"cancelled": False, "rows": [_anchor_row(), contested]}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    context = build_batch_export_context(
        report,
        entities=[
            *_entities(),
            DxfEntityRecord(
                "T2", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [700, 500, 0]}, "2N8x200"
            ),
        ],
        engine=_engine(),
        frame_decisions={"FRAME": "VERTICALS"},
    )

    blocked = {b.annotation_handle: b.reason_codes for b in context.blockers}
    assert "OWNERSHIP_CONFLICT" in blocked["T2"]

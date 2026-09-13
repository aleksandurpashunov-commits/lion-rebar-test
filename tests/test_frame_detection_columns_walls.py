from __future__ import annotations

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.frame_detection import (
    CATEGORY_SHEET_NAMES,
    detect_drawing_frames,
    unresolved_frames,
)


def _text(handle: str, text: str, *, insert, layer: str) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer=layer,
        geometry={"insert": list(insert), "height": 25.0},
        text=text,
    )


def _frame_insert(handle: str, *, insert) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="INSERT",
        layer="ramka_1",
        geometry={"insert": list(insert)},
    )


def test_koloni_single_sheet_title_resolves_as_columns_frame() -> None:
    """Owner-confirmed 2026-09-12: BEAM_FREEZE_NO_FRAMES hard-blocked Final
    Control/Export for EVERY koloni/shaibi project from the second designer,
    despite each sheet having a real, unambiguous title -- just with words
    ("КОЛОНИ"/"ШАЙБИ") this vocabulary didn't know. Verified against the
    REAL koloni DXF: exactly one title on layer "$Ramka" reading
    "АРМИРОВКА КОЛОНИ ОТ КОТА +18,18 ДО КОТА +21,48" plus exactly one
    title-block INSERT on layer "ramka_1" (already matched by
    _is_frame_layer) -- real, sufficient single-sheet evidence."""

    entities = [
        _text("A1", "1:50", insert=(0.0, 1000.0, 0.0), layer="$Ramka"),
        _text(
            "A2",
            "АРМИРОВКА КОЛОНИ ОТ КОТА +18,18 ДО КОТА +21,48",
            insert=(0.0, 950.0, 0.0),
            layer="$Ramka",
        ),
        _text(
            "A3",
            "COLUMNS REINFORCEMENT FROM  +18,18 TO +21,48",
            insert=(0.0, 900.0, 0.0),
            layer="$Ramka",
        ),
        _frame_insert("B1", insert=(500.0, 500.0, 0.0)),
    ]

    frames = detect_drawing_frames(entities)

    assert len(frames) == 1
    frame = frames[0]
    assert frame.category_code == "COLUMNS"
    assert frame.suggested_sheet_name == CATEGORY_SHEET_NAMES["COLUMNS"]
    assert frame.resolution_reason == "SINGLE_SHEET_TITLE_AND_FRAME_INSERT"
    assert not frame.is_unknown
    assert not unresolved_frames(frames)


def test_shaibi_single_sheet_title_resolves_as_walls_frame() -> None:
    entities = [
        _text(
            "A1",
            "АРМИРОВКА ШАЙБИ ОТ КОТА +18,18 ДО КОТА +21,48",
            insert=(0.0, 950.0, 0.0),
            layer="$Ramka",
        ),
        _frame_insert("B1", insert=(500.0, 500.0, 0.0)),
    ]

    frames = detect_drawing_frames(entities)

    assert len(frames) == 1
    assert frames[0].category_code == "WALLS"
    assert frames[0].suggested_sheet_name == CATEGORY_SHEET_NAMES["WALLS"]
    assert not unresolved_frames(frames)


def test_columns_title_without_frame_insert_stays_unresolved() -> None:
    """Fail-closed: the single-sheet fallback still requires real
    corroborating evidence (a title-block INSERT), not the title text
    alone -- unchanged safety property, just extended vocabulary."""

    entities = [
        _text(
            "A1",
            "АРМИРОВКА КОЛОНИ ОТ КОТА +18,18 ДО КОТА +21,48",
            insert=(0.0, 950.0, 0.0),
            layer="$Ramka",
        ),
    ]

    frames = detect_drawing_frames(entities)

    assert frames == []


def test_existing_beams_and_verticals_titles_still_classify_unchanged() -> None:
    """Regression guard: the new COLUMNS/WALLS entries must not shadow or
    reorder the existing BEAMS/VERTICALS single-sheet patterns."""

    beams_entities = [
        _text(
            "A1",
            "АРМИРОВКА НА ГРЕДИ НА КОТА +11,90",
            insert=(0.0, 950.0, 0.0),
            layer="$Ramka",
        ),
        _frame_insert("B1", insert=(500.0, 500.0, 0.0)),
    ]
    frames = detect_drawing_frames(beams_entities)
    assert len(frames) == 1
    assert frames[0].category_code == "BEAMS"

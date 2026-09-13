from __future__ import annotations

from lion_rebar.dxf.batch_geometry_report import (
    _apply_named_u_subtype_context,
    _named_u_subtype_code,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import ShapeDefinition


def _shapes() -> tuple[ShapeDefinition, ...]:
    return (
        ShapeDefinition(
            code="F003",
            name="ПРЪТ С ДВА ВИНКЕЛА",
            state="LOCKED",
            locked=True,
        ),
        ShapeDefinition(
            code="F005",
            name="ФИБА",
            state="LOCKED",
            locked=True,
            symmetric=True,
        ),
    )


def _context(text: str, *, count: int = 32, diameter: float = 8.0) -> dict:
    return {
        "annotation_handle": "CTX",
        "annotation_text": text,
        "annotation_class": "MISSING_LENGTH",
        "annotation_count": count,
        "diameter_mm": diameter,
        "candidate_notes": [],
    }


def _u_row(
    handle: str,
    *,
    count: int = 32,
    diameter: float = 8.0,
    segments: list[float] | None = None,
) -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": f"{count}N{diameter:g}x94",
        "annotation_class": "ACTIONABLE",
        "annotation_count": count,
        "diameter_mm": diameter,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_confidence": "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА",
        "candidate_handles": [f"{handle}-1", f"{handle}-2", f"{handle}-3"],
        "canonical_segment_lengths_cm": segments or [40.0, 14.0, 40.0],
        "master_shape_code": "F003",
        "master_shape_name": "ПРЪТ С ДВА ВИНКЕЛА",
        "master_conflict": True,
        "master_ambiguous_shape_codes": ["F003", "F005"],
        "review_reason_codes": ["AMBIGUOUS_MASTER"],
        "candidate_notes": [],
        "analysis_warnings": [],
        "global_conflict": False,
        "geometry_owner_conflict": False,
        "near_gap_healed": False,
        "mirror_requires_review": False,
        "quantity_review_required": False,
        "data_quality_flags": [],
    }


def test_explicit_fiba_context_resolves_f005_and_only_master_ambiguity() -> None:
    context = _context("фиби 32N8/s=20")
    first = _u_row("A")
    second = _u_row("B")
    rows = [first, second, context]

    _apply_named_u_subtype_context(
        rows,
        shape_definitions=_shapes(),
        policy=RecognitionPolicy(),
    )

    assert {first["master_shape_code"], second["master_shape_code"]} == {"F005"}
    assert first["master_shape_name"] == "ФИБА"
    assert first["master_ambiguous_shape_codes"] == []
    assert first["master_conflict"] is False
    assert first["batch_status"] == "СИЛЕН ПО ГЕОМЕТРИЯ"
    assert first["contextual_rule_code"] == "EXPLICIT_FIBA_TEXT_F005"
    assert context["named_u_subtype_context_target"] == "F005"


def test_named_context_does_not_guess_when_count_and_diameter_have_two_u_signatures() -> None:
    context = _context("фиби 32N8/s=20")
    first = _u_row("A", segments=[40.0, 14.0, 40.0])
    second = _u_row("B", segments=[50.0, 20.0, 50.0])

    _apply_named_u_subtype_context(
        [first, second, context],
        shape_definitions=_shapes(),
        policy=RecognitionPolicy(),
    )

    assert first["master_shape_code"] == "F003"
    assert first["master_conflict"] is True
    assert "не водят до една-единствена U-сигнатура" in context["candidate_notes"][-1]


def test_open_u_text_selects_f003_but_geometry_only_remains_ambiguous() -> None:
    assert _named_u_subtype_code("ОТВОРЕН ДЕТАЙЛ 12N10") == "F003"
    assert _named_u_subtype_code("U-форма 12N10") == "F003"
    assert _named_u_subtype_code("12N10x120") is None

    context = _context("отворен детайл 32N8/s=20")
    row = _u_row("A")
    _apply_named_u_subtype_context(
        [row, context],
        shape_definitions=_shapes(),
        policy=RecognitionPolicy(),
    )
    assert row["master_shape_code"] == "F003"
    assert row["contextual_rule_code"] == "EXPLICIT_OPEN_U_TEXT_F003"


def test_ownership_conflict_is_not_promoted_by_named_context() -> None:
    context = _context("фиби 32N8/s=20")
    row = _u_row("A")
    row["global_conflict"] = True

    _apply_named_u_subtype_context(
        [row, context],
        shape_definitions=_shapes(),
        policy=RecognitionPolicy(),
    )

    assert row["master_shape_code"] == "F005"
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"


def test_named_context_can_use_unique_report_signature_when_scaled_geometry_has_no_segments() -> None:
    context = _context("фиби 32N8/s=20")
    row = _u_row("A")
    row["canonical_segment_lengths_cm"] = []
    row["declared_length_cm"] = 94.0
    row["segment_count"] = 3
    row["canonical_signature"] = "O|n=3|r=0.43011,0.13978,0.43011|a=-90,-90"

    _apply_named_u_subtype_context(
        [row, context],
        shape_definitions=_shapes(),
        policy=RecognitionPolicy(),
    )

    assert row["master_shape_code"] == "F005"
    assert row["named_u_subtype_context_resolved"] is True

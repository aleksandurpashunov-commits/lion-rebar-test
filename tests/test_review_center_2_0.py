"""Review Center 2.0 — тестове за P1-3, P1-4, P1-5."""
from __future__ import annotations

from types import SimpleNamespace

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.apply_to_similar import (
    MAX_SIMILAR_TARGETS,
    SimilarApplyDisabled,
    assert_similar_enabled,
    build_similar_plan,
    similar_signature,
)
from lion_rebar.review.shape_selection import (
    available_shapes,
    refresh_segments_for_shape,
    search_shapes,
    shape_preview,
)

IDENT = DataIdentity("P1", "a" * 64, "G1", "R1")


def _shape(code, name, count, active=True, state="ACTIVE"):
    return SimpleNamespace(code=code, name=name, active=active, state=state,
                           recognition=SimpleNamespace(segment_count=count))


def _engine(*shapes):
    return SimpleNamespace(shapes=tuple(shapes))


def _row(handle, **over):
    row = {
        "annotation_handle": handle, "annotation_text": "25N8x135",
        "annotation_count": "25", "diameter_mm": "8", "declared_length_cm": "135",
        "master_shape_code": "F006", "candidate_handles": f"G{handle}",
        "matcher_blocker_codes": "HOOKED_CLOSURE", "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "review_group_key": "K1", "project_id": "P1", "source_sha256": "a" * 64,
        "source_generation_id": "G1", "analysis_run_id": "R1",
    }
    row.update(over)
    return row


# ------------------------------------------------- P1-4 списък и търсене

def test_all_active_shapes_are_offered_not_only_f001_f008():
    engine = _engine(*[_shape(f"F{i:03d}", f"Форма {i}", 3) for i in range(1, 20)])
    codes = [c.code for c in available_shapes(engine)]
    assert len(codes) == 19
    assert "F019" in codes and "F013" in codes


def test_retired_shapes_are_hidden_by_default():
    engine = _engine(_shape("F001", "Прав", 1),
                     _shape("F005", "Оттеглена", 3, active=True, state="RETIRED"))
    assert [c.code for c in available_shapes(engine)] == ["F001"]
    assert len(available_shapes(engine, include_retired=True)) == 2


def test_inactive_shapes_are_hidden():
    engine = _engine(_shape("F001", "Прав", 1), _shape("F009", "Неактивна", 3,
                                                       active=False))
    assert [c.code for c in available_shapes(engine)] == ["F001"]


def test_search_by_exact_code():
    engine = _engine(_shape("F001", "Прав прът", 1), _shape("F006", "Стреме", 6))
    assert [c.code for c in search_shapes(available_shapes(engine), "F006")] == ["F006"]
    assert [c.code for c in search_shapes(available_shapes(engine), "f006")] == ["F006"]


def test_search_by_name_fragment_and_multiple_tokens():
    engine = _engine(_shape("F003", "Прът с два винкела", 3),
                     _shape("F004", "Прът с един винкел", 2),
                     _shape("F006", "Стреме бигла", 6))
    choices = available_shapes(engine)
    assert [c.code for c in search_shapes(choices, "стреме")] == ["F006"]
    assert [c.code for c in search_shapes(choices, "два винкела")] == ["F003"]


def test_empty_query_returns_everything():
    engine = _engine(_shape("F001", "Прав", 1), _shape("F006", "Стреме", 6))
    assert len(search_shapes(available_shapes(engine), "   ")) == 2


def test_every_shape_has_a_preview():
    engine = _engine(*[_shape(f"F{i:03d}", f"Форма {i}", i % 6 + 1) for i in range(1, 20)])
    assert all(c.preview for c in available_shapes(engine))
    assert shape_preview("F006", 6)


# ------------------------------------------------- P1-5 segment refresh

def test_same_shape_keeps_segments_untouched():
    result = refresh_segments_for_shape(
        previous_shape_code="F003", new_shape_code="F003",
        current_segments=[650, 50], production_length_cm=700,
        new_shape_segment_count=2)
    assert not result.cleared and result.segments == (650.0, 50.0)


def test_shape_change_clears_foreign_segments():
    """Ядрото на P1-5: F003 сегменти не бива да оцелеят като F006."""

    result = refresh_segments_for_shape(
        previous_shape_code="F003", new_shape_code="F006",
        current_segments=[650, 50], production_length_cm=700,
        new_shape_segment_count=6)
    assert result.cleared
    assert result.segments == ()
    assert "SEGMENT_COUNT_MISMATCH" in result.reason


def test_shape_change_clears_when_total_does_not_match():
    result = refresh_segments_for_shape(
        previous_shape_code="F003", new_shape_code="F004",
        current_segments=[650, 50], production_length_cm=900,
        new_shape_segment_count=2)
    assert result.cleared and "TOTAL_MISMATCH" in result.reason


def test_shape_change_keeps_verified_segments():
    result = refresh_segments_for_shape(
        previous_shape_code="F003", new_shape_code="F004",
        current_segments=[650, 50], production_length_cm=700,
        new_shape_segment_count=2)
    assert not result.cleared and result.reason == "CARRIED_OVER_VERIFIED"


def test_unknown_target_segment_count_does_not_carry_over():
    result = refresh_segments_for_shape(
        previous_shape_code="F003", new_shape_code="F013",
        current_segments=[650, 50], production_length_cm=700,
        new_shape_segment_count=0)
    assert result.cleared and result.reason == "UNKNOWN_TARGET_SEGMENT_COUNT"


def test_f001_gets_single_segment_from_length():
    result = refresh_segments_for_shape(
        previous_shape_code="F006", new_shape_code="F001",
        current_segments=[10, 15, 160, 15, 160, 10], production_length_cm=1200)
    assert result.segments == (1200.0,) and not result.cleared


def test_no_segments_to_carry_is_cleared():
    result = refresh_segments_for_shape(
        previous_shape_code="F003", new_shape_code="F006",
        current_segments=[], production_length_cm=700, new_shape_segment_count=6)
    assert result.cleared and result.reason == "NO_SEGMENTS_TO_CARRY"


# ------------------------------------------------- P1-3 Apply to All Similar

def test_feature_is_enabled_by_default_since_owner_approval():
    """Собственикът съзнателно включи функцията на 2026-09-09 (Фаза 2), след
    като preview/cap/undo защитите бяха довършени и покрити с тестове."""

    plan = build_similar_plan(_row("A1"), [_row("A1"), _row("A2")], IDENT,
                              changes={"master_shape_code": "F013"})
    assert plan.enabled
    assert plan.can_apply


def test_explicitly_disabled_plan_blocks_apply():
    """Функцията може все още да се изключи изрично (напр. бъдеща настройка)."""

    plan = build_similar_plan(_row("A1"), [_row("A1"), _row("A2")], IDENT,
                              changes={"master_shape_code": "F013"}, enabled=False)
    assert not plan.enabled
    assert plan.refusal == "FEATURE_DISABLED"
    assert not plan.can_apply


def test_disabled_plan_still_previews_what_would_happen():
    plan = build_similar_plan(_row("A1"), [_row("A1"), _row("A2"), _row("A3")], IDENT,
                              changes={"master_shape_code": "F013"}, enabled=False)
    assert plan.affected_count == 2
    assert not plan.can_apply


def test_enabled_plan_can_apply():
    plan = build_similar_plan(_row("A1"), [_row("A1"), _row("A2")], IDENT,
                              changes={"master_shape_code": "F013"}, enabled=True)
    assert plan.can_apply and plan.affected_count == 1


def test_signature_ignores_geometry_but_keeps_everything_else():
    a = similar_signature(_row("A1", candidate_handles="G1"), IDENT)
    b = similar_signature(_row("A2", candidate_handles="G1,G2,G3"), IDENT)
    assert a == b
    c = similar_signature(_row("A3", diameter_mm="10"), IDENT)
    assert a != c


def test_resolved_positions_are_not_touched():
    plan = build_similar_plan(
        _row("A1"), [_row("A1"), _row("A2", batch_status="ОДОБРЕНА ВРЪЗКА")],
        IDENT, changes={"master_shape_code": "F013"}, enabled=True)
    excluded = [t for t in plan.excluded if t.annotation_handle == "A2"][0]
    assert excluded.reason == "ALREADY_RESOLVED"


def test_ownership_conflict_is_never_included():
    plan = build_similar_plan(
        _row("A1"), [_row("A1"), _row("A2", global_conflict=True)],
        IDENT, changes={"master_shape_code": "F013"}, enabled=True)
    assert [t.reason for t in plan.excluded if t.annotation_handle == "A2"] == [
        "OWNERSHIP_CONFLICT"]


def test_position_with_different_provenance_is_not_a_candidate_at_all():
    """Без геометрия -> друг provenance class -> изобщо не е същата позиция."""

    plan = build_similar_plan(
        _row("A1"), [_row("A1"), _row("A2", candidate_handles="")],
        IDENT, changes={"master_shape_code": "F013"}, enabled=True)
    assert plan.affected_count == 0
    assert not any(t.annotation_handle == "A2" for t in plan.targets)


def test_position_without_geometry_is_never_included():
    """Достижимо, когато и representative-ът е без геометрия: пак се отказва."""

    representative = _row("A1", candidate_handles="")
    plan = build_similar_plan(
        representative, [representative, _row("A2", candidate_handles="")],
        IDENT, changes={"master_shape_code": "F013"}, enabled=True)
    assert [t.reason for t in plan.excluded if t.annotation_handle == "A2"] == [
        "NO_GEOMETRY"]
    assert plan.affected_count == 0


def test_foreign_identity_is_excluded():
    plan = build_similar_plan(
        _row("A1"), [_row("A1"), _row("A2", source_generation_id="G2")],
        IDENT, changes={"master_shape_code": "F013"}, enabled=True)
    assert any(t.reason == "SOURCE_GENERATION_MISMATCH" for t in plan.excluded)


def test_scope_cap_refuses_instead_of_silently_applying():
    rows = [_row("A1")] + [_row(f"B{i}") for i in range(MAX_SIMILAR_TARGETS + 5)]
    plan = build_similar_plan(_row("A1"), rows, IDENT,
                              changes={"master_shape_code": "F013"}, enabled=True)
    assert "SCOPE_TOO_LARGE" in plan.refusal
    assert not plan.can_apply


def test_assert_similar_enabled_raises_when_disabled():
    plan = build_similar_plan(_row("A1"), [_row("A1"), _row("A2")], IDENT,
                              changes={"master_shape_code": "F013"}, enabled=False)
    try:
        assert_similar_enabled(plan)
    except SimilarApplyDisabled:
        return
    raise AssertionError("изключената функция трябва да вдига изключение")


def test_plan_reports_affected_group_count():
    rows = [_row("A1"), _row("A2"), _row("A3", review_group_key="K2")]
    plan = build_similar_plan(_row("A1"), rows, IDENT,
                              changes={"master_shape_code": "F013"}, enabled=True)
    assert plan.affected_count == 2
    assert plan.affected_groups == 2

"""Приемането на предложението на програмата не иска обяснение.

ПРАВИЛО (Product Owner): когато позицията е изцяло вярна, операторът
потвърждава и продължава. Обяснение се иска само при ПРОМЯНА.

Досега приемането на предложение се класифицираше като корекция, защото
редът нямаше собствени сегменти — и диалогът отказваше запис с
„При корекция/override опиши какво е променено“.
"""
from __future__ import annotations

from pathlib import Path

from lion_rebar.engineer_decision import (
    ACTION_CONFIRMATION,
    ACTION_CORRECTION,
    classify_engineer_action,
    engineer_note_policy,
)

STIRRUP = [10.0, 20.0, 25.0, 20.0, 25.0, 10.0]


def _classify(**over):
    arguments = {
        "reason_code": "CONFIRMATION",
        "suggested_shape_code": "F006", "resolved_shape_code": "F006",
        "original_count": 56, "resolved_count": 56,
        "original_diameter_mm": 8, "resolved_diameter_mm": 8,
        "original_length_cm": 115, "resolved_length_cm": 115,
        "suggested_segments": STIRRUP, "resolved_segments": STIRRUP,
        "suggested_geometry_handles": ["G1"], "resolved_geometry_handles": ["G1"],
    }
    arguments.update(over)
    return classify_engineer_action(**arguments)


def test_accepting_the_suggestion_unchanged_is_a_confirmation():
    assert _classify() == ACTION_CONFIRMATION


def test_changed_segments_still_require_a_note():
    assert _classify(
        resolved_segments=[10.0, 20.0, 30.0, 20.0, 30.0, 10.0]) == ACTION_CORRECTION


def test_changed_count_still_requires_a_note():
    assert _classify(resolved_count=57) == ACTION_CORRECTION


def test_changed_diameter_still_requires_a_note():
    assert _classify(resolved_diameter_mm=10) == ACTION_CORRECTION


def test_changed_length_still_requires_a_note():
    assert _classify(resolved_length_cm=120) == ACTION_CORRECTION


def test_changed_geometry_still_requires_a_note():
    assert _classify(resolved_geometry_handles=["G2"]) != ACTION_CONFIRMATION


def test_program_suggestion_counts_as_suggested():
    """Редът носи предложението в suggested_segments_cm."""

    source = Path("lion_rebar/engineer_decision.py").read_text(encoding="utf-8")
    assert 'row.get("suggested_segments_cm")' in source


def test_dialog_sends_the_suggestion_as_original():
    dialog = Path("lion_rebar/ui/engineer_decision_dialog.py").read_text(
        encoding="utf-8")
    assert 'self.row.get("suggested_shape_code")' in dialog
    assert 'self.row.get("suggested_segments_cm")' in dialog


def test_structured_valid_correction_does_not_require_free_text():
    policy = engineer_note_policy(
        action_class=ACTION_CORRECTION,
        reason_code="WRONG_SEGMENTS",
        geometry_handles=["G1"],
    )
    assert not policy.required
    assert policy.code == "STRUCTURED_RECORD_SUFFICIENT"


def test_safety_exceptions_still_require_free_text():
    no_geometry = engineer_note_policy(
        action_class=ACTION_CORRECTION,
        reason_code="NO_GEOMETRY",
        geometry_handles=[],
    )
    other = engineer_note_policy(
        action_class=ACTION_CORRECTION,
        reason_code="OTHER",
        geometry_handles=["G1"],
    )
    exclusion = engineer_note_policy(
        action_class="EXCLUSION",
        reason_code="NON_PRODUCTION_POSITION",
        geometry_handles=[],
    )
    assert no_geometry.required and no_geometry.acknowledgement_required
    assert other.required
    assert exclusion.required and exclusion.acknowledgement_required

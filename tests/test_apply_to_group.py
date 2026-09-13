"""P1-2 Apply to Group — regression тестове.

Централното твърдение, което трябва да е невъзможно да се наруши:
решение НЕ се копира само защото текстът е еднакъв.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.apply_to_group import (
    APPLICABLE_FIELDS,
    active_group_operations,
    apply_signature,
    build_group_plan,
    geometry_class,
    load_group_operations,
    problem_class,
    provenance_class,
    record_group_operation,
    record_group_undo,
    signature_fields,
)

IDENT = DataIdentity("P1", "a" * 64, "G1", "R1")


def _row(handle: str, **over):
    row = {
        "annotation_handle": handle, "annotation_text": "25N8x135",
        "annotation_count": "25", "diameter_mm": "8", "declared_length_cm": "135",
        "master_shape_code": "F006", "candidate_handles": f"G{handle}",
        "matcher_blocker_codes": "HOOKED_CLOSURE", "review_group_key": "K1",
        "project_id": "P1", "source_sha256": "a" * 64,
        "source_generation_id": "G1", "analysis_run_id": "R1",
    }
    row.update(over)
    return row


def _plan(members, **changes):
    rep = members[0]
    return build_group_plan(rep, members, IDENT,
                            changes=changes or {"master_shape_code": "F013"})


# ---------------------------------------------------- подпис

def test_signature_has_all_ten_contract_components():
    fields = signature_fields(_row("A1"), IDENT)
    for name in ("project_id", "source_generation_id", "annotation_text", "count",
                 "diameter_mm", "length_cm", "master_shape_code", "problem_class",
                 "geometry_class", "provenance_class"):
        assert name in fields, name


def test_identical_rows_share_signature():
    assert apply_signature(_row("A1"), IDENT) == apply_signature(_row("A2"), IDENT)


def test_numeric_formatting_does_not_split_a_group():
    """135, 135.0 и 135.000 са една и съща дължина."""

    base = apply_signature(_row("A1", declared_length_cm="135"), IDENT)
    for variant in ("135.0", "135.000", " 135 "):
        assert apply_signature(_row("A2", declared_length_cm=variant), IDENT) == base


def test_same_text_but_different_diameter_is_not_equivalent():
    """Ядрото на договора: еднакъв текст НЕ значи еднаква позиция."""

    a = apply_signature(_row("A1", diameter_mm="8"), IDENT)
    b = apply_signature(_row("A2", diameter_mm="10"), IDENT)
    assert a != b


def test_different_problem_code_is_not_equivalent():
    a = apply_signature(_row("A1"), IDENT)
    b = apply_signature(_row("A2", matcher_blocker_codes="LENGTH_MISMATCH"), IDENT)
    assert a != b


def test_geometry_class_distinguishes_missing_and_conflicted():
    assert geometry_class(_row("A1", candidate_handles="")) == "NO_GEOMETRY"
    assert geometry_class(_row("A1", global_conflict=True)) == "CONFLICTED"
    assert geometry_class(_row("A1", candidate_handles="G1,G2")) == "HANDLES:2"


def test_provenance_class_falls_back_safely():
    assert provenance_class(_row("A1", candidate_handles="")) == "NONE"
    assert provenance_class(_row("A1")) == "DXF_HANDLES"
    assert provenance_class(
        _row("A1", geometry_provenance="none_engineer_override")) == "NONE_ENGINEER_OVERRIDE"


def test_problem_class_is_order_independent():
    a = problem_class(_row("A1", matcher_blocker_codes="B,A"))
    b = problem_class(_row("A2", matcher_blocker_codes="A, B"))
    assert a == b


# ---------------------------------------------------- план

def test_representative_is_never_counted_as_affected():
    plan = _plan([_row("A1"), _row("A2")])
    assert plan.affected_count == 1
    assert plan.members[0].reason == "REPRESENTATIVE"


def test_mismatched_member_is_excluded_with_named_field():
    plan = _plan([_row("A1"), _row("A2", diameter_mm="10")])
    excluded = [m for m in plan.excluded if m.annotation_handle == "A2"][0]
    assert excluded.reason.startswith("SIGNATURE_MISMATCH")
    assert "diameter_mm" in excluded.reason


def test_foreign_identity_member_is_excluded():
    plan = _plan([_row("A1"), _row("A2", source_generation_id="G2")])
    excluded = [m for m in plan.excluded if m.annotation_handle == "A2"][0]
    assert excluded.reason == "SOURCE_GENERATION_MISMATCH"


def test_plan_preview_exposes_everything_the_operator_must_see():
    plan = _plan([_row("A1"), _row("A2")])
    payload = plan.to_dict()
    assert payload["affected_count"] == 1
    assert payload["signature"]["master_shape_code"] == "F006"
    member = payload["members"][1]
    for key in ("annotation_handle", "annotation_text", "diameter_mm", "length_cm",
                "master_shape_code", "problem_class", "geometry_handles"):
        assert key in member


def test_only_applicable_fields_are_carried():
    plan = build_group_plan(_row("A1"), [_row("A1"), _row("A2")], IDENT,
                            changes={"master_shape_code": "F013",
                                     "batch_status": "СИЛЕН",
                                     "annotation_handle": "HACK"})
    assert set(plan.changes) <= set(APPLICABLE_FIELDS)
    assert "batch_status" not in plan.changes
    assert "annotation_handle" not in plan.changes


def test_plan_cannot_apply_without_changes():
    plan = build_group_plan(_row("A1"), [_row("A1"), _row("A2")], IDENT, changes={})
    assert not plan.can_apply


def test_plan_cannot_apply_without_eligible_members():
    plan = _plan([_row("A1"), _row("A2", diameter_mm="10")])
    assert plan.affected_count == 0
    assert not plan.can_apply


# ---------------------------------------------------- одит и обратимост

def test_operation_requires_author_and_note():
    with tempfile.TemporaryDirectory() as d:
        plan = _plan([_row("A1"), _row("A2")])
        for bad in ({"applied_by": ""}, {"note": ""}):
            kwargs = {"applied_by": "инженер", "note": "мотив", "identity": IDENT}
            kwargs.update(bad)
            try:
                record_group_operation(Path(d), plan, **kwargs)
            except ValueError:
                continue
            raise AssertionError(f"очаква се отказ за {bad}")


def test_operation_is_journalled_with_before_state():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        plan = _plan([_row("A1"), _row("A2")])
        before = {"A2": {"master_shape_code": "F006"}}
        operation_id = record_group_operation(
            root, plan, applied_by="инженер", note="еднакви стремена",
            identity=IDENT, before_state=before)
        rows = load_group_operations(root)
        assert len(rows) == 1
        entry = rows[0]
        assert entry["operation_id"] == operation_id
        assert entry["affected_handles"] == ["A2"]
        assert entry["before_state"]["A2"]["master_shape_code"] == "F006"
        assert entry["project_id"] == "P1"
        assert entry["note"] == "еднакви стремена"


def test_excluded_members_are_recorded_in_the_journal():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        plan = _plan([_row("A1"), _row("A2"), _row("A3", diameter_mm="10")])
        record_group_operation(root, plan, applied_by="и", note="мотив", identity=IDENT)
        entry = load_group_operations(root)[0]
        excluded = {item["annotation_handle"] for item in entry["excluded"]}
        assert "A3" in excluded


def test_operation_can_be_undone_once():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        plan = _plan([_row("A1"), _row("A2")])
        operation_id = record_group_operation(
            root, plan, applied_by="и", note="мотив", identity=IDENT,
            before_state={"A2": {"master_shape_code": "F006"}})
        assert active_group_operations(root)
        assert record_group_undo(root, operation_id=operation_id, undone_by="и")
        assert active_group_operations(root) == []
        assert not record_group_undo(root, operation_id=operation_id, undone_by="и")


def test_undo_of_unknown_operation_is_refused():
    with tempfile.TemporaryDirectory() as d:
        assert not record_group_undo(Path(d), operation_id="nope", undone_by="и")


def test_journal_is_append_only():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        plan = _plan([_row("A1"), _row("A2")])
        first = record_group_operation(root, plan, applied_by="и", note="1",
                                       identity=IDENT)
        record_group_undo(root, operation_id=first, undone_by="и", note="грешка")
        second = record_group_operation(root, plan, applied_by="и", note="2",
                                        identity=IDENT)
        rows = load_group_operations(root)
        assert [r["action"] for r in rows] == [
            "APPLY_TO_GROUP", "UNDO_GROUP", "APPLY_TO_GROUP"]
        assert first != second

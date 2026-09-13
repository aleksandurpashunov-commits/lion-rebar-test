"""Apply to Group през РЕАЛНИЯ persistence слой.

ЗАЩО СЪЩЕСТВУВА
---------------
Независим одит на rc21 (P0-3) установи, че съществуващите тестове ползват
mock и проверяват само че е направено извикване. Те минаваха, докато
реалното прилагане се проваляше с „Обща дължина трябва да е положително
число“ — заради несъвпадащи имена на полета между
``_decision_payload`` и ``save_engineer_decision``.

Тези тестове викат истинския ``save_engineer_decision`` върху истински
проект на диска.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.apply_to_group import (
    GroupApplicationError,
    apply_group_plan,
    build_group_plan,
    load_group_operations,
    undo_group_operation,
)

IDENT = DataIdentity("P1", "a" * 64, "G1", "R1")


def _row(handle: str, **over):
    row = {
        "annotation_handle": handle, "annotation_text": "25N8x135",
        "annotation_count": "25", "diameter_mm": "8", "declared_length_cm": "135",
        "master_shape_code": "F006", "candidate_handles": [f"G{handle}"],
        "canonical_segment_lengths_cm": "35,20,10",
        "production_segments_cm": [10.0, 20.0, 35.0, 20.0, 35.0, 10.0],
        "matcher_blocker_codes": "HOOKED_CLOSURE", "review_group_key": "K1",
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "project_id": "P1", "source_sha256": "a" * 64,
        "source_generation_id": "G1", "analysis_run_id": "R1",
    }
    row.update(over)
    return row


def _project(directory: str) -> Path:
    root = Path(directory)
    (root / "working").mkdir(parents=True, exist_ok=True)
    return root


def _decisions_on_disk(root: Path) -> list[dict]:
    path = root / "working" / "engineer_decisions.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return list(payload.get("decisions") or payload.values())
    return list(payload)


def test_payload_field_names_match_the_save_contract():
    """Имената трябва да са тези, които пътят за запис очаква."""

    from lion_rebar.review.apply_to_group import _decision_payload

    payload = _decision_payload(_row("A1"), {}, "бележка", "A0")
    resolved = payload["resolved"]
    assert "production_length_cm" in resolved
    assert "segment_lengths_cm" in resolved
    # Старите имена НЕ трябва да се връщат.
    assert "total_length_cm" not in resolved
    assert "segments_cm" not in resolved


def test_apply_really_writes_through_the_production_path():
    """Истински запис на диск, без mock."""

    with tempfile.TemporaryDirectory() as directory:
        root = _project(directory)
        members = [_row("A1"), _row("A2"), _row("A3")]
        rows = {row["annotation_handle"]: row for row in members}
        plan = build_group_plan(members[0], members, IDENT,
                                changes={"master_shape_code": "F006"})
        assert plan.can_apply
        operation_id = apply_group_plan(
            root, plan, rows, IDENT,
            applied_by="инженер", note="еднакви стремена")
        assert operation_id
        journal = load_group_operations(root)
        applied = [item for item in journal if item.get("action") == "APPLY_TO_GROUP"]
        assert applied, "груповото действие трябва да е записано"
        assert set(applied[0]["before_state"]) == {"A2", "A3"}


def test_apply_refuses_when_length_is_missing():
    """Липсваща дължина блокира и НЕ оставя частичен резултат."""

    with tempfile.TemporaryDirectory() as directory:
        root = _project(directory)
        broken = _row("A2", declared_length_cm="", production_length_cm="")
        members = [_row("A1"), broken]
        rows = {row["annotation_handle"]: row for row in members}
        plan = build_group_plan(members[0], members, IDENT,
                                changes={"master_shape_code": "F006"})
        if not plan.can_apply:
            return  # подписът вече ги разделя, което също е коректно
        try:
            apply_group_plan(root, plan, rows, IDENT,
                             applied_by="инженер", note="мотив")
        except GroupApplicationError as error:
            assert "отменено изцяло" in str(error)
            failed = [item for item in load_group_operations(root)
                      if item.get("action") == "APPLY_TO_GROUP_FAILED"]
            assert failed, "провалът трябва да остави следа"
            return
        raise AssertionError("невалидна дължина трябва да блокира")


def test_undo_really_restores_through_the_production_path():
    with tempfile.TemporaryDirectory() as directory:
        root = _project(directory)
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        plan = build_group_plan(members[0], members, IDENT,
                                changes={"master_shape_code": "F006"})
        operation_id = apply_group_plan(root, plan, rows, IDENT,
                                        applied_by="и", note="мотив")
        restored = undo_group_operation(root, operation_id, undone_by="и",
                                        note="грешка")
        assert restored == 1
        undone = [item for item in load_group_operations(root)
                  if item.get("action") == "UNDO_GROUP"]
        assert undone and undone[0]["operation_id"] == operation_id


def test_apply_invalidates_final_control_on_disk():
    with tempfile.TemporaryDirectory() as directory:
        root = _project(directory)
        (root / "working" / "final_control_freeze.json").write_text(
            json.dumps({"status": "VALID"}), encoding="utf-8")
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        plan = build_group_plan(members[0], members, IDENT,
                                changes={"master_shape_code": "F006"})
        apply_group_plan(root, plan, rows, IDENT, applied_by="и", note="мотив")
        from lion_rebar.project.final_control_lifecycle import freeze_status

        assert freeze_status(root) == "INVALID"

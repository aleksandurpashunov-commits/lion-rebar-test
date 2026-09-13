"""Тестове за РЕАЛНОТО прилагане и връщане на групово решение.

Одитът на RC1 установи, че ``Apply to Group`` строи план и води журнал,
но не променя нищо, а ``Undo`` не възстановява нищо. Тези тестове
заключват поправеното поведение.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.apply_to_group import (
    load_group_operations,
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
        "matcher_blocker_codes": "HOOKED_CLOSURE", "review_group_key": "K1",
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "project_id": "P1", "source_sha256": "a" * 64,
        "source_generation_id": "G1", "analysis_run_id": "R1",
    }
    row.update(over)
    return row


class _Recorder:
    """Заместител на ``save_engineer_decision`` за проверка на извикванията."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[dict] = []
        self.fail_on = fail_on

    def __call__(self, root, payload):
        handle = payload["annotation"]["handle"]
        if self.fail_on and handle == self.fail_on:
            raise RuntimeError(f"симулиран провал на {handle}")
        self.calls.append(payload)

    @property
    def handles(self) -> list[str]:
        return [call["annotation"]["handle"] for call in self.calls]


def _plan(members, **changes):
    return build_group_plan(members[0], members, IDENT,
                            changes=changes or {"master_shape_code": "F013"})


# ------------------------------------------------ реално прилагане

def test_apply_writes_through_the_engineer_decision_path():
    """Групата не пише директно в Ledger-а — минава през същия контракт."""

    with tempfile.TemporaryDirectory() as directory:
        recorder = _Recorder()
        members = [_row("A1"), _row("A2"), _row("A3")]
        plan = _plan(members)
        rows = {row["annotation_handle"]: row for row in members}
        operation_id = apply_group_plan(
            Path(directory), plan, rows, IDENT,
            applied_by="инженер", note="еднакви стремена",
            save_decision=recorder)
        assert recorder.handles == ["A2", "A3"]
        assert operation_id
        assert recorder.calls[0]["source"] == "APPLY_TO_GROUP"
        assert recorder.calls[0]["resolved"]["shape_code"] == "F013"


def test_apply_records_before_state_for_every_target():
    with tempfile.TemporaryDirectory() as directory:
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        apply_group_plan(Path(directory), _plan(members), rows, IDENT,
                         applied_by="инженер", note="мотив",
                         save_decision=_Recorder())
        entry = load_group_operations(Path(directory))[0]
        assert set(entry["before_state"]) == {"A2"}
        assert entry["before_state"]["A2"]["master_shape_code"] == "F006"


def test_partial_failure_rolls_back_everything():
    """Частично приложена група е недопустима."""

    with tempfile.TemporaryDirectory() as directory:
        recorder = _Recorder(fail_on="A3")
        members = [_row("A1"), _row("A2"), _row("A3"), _row("A4")]
        rows = {row["annotation_handle"]: row for row in members}
        try:
            apply_group_plan(Path(directory), _plan(members), rows, IDENT,
                             applied_by="инженер", note="мотив",
                             save_decision=recorder)
        except GroupApplicationError as error:
            assert "отменено изцяло" in str(error)
        else:
            raise AssertionError("очаква се отказ при провал")
        # Връщането вече е БАЙТОВО: persistence файлът се възстановява от
        # snapshot, вместо да се правят компенсиращи записи. Затова тук се
        # проверява резултатът, а не следата от отменящи извиквания.
        persistence = Path(directory) / "working" / "approved_geometry_links.json"
        assert not persistence.exists(), (
            "файлът не е съществувал преди операцията и не бива да остане")


def test_failure_is_recorded_in_the_journal():
    with tempfile.TemporaryDirectory() as directory:
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        try:
            apply_group_plan(Path(directory), _plan(members), rows, IDENT,
                             applied_by="инженер", note="мотив",
                             save_decision=_Recorder(fail_on="A2"))
        except GroupApplicationError:
            pass
        actions = [op["action"] for op in load_group_operations(Path(directory))]
        assert "APPLY_TO_GROUP_FAILED" in actions


def test_apply_requires_author_and_note():
    with tempfile.TemporaryDirectory() as directory:
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        for kwargs in ({"applied_by": ""}, {"note": ""}):
            arguments = {"applied_by": "и", "note": "мотив",
                         "save_decision": _Recorder()}
            arguments.update(kwargs)
            try:
                apply_group_plan(Path(directory), _plan(members), rows, IDENT,
                                 **arguments)
            except GroupApplicationError:
                continue
            raise AssertionError(f"очаква се отказ за {kwargs}")


def test_apply_refuses_when_plan_cannot_apply():
    with tempfile.TemporaryDirectory() as directory:
        foreign = _row("X1", project_id="OTHER")
        plan = build_group_plan(foreign, [foreign, _row("A2")], IDENT,
                                changes={"master_shape_code": "F013"})
        try:
            apply_group_plan(Path(directory), plan, {}, IDENT,
                             applied_by="и", note="мотив",
                             save_decision=_Recorder())
        except GroupApplicationError as error:
            assert "REPRESENTATIVE_IDENTITY" in str(error)
            return
        raise AssertionError("чужд representative трябва да блокира")


def test_apply_invalidates_final_control():
    with tempfile.TemporaryDirectory() as directory:
        import json

        root = Path(directory)
        (root / "working").mkdir(parents=True)
        (root / "working" / "final_control_freeze.json").write_text(
            json.dumps({"status": "VALID"}), encoding="utf-8")
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        apply_group_plan(root, _plan(members), rows, IDENT,
                         applied_by="и", note="мотив", save_decision=_Recorder())
        from lion_rebar.project.final_control_lifecycle import freeze_status

        assert freeze_status(root) == "INVALID"


# ------------------------------------------------ реално връщане

def test_undo_restores_previous_state():
    """Undo не е маркер — възстановява реално."""

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        recorder = _Recorder()
        members = [_row("A1"), _row("A2"), _row("A3")]
        rows = {row["annotation_handle"]: row for row in members}
        operation_id = apply_group_plan(root, _plan(members), rows, IDENT,
                                        applied_by="и", note="мотив",
                                        save_decision=recorder)
        recorder.calls.clear()
        restored = undo_group_operation(root, operation_id, undone_by="и",
                                        note="грешка", save_decision=recorder)
        assert restored == 2
        # Snapshot връщането не прави нови решения — това е разликата от
        # логическото връщане.
        entry = [item for item in load_group_operations(root)
                 if item.get("action") == "UNDO_GROUP"]
        assert entry and entry[0]["operation_id"] == operation_id


def test_undo_twice_is_refused():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        operation_id = apply_group_plan(root, _plan(members), rows, IDENT,
                                        applied_by="и", note="мотив",
                                        save_decision=_Recorder())
        undo_group_operation(root, operation_id, undone_by="и",
                             save_decision=_Recorder())
        try:
            undo_group_operation(root, operation_id, undone_by="и",
                                 save_decision=_Recorder())
        except GroupApplicationError as error:
            assert "вече е върнато" in str(error)
            return
        raise AssertionError("двойно връщане трябва да се откаже")


def test_undo_after_export_is_refused():
    """Изнесеният опис не се пренаписва мълчаливо."""

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        members = [_row("A1"), _row("A2")]
        rows = {row["annotation_handle"]: row for row in members}
        operation_id = apply_group_plan(root, _plan(members), rows, IDENT,
                                        applied_by="и", note="мотив",
                                        save_decision=_Recorder())
        try:
            undo_group_operation(root, operation_id, undone_by="и",
                                 save_decision=_Recorder(),
                                 exported_after=lambda operation: True)
        except GroupApplicationError as error:
            assert "export" in str(error)
            return
        raise AssertionError("връщане след export трябва да се откаже")


def test_undo_of_unknown_operation_is_refused():
    with tempfile.TemporaryDirectory() as directory:
        try:
            undo_group_operation(Path(directory), "nope", undone_by="и",
                                 save_decision=_Recorder())
        except GroupApplicationError:
            return
        raise AssertionError("непознато действие трябва да се откаже")

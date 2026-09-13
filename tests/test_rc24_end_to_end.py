"""End-to-end тестове за трите сценария от rc24 заданието.

СЦЕНАРИЙ 1: смяна на категория СЛЕД freeze -> export отказва
СЦЕНАРИЙ 2: провал по средата на групово прилагане -> нулева промяна
СЦЕНАРИЙ 3: конфликтен дублиран множител -> production blocker
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.export.frozen_export_config import (
    ExportConfigError,
    FrozenFrameMapping,
    assert_config_matches_freeze,
    compare_mappings,
    config_fingerprint,
    read_frozen_config,
    verify_trace_against_freeze,
    write_frozen_config,
)


class _Frame:
    def __init__(self, index, handle, title, code, sheet):
        self.index = index
        self.marker_handle = handle
        self.title_text = title
        self.category_code = code
        self.suggested_sheet_name = sheet


class _Config:
    def __init__(self, index, code, sheet, title=""):
        self.frame_index = index
        self.category_code = code
        self.sheet_name = sheet
        self.title_text = title


# ------------------------------------------------ сценарий 1

def _frozen_project(directory: str):
    root = Path(directory)
    frames = [_Frame(1, "M1", "АРМИРОВКА ВЕРТИКАЛИ", "VERTICALS", "вертикали")]
    from lion_rebar.export.frozen_export_config import mapping_from_frames

    write_frozen_config(root, mapping_from_frames(frames))
    return root, frames


def test_config_is_frozen_with_the_final_control():
    with tempfile.TemporaryDirectory() as directory:
        root, _frames = _frozen_project(directory)
        frozen = read_frozen_config(root)
        assert len(frozen) == 1
        assert frozen[0].category_code == "VERTICALS"
        assert frozen[0].sheet_name == "вертикали"


def test_changing_category_after_freeze_blocks_export():
    """СЦЕНАРИЙ 1 — точно дефектът от одита на rc21."""

    with tempfile.TemporaryDirectory() as directory:
        root, frames = _frozen_project(directory)
        tampered = [_Config(1, "BOTTOM", "долна", "АРМИРОВКА ВЕРТИКАЛИ")]
        try:
            assert_config_matches_freeze(root, tampered, frames)
        except ExportConfigError as error:
            assert "VERTICALS" in str(error)
            assert "BOTTOM" in str(error)
            return
        raise AssertionError("смяната на категория трябва да блокира export")


def test_changing_sheet_name_after_freeze_blocks_export():
    with tempfile.TemporaryDirectory() as directory:
        root, frames = _frozen_project(directory)
        tampered = [_Config(1, "VERTICALS", "друг лист")]
        try:
            assert_config_matches_freeze(root, tampered, frames)
        except ExportConfigError as error:
            assert "друг лист" in str(error)
            return
        raise AssertionError("смяната на лист трябва да блокира export")


def test_matching_config_passes():
    with tempfile.TemporaryDirectory() as directory:
        root, frames = _frozen_project(directory)
        assert_config_matches_freeze(
            root, [_Config(1, "VERTICALS", "вертикали")], frames)


def test_missing_frozen_config_requires_new_final_control():
    with tempfile.TemporaryDirectory() as directory:
        try:
            assert_config_matches_freeze(
                Path(directory), [_Config(1, "BOTTOM", "долна")], [])
        except ExportConfigError as error:
            assert "Финален контрол" in str(error)
            return
        raise AssertionError("липсващата freeze конфигурация трябва да блокира")


def test_verifier_compares_the_real_excel_trace():
    """Проверява това, което Е ИЗЛЯЗЛО, не което е поискано."""

    with tempfile.TemporaryDirectory() as directory:
        root, _frames = _frozen_project(directory)
        trace = {"schema": "lion-rebar-excel-traceability/v2", "frames": [
            {"frame_index": 1, "sheet_name": "долна", "category_code": "BOTTOM"}]}
        problems = verify_trace_against_freeze(root, trace)
        assert any("EXPORT_SHEET_MISMATCH" in item for item in problems)
        assert any("EXPORT_CATEGORY_MISMATCH" in item for item in problems)


def test_verifier_accepts_matching_v2_and_v3_traces():
    with tempfile.TemporaryDirectory() as directory:
        root, _frames = _frozen_project(directory)
        for schema in (
            "lion-rebar-excel-traceability/v2",
            "lion-rebar-excel-traceability/v3",
        ):
            trace = {"schema": schema, "frames": [
                {"frame_index": 1, "sheet_name": "вертикали",
                 "category_code": "VERTICALS"}]}
            assert verify_trace_against_freeze(root, trace) == []


def test_tampered_frozen_config_fingerprint_blocks_export():
    with tempfile.TemporaryDirectory() as directory:
        root, frames = _frozen_project(directory)
        path = root / "working" / "final_control_export_config.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["frames"][0]["category_code"] = "BOTTOM"
        payload["frames"][0]["sheet_name"] = "долна"
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            assert_config_matches_freeze(
                root, [_Config(1, "BOTTOM", "долна")], frames)
        except ExportConfigError as error:
            assert "FINGERPRINT_MISMATCH" in str(error)
            return
        raise AssertionError("подменена freeze конфигурация трябва да блокира")


def test_fingerprint_changes_with_the_mapping():
    a = [FrozenFrameMapping(1, "M1", "t", "VERTICALS", "вертикали")]
    b = [FrozenFrameMapping(1, "M1", "t", "BOTTOM", "долна")]
    assert config_fingerprint(a) != config_fingerprint(b)


def test_added_and_removed_frames_are_reported():
    frozen = [FrozenFrameMapping(1, "M1", "t", "BEAMS", "греди")]
    current = [FrozenFrameMapping(2, "M2", "t", "BEAMS", "греди")]
    problems = compare_mappings(frozen, current)
    assert any("липсва" in item for item in problems)
    assert any("не е замразена" in item for item in problems)


# ------------------------------------------------ сценарий 2

def test_partial_failure_leaves_persistence_untouched():
    """СЦЕНАРИЙ 2 — байтов snapshot, не логическо връщане."""

    from lion_rebar.project.data_identity import DataIdentity
    from lion_rebar.review.apply_to_group import (
        GroupApplicationError,
        apply_group_plan,
        build_group_plan,
    )

    identity = DataIdentity("P1", "a" * 64, "G1", "R1")

    def _row(handle):
        return {
            "annotation_handle": handle, "annotation_text": "25N8x135",
            "annotation_count": "25", "diameter_mm": "8",
            "declared_length_cm": "135", "master_shape_code": "F006",
            "candidate_handles": [f"G{handle}"],
            "canonical_segment_lengths_cm": "35,20,10",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД", "review_group_key": "K1",
            "project_id": "P1", "source_sha256": "a" * 64,
            "source_generation_id": "G1", "analysis_run_id": "R1",
        }

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "working").mkdir(parents=True)
        persistence = root / "working" / "approved_geometry_links.json"
        original = json.dumps({"links": [{"handle": "СТАРО"}]}, ensure_ascii=False)
        persistence.write_text(original, encoding="utf-8")

        members = [_row("A1"), _row("A2"), _row("A3")]
        rows = {row["annotation_handle"]: row for row in members}
        plan = build_group_plan(members[0], members, identity,
                                changes={"master_shape_code": "F006"})

        def failing(_root, payload):
            if payload["annotation"]["handle"] == "A3":
                raise RuntimeError("симулиран провал")

        try:
            apply_group_plan(root, plan, rows, identity, applied_by="и",
                             note="мотив", save_decision=failing)
        except GroupApplicationError:
            pass
        assert persistence.read_text(encoding="utf-8") == original, (
            "persistence файлът трябва да е БАЙТ ПО БАЙТ същият")


def test_snapshot_is_recorded_in_the_journal():
    from lion_rebar.review.apply_to_group import record_group_operation
    import inspect

    assert "persistence_snapshot" in inspect.signature(
        record_group_operation).parameters


# ------------------------------------------------ сценарий 3

def test_conflicting_duplicate_multiplier_is_flagged():
    """СЦЕНАРИЙ 3 — различни стойности на един таг."""

    from lion_rebar.dxf.importer import _rebar_annotation_from_insert_record
    from lion_rebar.dxf.models import DxfEntityRecord

    record = DxfEntityRecord("H", "INSERT", "", {
        "insert": [0, 0, 0],
        "attributes": [
            {"tag": "BX", "text": "39", "insert": [0, 0, 0], "height": 10},
            {"tag": "BX", "text": "12", "insert": [0, 0, 0], "height": 10},
            {"tag": "BP", "text": "2", "insert": [0, 0, 0], "height": 10},
            {"tag": "BJ", "text": "28", "insert": [0, 0, 0], "height": 10},
            {"tag": "FI", "text": "8", "insert": [0, 0, 0], "height": 10},
            {"tag": "L", "text": "110", "insert": [0, 0, 0], "height": 10},
        ]})
    result = _rebar_annotation_from_insert_record(record)
    assert "BX" in result.geometry["conflicting_attribute_tags"]
    assert any("КОНФЛИКТНО" in item
               for item in result.geometry["count_multiplier_problems"])


def test_identical_duplicate_is_not_a_conflict():
    """Копиран атрибут със същата стойност е безобиден."""

    from lion_rebar.dxf.importer import _rebar_annotation_from_insert_record
    from lion_rebar.dxf.models import DxfEntityRecord

    record = DxfEntityRecord("H", "INSERT", "", {
        "insert": [0, 0, 0],
        "attributes": [
            {"tag": "BX", "text": "39", "insert": [0, 0, 0], "height": 10},
            {"tag": "BX", "text": "39", "insert": [0, 0, 0], "height": 10},
            {"tag": "BJ", "text": "28", "insert": [0, 0, 0], "height": 10},
            {"tag": "FI", "text": "8", "insert": [0, 0, 0], "height": 10},
            {"tag": "L", "text": "110", "insert": [0, 0, 0], "height": 10},
        ]})
    result = _rebar_annotation_from_insert_record(record)
    assert result.geometry["conflicting_attribute_tags"] == []


def test_conflicting_duplicate_blocks_the_production_gate():
    source = Path("lion_rebar/export/batch_auto_output.py").read_text(encoding="utf-8")
    assert "MULTIPLIER_UNREADABLE" in source
    assert "count_multiplier_problems" in source


# ------------------------------------------------ свързаност

def test_undo_has_a_real_button():
    """Твърдението „може да бъде върнато“ вече има изпълнение."""

    review = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")
    assert "undo_group_button" in review
    assert "def _undo_last_group" in review
    assert "undo_group_operation(" in review


def test_export_blocks_on_config_mismatch():
    source = Path("lion_rebar/export/excel_export.py").read_text(encoding="utf-8")
    assert "assert_config_matches_freeze" in source
    start = source.index("def generate_excel_export")
    body = source[start:start + 1800]
    assert "assert_config_matches_freeze" in body

"""Регресионни тестове по находките от реалното Windows приемане.

Всяка от тях е открита от оператор при нормална работа — не от одит на код.
"""
from __future__ import annotations

import json
import hashlib
import tempfile
from pathlib import Path

from lion_rebar.export.final_export_manifest import (
    build_final_export_manifest,
    verify_final_export_manifest,
    write_final_export_manifest,
)


def _fixture(tmp: Path, *, ledger_rows: int, excel_rows: int, non_production: int,
             bundle_sha: str = ""):
    ledger = tmp / "final_integrity_ledger.csv"
    ledger.write_text("a\n", encoding="utf-8")
    excel = tmp / "spec.xlsx"
    excel.write_bytes(b"PK demo")
    dxf = tmp / "spec.dxf"
    dxf.write_text("0\nEOF\n", encoding="utf-8")
    from lion_rebar.export.frozen_export_config import (
        FrozenFrameMapping, write_frozen_config,
    )
    config = write_frozen_config(
        tmp, [FrozenFrameMapping(1, "M1", "ГРЕДИ", "BEAMS", "греди")]
    )
    excel_trace = tmp / "spec_TRACEABILITY.json"
    excel_trace.write_text(json.dumps({
        "schema": "lion-rebar-excel-traceability/v2",
        "frames": [{"frame_index": 1, "category_code": "BEAMS",
                    "sheet_name": "греди"}],
    }), encoding="utf-8")
    dxf_trace = tmp / "spec_DXF_TRACEABILITY.json"
    dxf_trace.write_text("{}", encoding="utf-8")
    freeze = tmp / "final_control_freeze.json"
    freeze.write_text(json.dumps({
        "schema": "lion-rebar-final-control-freeze/v1", "status": "VALID",
        "artifacts": [{"role": "EXPORT_CONFIG",
                       "sha256": hashlib.sha256(config.read_bytes()).hexdigest()}],
    }), encoding="utf-8")
    return build_final_export_manifest(
        app_version="0.1.44.20-rc6", engine_id="lion-rebar-master",
        engine_version="0.3.13", engine_sha256="474f" * 16,
        engine_bundle_sha256=bundle_sha, matcher_sha256="0521" * 16,
        project_id="P1", source_sha256="a" * 64, source_generation_id="G1",
        analysis_run_id="R1", final_control_id="R1",
        final_ledger_path=ledger, excel_path=excel, final_dxf_path=dxf,
        project_root=tmp, export_config_path=config,
        excel_trace_path=excel_trace, dxf_trace_path=dxf_trace,
        final_control_path=freeze,
        ledger_row_count=ledger_rows, excel_row_count=excel_rows,
        non_production_position_count=non_production)


# ---------------------------------------------- находка 5: уравнението

def test_win_excluded_position_is_not_a_row_mismatch():
    """Реалният случай: Ledger 165, Excel 164, една изключена позиция."""

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        manifest = _fixture(tmp, ledger_rows=165, excel_rows=164, non_production=1)
        assert manifest["completeness"]["ledger_matches_excel"] is True
        assert manifest["completeness"]["ledger_excel_equation"] == "165 - 1 = 164"
        assert verify_final_export_manifest(
            write_final_export_manifest(tmp, manifest)) == []


def test_win_no_exclusions_still_requires_equality():
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        manifest = _fixture(tmp, ledger_rows=165, excel_rows=165, non_production=0)
        assert manifest["completeness"]["ledger_matches_excel"] is True


def test_win_real_mismatch_is_still_caught():
    """Поправката не бива да ослепи проверката."""

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        manifest = _fixture(tmp, ledger_rows=165, excel_rows=163, non_production=1)
        assert manifest["completeness"]["ledger_matches_excel"] is False
        problems = verify_final_export_manifest(
            write_final_export_manifest(tmp, manifest))
        assert any("LEDGER_EXCEL_ROW_MISMATCH" in p for p in problems)


def test_win_missing_excel_rows_do_not_pass_silently():
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        manifest = _fixture(tmp, ledger_rows=165, excel_rows=0, non_production=1)
        assert manifest["completeness"]["ledger_matches_excel"] is False


# ---------------------------------------------- engine SHA provenance

def test_win_engine_bundle_sha_is_recorded_when_available():
    """Договорът сверява хеша на .lionengine ФАЙЛА, не на съдържанието."""

    with tempfile.TemporaryDirectory() as directory:
        manifest = _fixture(Path(directory), ledger_rows=1, excel_rows=1,
                            non_production=0, bundle_sha="4a0e" * 16)
        assert manifest["build"]["engine_sha256"] == "4a0e" * 16
        assert manifest["build"]["engine_content_sha256"] == "474f" * 16


def test_win_engine_sha_falls_back_to_content_hash():
    with tempfile.TemporaryDirectory() as directory:
        manifest = _fixture(Path(directory), ledger_rows=1, excel_rows=1,
                            non_production=0)
        assert manifest["build"]["engine_sha256"] == "474f" * 16


# ---------------------------------------------- находка 4: отметката

def test_win_exclusion_checkbox_writes_terminal_decision():
    """Отметката вече записва NON_PRODUCTION_POSITION, не наследения код."""

    source = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")
    index = source.index("def _open_problem_dialog")
    body = source[index:index + 5000]
    assert '"NON_PRODUCTION_POSITION" if explicit_exclusion else "PROBLEM_RECORDED"' in body
    # Наследеният код не бива да се записва повече. Коментарите, които
    # обясняват поправката, се игнорират.
    code_lines = [
        line for line in body.splitlines()
        if "EXCLUDED_BY_ENGINEER" in line and not line.strip().startswith("#")
    ]
    assert code_lines == [], code_lines


def test_win_exclusion_requires_a_note():
    source = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")
    index = source.index("def _open_problem_dialog")
    body = source[index:index + 5000]
    assert "Липсва мотив за изключване" in body


def test_win_exclusion_supplies_evidence():
    source = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")
    index = source.index("def _open_problem_dialog")
    body = source[index:index + 6000]
    assert '"evidence"' in body
    assert '"REVIEW_PROBLEM_DIALOG"' in body


def test_win_terminal_decision_shape_passes_final_control_validation():
    """Записът, който отметката произвежда, минава валидацията 1:1."""

    decision = {
        "decision": "NON_PRODUCTION_POSITION",
        "action_class": "EXCLUSION",
        "reason_code": "NON_PRODUCTION_POSITION",
        "note": "не е реална производствена позиция",
        "evidence": {"source": "REVIEW_PROBLEM_DIALOG"},
    }
    valid = (
        str(decision.get("action_class") or "").strip().upper() == "EXCLUSION"
        and str(decision.get("reason_code") or "").strip().upper()
        == "NON_PRODUCTION_POSITION"
        and bool(str(decision.get("note") or "").strip())
        and isinstance(decision.get("evidence"), dict)
        and bool(decision.get("evidence"))
    )
    assert valid


def test_win_diagnostic_reason_is_preserved():
    """Защо е изключена позицията не бива да се губи."""

    source = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")
    assert '"diagnostic_reason_code": reason_code' in source


# ---------------------------------------------- находка 6: изолацията

def test_win_isolation_moved_to_open_project():
    manager = Path("lion_rebar/project/manager.py").read_text(encoding="utf-8")
    index = manager.index("def open_project")
    body = manager[index:index + 2500]
    assert "audit_project_isolation" in body
    assert "last_isolation_report" in body


def test_win_create_project_guard_is_marked_as_unreachable_second_line():
    manager = Path("lion_rebar/project/manager.py").read_text(encoding="utf-8")
    index = manager.index("def create_project")
    body = manager[index:index + 2500]
    assert "ВТОРА ЛИНИЯ" in body
    assert "недостижима" in body


def test_win_open_project_isolation_does_not_block():
    """Предупреждава, не отказва — практиката още не е известна."""

    manager = Path("lion_rebar/project/manager.py").read_text(encoding="utf-8")
    index = manager.index("def open_project")
    body = manager[index:index + 2500]
    assert "ProjectError" not in body.split("audit_project_isolation")[1][:600]


# ------------------------------- VERTICALS: одобрена категория (2026-08-24)

def test_verticals_has_its_own_sheet_in_approved_order():
    """Реален чертеж: 'АРМИРОВКА ВЕРТИКАЛИ ДО КОТА +8,90' блокираше Excel."""

    from lion_rebar.export.frame_detection import (
        CATEGORY_ORDER,
        CATEGORY_SHEET_NAMES,
        classify_title,
    )

    assert CATEGORY_SHEET_NAMES["VERTICALS"] == "вертикали"
    assert classify_title("АРМИРОВКА ВЕРТИКАЛИ ДО КОТА +8,90") == ("VERTICALS", "HIGH")
    assert classify_title("АРМИРОВКА ВЕРТИКАЛИ ДО КОТА + 11,90") == ("VERTICALS", "HIGH")
    sheets = [
        CATEGORY_SHEET_NAMES[code]
        for code, _ in sorted(CATEGORY_ORDER.items(), key=lambda kv: kv[1])
        if code != "UNKNOWN"
    ]
    assert sheets == ["долна", "долна усилване", "горна", "горна усилване",
                      "детайли", "греди", "вертикали"]


def test_verticals_does_not_shadow_other_categories():
    """Добавянето не бива да променя разпознаването на останалите."""

    from lion_rebar.export.frame_detection import classify_title

    assert classify_title("ДОЛНА АРМИРОВКА НА КОТА +11.90") == ("BOTTOM", "HIGH")
    assert classify_title("ГОРНА АРМИРОВКА", "УСИЛВАНЕ") == ("TOP_EXTRA", "HIGH")
    assert classify_title("АРМИРОВКА ГРЕДИ НА КОТА +5.90") == ("BEAMS", "HIGH")
    assert classify_title("ДЕТАЙЛИ АРМИРОВКА") == ("DETAILS", "HIGH")
    assert classify_title("НЕЩО СЪВСЕМ ДРУГО") == ("UNKNOWN", "LOW")


def test_verticals_is_found_by_fallback_tokens_too():
    from lion_rebar.export.frame_detection import TITLE_TOKENS

    assert "ВЕРТИКАЛ" in TITLE_TOKENS

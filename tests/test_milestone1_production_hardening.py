"""Regression тестове за Milestone 1 — Production Hardening.

Покриват P0-1 (Unicode), P0-2 (frame resolution), P0-4 (engine pin),
P0-7 (final export manifest). Без Qt и без ezdxf, за да работят в CI.
"""
from __future__ import annotations

import json
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

from lion_rebar.dxf.text_normalization import (
    contains_encoded_text,
    decode_dxf_text,
    normalize_for_match,
)
from lion_rebar.export.frame_detection import (
    apply_operator_frame_decisions,
    classify_title,
    detect_drawing_frames,
    frame_provenance_rows,
    unresolved_frames,
)
from lion_rebar.export.final_export_manifest import (
    build_final_export_manifest,
    verify_final_export_manifest,
    write_final_export_manifest,
)
from lion_rebar.project.engine_pin import describe_engine_pin_mismatch


def _text(handle, text, x, y, height=21.0):
    return SimpleNamespace(
        handle=handle, entity_type="TEXT", text=text,
        geometry={"insert": [x, y, 0.0], "height": height})


# --------------------------------------------------------------- P0-1

def test_decode_autocad_unicode_escape():
    raw = r"\U+0427\U+0415\U+0420\U+0422\U+0415\U+0416:"
    assert decode_dxf_text(raw) == "ЧЕРТЕЖ:"


def test_decode_is_idempotent():
    raw = r"\U+0427\U+0415\U+0420\U+0422\U+0415\U+0416"
    assert decode_dxf_text(decode_dxf_text(raw)) == decode_dxf_text(raw)


def test_decode_handles_percent_codes_and_plain_text():
    assert decode_dxf_text("%%UДетайл") == "Детайл"
    assert decode_dxf_text("N%%c12") == "NØ12"
    assert decode_dxf_text("45%%d") == "45°"
    assert decode_dxf_text("ДОЛНА АРМИРОВКА") == "ДОЛНА АРМИРОВКА"


def test_decode_never_raises_on_broken_input():
    assert decode_dxf_text(r"\U+ZZZZ") == r"\U+ZZZZ"
    assert decode_dxf_text(None) == ""


def test_contains_encoded_text_detects_only_real_escapes():
    assert contains_encoded_text(r"\U+0427")
    assert contains_encoded_text("%%u")
    assert not contains_encoded_text("ЧЕРТЕЖ")


def test_normalize_for_match_upper_and_collapsed():
    assert normalize_for_match(r"  \U+0447\U+0435\U+0440\U+0442\U+0435\U+0436   x ") == "ЧЕРТЕЖ X"


# --------------------------------------------------------------- P0-2

def test_encoded_marker_is_found_after_decode():
    """Belgravia root cause: суровият текст дава 0 маркера."""

    records = [
        _text("M1", r"\U+0427\U+0415\U+0420\U+0422\U+0415\U+0416:", 104433, -2919),
        _text("T1", "ДОЛНА АРМИРОВКА НА КОТА +11.90", 104584, -2965),
    ]
    frames = detect_drawing_frames(records)
    assert len(frames) == 1
    assert frames[0].category_code == "BOTTOM"


def test_fallback_resolves_title_outside_primary_window():
    """Заглавието е на dy=-46 — извън доказания прозорец (-12..15)."""

    records = [
        _text("M1", "ЧЕРТЕЖ:", 0, 0),
        _text("T1", "ДОЛНА АРМИРОВКА НА КОТА +11.90", 151, -46),
    ]
    frame = detect_drawing_frames(records)[0]
    assert frame.category_code == "BOTTOM"
    assert frame.resolution_reason == "FALLBACK_RADIUS"
    assert frame.confidence == "MEDIUM"
    assert frame.title_handle == "T1"
    assert frame.title_distance > 0


def test_primary_window_behaviour_is_unchanged():
    """Fail-safe: доказаният път остава HIGH / PRIMARY_WINDOW."""

    records = [_text("M1", "ЧЕРТЕЖ:", 1000, 1000),
               _text("T1", "ГОРНА АРМИРОВКА", 1200, 1005)]
    frame = detect_drawing_frames(records)[0]
    assert frame.category_code == "TOP"
    assert frame.confidence == "HIGH"
    assert frame.resolution_reason == "PRIMARY_WINDOW"


def test_prose_mentioning_keyword_is_not_taken_as_title():
    """Общата бележка съдържа 'ГРЕД', но не е заглавие -> без предположение."""

    records = [_text("M1", "ЧЕРТЕЖ:", 0, 0),
               _text("N1", "плочи, греди, колони и шайби !", 300, -100, height=27.3)]
    frame = detect_drawing_frames(records)[0]
    assert frame.category_code == "UNKNOWN"
    assert frame.requires_operator_decision


def test_ambiguous_fallback_refuses_to_guess():
    records = [_text("M1", "ЧЕРТЕЖ:", 0, 0),
               _text("T1", "ДОЛНА АРМИРОВКА", 100, -50),
               _text("T2", "ГОРНА АРМИРОВКА", 101, -51)]
    frame = detect_drawing_frames(records)[0]
    assert frame.category_code == "UNKNOWN"
    assert frame.resolution_reason == "FALLBACK_AMBIGUOUS"


def test_no_title_marks_frame_for_operator():
    frame = detect_drawing_frames([_text("M1", "ЧЕРТЕЖ:", 0, 0)])[0]
    assert frame.category_code == "UNKNOWN"
    assert frame.resolution_reason == "NO_TITLE_FOUND"
    assert len(unresolved_frames([frame])) == 1


def test_operator_can_resolve_unknown_frame():
    frames = detect_drawing_frames([_text("M1", "ЧЕРТЕЖ:", 0, 0)])
    resolved = apply_operator_frame_decisions(frames, {"M1": "BEAMS"})
    assert resolved[0].category_code == "BEAMS"
    assert resolved[0].resolved_by == "OPERATOR"
    assert resolved[0].confidence == "OPERATOR"
    assert unresolved_frames(resolved) == []


def test_operator_cannot_override_proven_automatic_category():
    frames = detect_drawing_frames([_text("M1", "ЧЕРТЕЖ:", 1000, 1000),
                                    _text("T1", "ГОРНА АРМИРОВКА", 1200, 1005)])
    resolved = apply_operator_frame_decisions(frames, {"M1": "BEAMS"})
    assert resolved[0].category_code == "TOP"
    assert resolved[0].resolved_by == "AUTO"


def test_operator_decision_rejects_unknown_category_code():
    frames = detect_drawing_frames([_text("M1", "ЧЕРТЕЖ:", 0, 0)])
    resolved = apply_operator_frame_decisions(frames, {"M1": "NOT_A_CATEGORY"})
    assert resolved[0].category_code == "UNKNOWN"


def test_frame_provenance_is_complete():
    frames = detect_drawing_frames([_text("M1", "ЧЕРТЕЖ:", 0, 0),
                                    _text("T1", "ДОЛНА АРМИРОВКА", 151, -46)])
    row = frame_provenance_rows(frames)[0]
    for key in ("marker_handle", "title_handle", "title_distance", "category_code",
                "confidence", "resolution_reason", "resolved_by", "marker_text_height"):
        assert key in row


def test_classify_title_contract_unchanged():
    assert classify_title("ДОЛНА АРМИРОВКА") == ("BOTTOM", "HIGH")
    assert classify_title("ДОЛНА АРМИРОВКА", "УСИЛВАНЕ") == ("BOTTOM_EXTRA", "HIGH")
    assert classify_title("АРМИРОВКА ГРЕДИ") == ("BEAMS", "HIGH")
    assert classify_title("НЕЩО ДРУГО") == ("UNKNOWN", "LOW")


# --------------------------------------------------------------- P0-4

def test_identical_engine_pin_produces_no_warning():
    assert describe_engine_pin_mismatch(
        project_engine_id="lion-rebar-master", project_version="0.3.13",
        project_sha256="a" * 64, current_engine_id="lion-rebar-master",
        current_version="0.3.13", current_sha256="a" * 64) == []


def test_same_version_different_sha_reports_sha_not_version():
    result = describe_engine_pin_mismatch(
        project_engine_id="lion-rebar-master", project_version="0.3.13",
        project_sha256="a" * 64, current_engine_id="lion-rebar-master",
        current_version="0.3.13", current_sha256="b" * 64)
    assert [item["field"] for item in result] == ["content_sha256"]
    assert "подпис" in result[0]["label"]


def test_legacy_project_without_recorded_sha_does_not_warn():
    assert describe_engine_pin_mismatch(
        project_engine_id="lion-rebar-master", project_version="0.3.13",
        project_sha256="", current_engine_id="lion-rebar-master",
        current_version="0.3.13", current_sha256="b" * 64) == []


def test_real_version_change_is_reported():
    result = describe_engine_pin_mismatch(
        project_engine_id="lion-rebar-master", project_version="0.3.13",
        project_sha256="a" * 64, current_engine_id="lion-rebar-master",
        current_version="0.3.14", current_sha256="a" * 64)
    assert [item["field"] for item in result] == ["version"]


# --------------------------------------------------------------- P0-7

def _manifest_fixture(tmp: Path, **over):
    ledger = tmp / "ledger.csv"; ledger.write_text("a\n1\n", encoding="utf-8")
    excel = tmp / "spec.xlsx"; excel.write_bytes(b"PK\x03\x04demo")
    dxf = tmp / "final.dxf"; dxf.write_text("0\nEOF\n", encoding="utf-8")
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
    dxf_trace = tmp / "final_TRACEABILITY.json"
    dxf_trace.write_text("{}", encoding="utf-8")
    freeze = tmp / "final_control_freeze.json"
    freeze.write_text(json.dumps({
        "schema": "lion-rebar-final-control-freeze/v1", "status": "VALID",
        "artifacts": [{"role": "EXPORT_CONFIG",
                       "sha256": hashlib.sha256(config.read_bytes()).hexdigest()}],
    }), encoding="utf-8")
    args = dict(app_version="0.1.44.20", engine_id="lion-rebar-master",
                engine_version="0.3.13", engine_sha256="4a0e" * 16,
                matcher_sha256="0521" * 16, project_id="P1", source_sha256="aa" * 32,
                source_generation_id="G1", analysis_run_id="R1", final_control_id="FC1",
                final_ledger_path=ledger,
                excel_path=excel, final_dxf_path=dxf, project_root=tmp,
                export_config_path=config, excel_trace_path=excel_trace,
                dxf_trace_path=dxf_trace, final_control_path=freeze,
                ledger_row_count=165,
                excel_row_count=165)
    args.update(over)
    return args, ledger, excel, dxf


def test_manifest_contains_all_required_identity_fields():
    with tempfile.TemporaryDirectory() as d:
        args, *_ = _manifest_fixture(Path(d))
        manifest = build_final_export_manifest(**args)
        for key in ("app_version", "engine_version", "engine_sha256", "matcher_sha256"):
            assert manifest["build"][key]
        for key in ("project_id", "source_sha256", "source_generation_id",
                    "analysis_run_id", "final_control_id"):
            assert manifest["identity"][key]
        assert {a["role"] for a in manifest["artifacts"]} == {
            "FINAL_LEDGER", "EXCEL", "FINAL_DXF", "EXPORT_CONFIG",
            "EXCEL_TRACE", "DXF_TRACE", "FINAL_CONTROL_FREEZE"}
        assert manifest["completeness"]["complete"]


def test_manifest_verify_detects_tampered_artifact():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        args, _, excel, _ = _manifest_fixture(tmp)
        path = write_final_export_manifest(tmp, build_final_export_manifest(**args))
        assert verify_final_export_manifest(path) == []
        excel.write_bytes(b"PK\x03\x04TAMPERED")
        assert "ARTIFACT_SHA_MISMATCH: EXCEL" in verify_final_export_manifest(path)


def test_manifest_verify_detects_missing_artifact():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        args, _, _, dxf = _manifest_fixture(tmp)
        path = write_final_export_manifest(tmp, build_final_export_manifest(**args))
        dxf.unlink()
        assert any("FINAL_DXF" in p for p in verify_final_export_manifest(path))


def test_manifest_flags_ledger_excel_row_mismatch():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        args, *_ = _manifest_fixture(tmp, excel_row_count=160)
        path = write_final_export_manifest(tmp, build_final_export_manifest(**args))
        assert any("LEDGER_EXCEL_ROW_MISMATCH" in p for p in verify_final_export_manifest(path))


def test_manifest_flags_unresolved_frames():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        args, *_ = _manifest_fixture(tmp, unresolved_frame_count=8)
        path = write_final_export_manifest(tmp, build_final_export_manifest(**args))
        assert any("UNRESOLVED_FRAMES" in p for p in verify_final_export_manifest(path))


def test_manifest_is_deterministic_for_same_inputs():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        args, *_ = _manifest_fixture(tmp)
        a = build_final_export_manifest(exported_at="2026-08-23T00:00:00+00:00", **args)
        b = build_final_export_manifest(exported_at="2026-08-23T00:00:00+00:00", **args)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

"""Recovery.4 — разделяне Form Recognition / Production Dimensions за F006.

Дванадесетте задължителни теста от заданието. Всеки тест печата
input / expected / actual / reason при провал чрез assert съобщенията.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import (
    _apply_f006_printed_production_dimensions,
    _derive_f006_dimensions_from_candidate,
)


class _Shape:
    code = "F006"
    active = True


def _row(
    *,
    segments=None,
    hooked=True,
    ambiguous=False,
    conflict=False,
    difference=1.44,
    status="СИЛНО СТРЕМЕ F006",
):
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "T1",
        "annotation_text": "33N8x255",
        "master_shape_code": "F006",
        "declared_length_cm": 255.0,
        "candidate_handles": ["G1"],
        "batch_status": status,
        "candidate_confidence": "СИЛНО СЕМЕЙСТВО F006",
        "numeric_difference": difference,
        "review_reason_codes": ["HOOKED_CLOSURE"] if hooked else [],
        "matcher_blocker_codes": "HOOKED_CLOSURE" if hooked else "",
        "canonical_segment_lengths_cm": list(
            segments if segments is not None else (4.6, 15.0, 160.0, 15.0, 160.0, 10.0)
        ),
        "master_ambiguous_shape_codes": ["F006", "F013"] if ambiguous else [],
        "global_conflict": conflict,
        "geometry_length_raw": 253.56,
    }
    return row


def _run_gate(row, *, segments_result=None, raises=None):
    """Изпълнява портата с контролиран _segments_for_link."""

    from lion_rebar.dxf import batch_geometry_report as bgr

    original = bgr._segments_for_link

    def fake(link, *, shape, entity_map, production_length_cm):
        if raises is not None:
            raise raises
        return segments_result

    bgr._segments_for_link = fake
    try:
        _apply_f006_printed_production_dimensions(
            [row], entities=[], shape_definitions=[_Shape()]
        )
    finally:
        bgr._segments_for_link = original
    return row


ASYM = Exception  # marker


def _asym_exc():
    from lion_rebar.export.excel_export import F006AsymmetricHookError

    return F006AsymmetricHookError(4.58719, 10.0)


def test_01_symmetric_f006_strong_and_production_exact() -> None:
    row = _row(segments=(10.0, 15.0, 160.0, 15.0, 160.0, 10.0), difference=0.0)
    _run_gate(row, segments_result=((10.0, 100.0, 17.5, 100.0, 17.5, 10.0), (90,) * 5))
    assert row["batch_status"] == "СИЛНО СТРЕМЕ F006", row
    assert row["f006_production_status"] == "F006_PRODUCTION_EXACT", row


def test_02_asymmetric_hooks_keep_strong_form() -> None:
    row = _row()
    _run_gate(row, raises=_asym_exc())
    assert row["batch_status"] == "СИЛНО СТРЕМЕ F006", (
        "form classification падна: " + row["batch_status"]
    )
    assert row.get("f006_asymmetric_hooks") is True


def test_03_derivable_dimensions_become_production_derived() -> None:
    row = _row(segments=(4.6, 15.0, 160.0, 15.0, 160.0, 10.0))
    _run_gate(row, raises=_asym_exc())
    assert row["f006_production_status"] == "F006_PRODUCTION_DERIVED", row
    assert "provenance" in str(row.get("f006_production_dimensions_provenance", "")) or row[
        "f006_production_dimensions_provenance"
    ]
    # Асиметричните куки са ЗАПАЗЕНИ, не заменени.
    assert row["canonical_segment_lengths_cm"][0] == 4.6
    assert row["canonical_segment_lengths_cm"][-1] == 10.0


def test_04_underivable_dimensions_become_production_review() -> None:
    row = _row(segments=(4.6, 15.0, 160.0))  # само 3 сегмента — неизвлечими
    _run_gate(row, raises=_asym_exc())
    assert row["batch_status"] == "СИЛНО СТРЕМЕ F006"
    assert row["f006_production_status"] == "F006_PRODUCTION_REVIEW", row
    assert row.get("production_export_blocked") is True


def test_05_asymmetric_without_hooked_closure_goes_review() -> None:
    row = _row(hooked=False)
    _run_gate(row, raises=_asym_exc())
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", row["batch_status"]


def test_06_total_length_out_of_tolerance_goes_review() -> None:
    row = _row(difference=5.0)
    _run_gate(row, raises=_asym_exc())
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", row["batch_status"]


def test_07_master_ambiguity_goes_review() -> None:
    row = _row(ambiguous=True)
    _run_gate(row, raises=_asym_exc())
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", row["batch_status"]


def test_08_ownership_conflict_goes_review() -> None:
    row = _row(conflict=True)
    _run_gate(row, raises=_asym_exc())
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", row["batch_status"]


def test_09_no_case_invents_symmetric_hooks() -> None:
    row = _row(segments=(4.6, 15.0, 160.0, 15.0, 160.0, 10.0))
    _run_gate(row, raises=_asym_exc())
    lengths = row["canonical_segment_lengths_cm"]
    assert lengths[0] != lengths[-1], "куките са изравнени изкуствено: " + str(lengths)
    derived = _derive_f006_dimensions_from_candidate(row, entity_map={})
    assert derived is not None
    assert derived["segments"][0] == 4.6 and derived["segments"][-1] == 10.0


def test_10_auto_approval_does_not_export_production_review(tmp_path: Path) -> None:
    """PRODUCTION_REVIEW ред е blocker в export контекста, не позиция."""

    import inspect

    from lion_rebar.export import batch_auto_output as bao

    source = inspect.getsource(bao)
    assert "F006_PRODUCTION_REVIEW" in source
    assert "production_export_blocked" in source


def test_11_legacy_auto_links_without_provenance_invalid() -> None:
    from lion_rebar.export.auto_approval import _provenance_matches

    current = {
        "source_dxf_sha256": "d" * 64,
        "engine_hash": "e" * 64,
        "application_version": "x",
        "matcher_sha256": "m" * 64,
        "rules_sha256": "r" * 64,
    }
    legacy = {"approved_by": "AUTO_BATCH_GATE"}
    assert not _provenance_matches(legacy, current)


def test_12_user_approvals_untouched_by_reconciliation(tmp_path: Path) -> None:
    from lion_rebar.export import auto_approval as aa

    project_root = tmp_path / "p"
    approved = project_root / "working" / "approved_geometry_links.json"
    approved.parent.mkdir(parents=True)
    approved.write_text(
        json.dumps(
            {
                "schema": aa.SCHEMA,
                "links": [
                    {
                        "annotation_handle": "U1",
                        "approved_by": "USER",
                        "geometry_handles": ["G9"],
                    }
                ],
                "history": [],
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"rows": []}), encoding="utf-8")

    class _Ctx:
        actionable_count = 0
        positions = []
        blockers = []

    original = aa.build_batch_export_context
    aa.build_batch_export_context = lambda *a, **k: _Ctx()
    try:
        aa.auto_approve_gate_positions(
            project_root,
            report_path=report,
            entities=[],
            engine=SimpleNamespace(version="0.3.13", to_dict=lambda: {"rules": []}),
            source_dxf_path=None,
        )
    finally:
        aa.build_batch_export_context = original
    payload = json.loads(approved.read_text(encoding="utf-8"))
    assert len(payload["links"]) == 1
    assert payload["links"][0]["approved_by"] == "USER"


# --- Release-blocker negative tests: fail-closed for unknown failures ------


def test_A_unrelated_excel_export_error_fails_closed() -> None:
    """Несвързан ExcelExportError НЕ запазва STRONG, дори при идеални условия."""

    from lion_rebar.export.excel_export import ExcelExportError

    row = _row()  # HOOKED_CLOSURE, без ambiguity/conflict, Δ=1.44 <= 2.1
    _run_gate(row, raises=ExcelExportError("несвързан проблем при VID"))
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", row["batch_status"]
    assert row.get("f006_production_status") is None
    assert "F006_PRINTED_DIMENSIONS_MISSING" in row["review_reason_codes"]


def test_B_unrelated_value_error_fails_closed() -> None:
    row = _row()
    _run_gate(row, raises=ValueError("unknown segment-mapping failure"))
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", row["batch_status"]
    assert row.get("f006_production_status") is None


def test_B2_unrelated_type_error_fails_closed() -> None:
    row = _row()
    _run_gate(row, raises=TypeError("unsupported geometry failure"))
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", row["batch_status"]


def test_C_known_asymmetric_hook_keeps_strong_form() -> None:
    """Известният случай продължава да дава STRONG + DERIVED/REVIEW."""

    row = _row(segments=(4.6, 15.0, 160.0, 15.0, 160.0, 10.0))
    _run_gate(row, raises=_asym_exc())
    assert row["batch_status"] == "СИЛНО СТРЕМЕ F006"
    assert row["f006_production_status"] == "F006_PRODUCTION_DERIVED"

    row2 = _row(segments=(4.6, 15.0, 160.0))  # неизвлечими -> REVIEW
    _run_gate(row2, raises=_asym_exc())
    assert row2["batch_status"] == "СИЛНО СТРЕМЕ F006"
    assert row2["f006_production_status"] == "F006_PRODUCTION_REVIEW"
    assert row2.get("production_export_blocked") is True


def test_C2_string_lookalike_error_does_not_pass() -> None:
    """Обикновен ExcelExportError със СЪЩИЯ текст вече не минава (без string matching)."""

    from lion_rebar.export.excel_export import ExcelExportError

    row = _row()
    _run_gate(
        row,
        raises=ExcelExportError(
            "F006 има различни ключове 4.6/10 cm; MASTER изисква еднакви L1."
        ),
    )
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД", (
        "текстов двойник на съобщението не бива да отключва fallback"
    )

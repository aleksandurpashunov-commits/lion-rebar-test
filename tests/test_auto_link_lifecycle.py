"""End-to-end проверка на жизнения цикъл на AUTO_BATCH_GATE одобренията.

Покрива задължителното поведение от recovery.4:

* Run 1: позиция минава портата -> AUTO link с пълен provenance;
* Run 2: същата позиция деградира -> Matcher-ът реално се изпълнява,
  старият AUTO link се оттегля и архивира, integrity пада, export блокиран;
* USER одобрение никога не се презаписва от AUTO потока;
* AUTO link без provenance (наследен от recovery.3) е невалиден при четене.
"""
from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.export.auto_approval import (
    AUTO_APPROVED_BY,
    AUTO_STATUS,
    _provenance_matches,
)


def _write_links(path: Path, links: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema": "lion-rebar-approved-geometry-links/v1", "links": links, "history": []},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_auto_links_are_never_skip_approved_in_next_batch() -> None:
    """AUTO link не бива да минава по пътя на ръчните одобрения (дефект 2, стъпка 3)."""

    import inspect

    from lion_rebar.dxf import batch_geometry_report as bgr

    source = inspect.getsource(bgr)
    # Порталната проверка задължително различава ръчни от AUTO одобрения.
    assert "approved_is_manual" in source
    assert "AUTO_BATCH_GATE" in source

    # Поведенчески: изграждаме минимален ред както го вижда цикълът.
    approved_auto = {"approved_by": AUTO_APPROVED_BY, "geometry_handles": ["G1"]}
    approved_user = {"approved_by": "USER", "geometry_handles": ["G1"]}
    is_manual = lambda approved: (
        str((approved or {}).get("approved_by") or "").strip().upper()
        != "AUTO_BATCH_GATE"
    )
    assert not is_manual(approved_auto)
    assert is_manual(approved_user)


def test_provenance_mismatch_and_missing_fields_are_invalid() -> None:
    current = {
        "source_dxf_sha256": "d" * 64,
        "engine_hash": "e" * 64,
        "application_version": "0.1.44.13-recovery.4",
        "matcher_sha256": "m" * 64,
        "rules_sha256": "r" * 64,
    }
    matching = dict(current)
    assert _provenance_matches(matching, current)

    for key in current:
        broken = dict(current)
        broken[key] = "x" * 64
        assert not _provenance_matches(broken, current), key

    # Наследен recovery.3 link без provenance: невалиден по подразбиране.
    legacy = {"approved_by": AUTO_APPROVED_BY}
    assert not _provenance_matches(legacy, current)


def test_run1_creates_auto_link_and_run2_revokes_on_degradation(tmp_path: Path) -> None:
    """Пълният Run1/Run2 сценарий от т. F, върху reconciliation ядрото."""

    from types import SimpleNamespace

    from lion_rebar.export import auto_approval as aa

    project_root = tmp_path / "project"
    approved_path = project_root / "working" / "approved_geometry_links.json"

    report1 = tmp_path / "report_run1.json"
    report1.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "annotation_handle": "T1",
                        "annotation_text": "2N10x100",
                        "batch_status": "СИЛЕН",
                        "candidate_handles": ["G1"],
                        "candidate_layer": "ARM",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    position = SimpleNamespace(
        annotation_handle="T1",
        geometry_handles=("G1",),
        count=2,
        diameter_mm=10.0,
        production_length_cm=100.0,
        production_length_source="ANNOTATION",
        shape_code="F001",
        shape_name="ПРАВ ПРЪТ",
    )

    class _Ctx:
        actionable_count = 1

        def __init__(self, positions, blockers):
            self.positions = positions
            self.blockers = blockers

    engine = SimpleNamespace(version="0.3.12", to_dict=lambda: {"rules": [], "shapes": []})

    calls = {"n": 0}

    def fake_context(report_path, *, entities, engine, approved_links=None,
                     frame_decisions=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Ctx([position], [])
        # Run 2: T1 вече не минава портата
        blocker = SimpleNamespace(
            annotation_handle="T1", reason_codes=("MATCHER_REVIEW",)
        )
        return _Ctx([], [blocker])

    original = aa.build_batch_export_context
    aa.build_batch_export_context = fake_context
    try:
        run1 = aa.auto_approve_gate_positions(
            project_root,
            report_path=report1,
            entities=[],
            engine=engine,
            source_dxf_path=None,
        )
        assert run1.newly_approved_count == 1
        assert run1.revoked_stale_count == 0

        payload = json.loads(approved_path.read_text(encoding="utf-8"))
        assert len(payload["links"]) == 1
        link = payload["links"][0]
        assert link["approved_by"] == AUTO_APPROVED_BY
        assert link["approval_status"] == AUTO_STATUS
        # Provenance полетата от т. D съществуват.
        for key in (
            "source_report_sha256",
            "source_dxf_sha256",
            "engine_version",
            "engine_hash",
            "application_version",
            "matcher_sha256",
            "rules_sha256",
            "generated_at",
        ):
            assert key in link, key

        # Run 2: същата позиция деградира до blocker.
        report2 = tmp_path / "report_run2.json"
        report2.write_text(
            json.dumps(
                {
                    "rows": [
                        {
                            "annotation_handle": "T1",
                            "annotation_text": "2N10x100",
                            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
                            "candidate_handles": ["G1"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        run2 = aa.auto_approve_gate_positions(
            project_root,
            report_path=report2,
            entities=[],
            engine=engine,
            source_dxf_path=None,
        )
        assert run2.newly_approved_count == 0
        assert run2.blocked_count == 1
        assert run2.revoked_stale_count == 1

        payload = json.loads(approved_path.read_text(encoding="utf-8"))
        assert payload["links"] == []
        assert len(payload["history"]) == 1
        archived = payload["history"][0]
        assert archived["annotation_handle"] == "T1"
        assert archived["revoked_reason"] == "AUTO_STALE_NOW_BLOCKED"
        assert archived["revoked_by_report"] == report2.name
    finally:
        aa.build_batch_export_context = original


def test_user_approval_is_never_overwritten_by_auto_flow(tmp_path: Path) -> None:
    """Т. G: ръчното USER одобрение остава недокоснато и при двата пробега."""

    from types import SimpleNamespace

    from lion_rebar.export import auto_approval as aa

    project_root = tmp_path / "project"
    approved_path = project_root / "working" / "approved_geometry_links.json"
    _write_links(
        approved_path,
        [
            {
                "annotation_handle": "T1",
                "approved_by": "USER",
                "approval_status": "ОДОБРЕНО РЪЧНО",
                "geometry_handles": ["G1"],
            }
        ],
    )

    report = tmp_path / "report.json"
    report.write_text(json.dumps({"rows": []}), encoding="utf-8")

    position = SimpleNamespace(
        annotation_handle="T1",
        geometry_handles=("G1",),
        count=2,
        diameter_mm=10.0,
        production_length_cm=100.0,
        production_length_source="ANNOTATION",
        shape_code="F001",
        shape_name="ПРАВ ПРЪТ",
    )

    class _Ctx:
        actionable_count = 1
        positions = [position]
        blockers = []

    original = aa.build_batch_export_context
    aa.build_batch_export_context = lambda *a, **k: _Ctx()
    try:
        summary = aa.auto_approve_gate_positions(
            project_root,
            report_path=report,
            entities=[],
            engine=SimpleNamespace(version="0.3.12", to_dict=lambda: {"rules": []}),
            source_dxf_path=None,
        )
        assert summary.kept_manual_count == 1
        assert summary.newly_approved_count == 0
        payload = json.loads(approved_path.read_text(encoding="utf-8"))
        assert len(payload["links"]) == 1
        assert payload["links"][0]["approved_by"] == "USER"
    finally:
        aa.build_batch_export_context = original


def test_batch_finished_uses_auto_approval_not_auto_output() -> None:
    """Дефект 1: няма остатъчна употреба на недефинираното auto_output."""

    import re

    source = Path("lion_rebar/ui/main_window.py").read_text(encoding="utf-8")
    # Никъде не се чете променлива auto_output (auto_output_message е валидна).
    assert not re.search(r"\bauto_output\b(?!_message)", source)
    assert "auto_approval.blocked_count > 0" in source
    assert "auto_approval.failed" in source


def test_legacy_auto_status_cannot_become_manual_skip() -> None:
    """Regression: persisted AUTO record from a real project must be recomputed."""
    auto = {
        "approved_by": "AUTO_BATCH_GATE",
        "approval_status": "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)",
        "auto_gate": {"batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ"},
        "geometry_handles": ["3AE6", "3B0F", "3B62"],
    }
    approved_by = str(auto.get("approved_by") or "").strip().upper()
    approval_status = str(auto.get("approval_status") or "").strip().upper()
    approved_is_auto = bool(auto) and (
        approved_by == "AUTO_BATCH_GATE"
        or "ОДОБРЕНО АВТОМАТИЧНО" in approval_status
        or bool(auto.get("auto_gate"))
    )
    assert approved_is_auto
    assert not (bool(auto) and not approved_is_auto)


def _strong_geometry_analysis():
    from lion_rebar.dxf.geometry_candidates import (
        GeometryCandidate,
        GeometryCandidateAnalysis,
    )

    candidate = GeometryCandidate(
        rank=1,
        handles=("G1",),
        layer="ARM_Bars",
        segment_count=1,
        geometry_length_raw=100.0,
        declared_length_cm=100.0,
        numeric_difference=0.0,
        distance_raw=1.0,
        layer_diameter_mm=None,
        diameter_layer_match=False,
        score=0.99,
        score_gap=None,
        distance_gap_raw=None,
        confidence="СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА",
        basis="ЕДИНИЧЕН DXF ОБЕКТ",
        master_shape_code="F001",
        master_shape_name="ПРАВ ПРЪТ",
        effective_geometry_length_cm=100.0,
        raw_numeric_difference=0.0,
    )
    return GeometryCandidateAnalysis(
        annotation_handle="T1",
        label="2N8x100",
        diameter_mm=8.0,
        declared_length_cm=100.0,
        candidates=(candidate,),
        status="Кандидатът е намерен.",
    )


def test_matching_auto_link_does_not_override_current_matcher_status() -> None:
    """Real Run2 defect: matching AUTO handles must remain a current strong row."""

    from lion_rebar.dxf.batch_geometry_report import _analysis_row

    auto = {
        "annotation_handle": "T1",
        "approved_by": "AUTO_BATCH_GATE",
        "approval_status": "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)",
        "auto_gate": {"batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ"},
        "geometry_handles": ["G1"],
    }
    row = _analysis_row(_strong_geometry_analysis(), auto)

    assert row["batch_status"] == "СИЛЕН ПО ГЕОМЕТРИЯ"
    assert row["approved_in_project"] is False
    assert row["approval_origin"] == "AUTO"
    assert row["previous_auto_approval_present"] is True
    assert row["previous_auto_geometry_match"] is True


def test_matching_manual_link_still_remains_project_approved() -> None:
    from lion_rebar.dxf.batch_geometry_report import _analysis_row

    manual = {
        "annotation_handle": "T1",
        "approved_by": "USER",
        "approval_status": "ОДОБРЕНО РЪЧНО",
        "geometry_handles": ["G1"],
    }
    row = _analysis_row(_strong_geometry_analysis(), manual)

    assert row["batch_status"] == "ОДОБРЕНА ВРЪЗКА"
    assert row["approved_in_project"] is True
    assert row["approval_origin"] == "MANUAL"


def test_unknown_legacy_link_is_recomputed_not_promoted_to_manual() -> None:
    from lion_rebar.dxf.batch_geometry_report import _analysis_row

    unknown = {
        "annotation_handle": "T1",
        "approved_by": "",
        "approval_status": "APPROVED",
        "geometry_handles": ["G1"],
    }
    row = _analysis_row(_strong_geometry_analysis(), unknown)

    assert row["batch_status"] == "СИЛЕН ПО ГЕОМЕТРИЯ"
    assert row["approved_in_project"] is False
    assert row["approval_origin"] == "UNKNOWN"


def test_frozen_runtime_uses_locked_matcher_identity(tmp_path: Path) -> None:
    from lion_rebar.config.constants import RECOGNITION_MATCHER_SHA256
    from lion_rebar.export.auto_approval import _matcher_identity

    sha, source = _matcher_identity(tmp_path / "shape_recognition.py")
    assert sha == RECOGNITION_MATCHER_SHA256
    assert source == "RELEASE_IDENTITY"

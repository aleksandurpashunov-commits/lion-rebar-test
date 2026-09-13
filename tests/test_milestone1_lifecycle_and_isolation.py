"""Regression тестове за Milestone 1, част 2 — P0-3 / P0-5 / P0-6 + рамки.

Без Qt и без ezdxf: цялата логика е в чисти модули, за да е доказуема в CI.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.export.frame_decisions import (
    FrameDecisionError,
    INTERNAL_ONLY_CATEGORIES,
    SELECTABLE_CATEGORIES,
    audit_trail,
    internal_only_decisions,
    load_frame_decisions,
    record_frame_decision,
    withdraw_frame_decision,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.final_control_lifecycle import (
    STATUS_INVALID,
    STATUS_VALID,
    assert_export_allowed,
    detect_production_impact,
    freeze_status,
    invalidate_final_control,
)
from lion_rebar.project.isolation import (
    assert_clean_for_new_project,
    audit_project_isolation,
    ledger_rows_isolated,
)
from lion_rebar.project.review_recovery import (
    ACTION_ASK_OPERATOR,
    ACTION_BLOCKED,
    ACTION_OPEN_LATEST,
    OPTION_CHOOSE_HISTORICAL,
    OPTION_RUN_ANALYSIS,
    decide_review_entry,
)

IDENT = DataIdentity(project_id="P1", source_sha256="a" * 64,
                     source_generation_id="G1", analysis_run_id="R1")


def _payload(**over):
    data = {"project_id": "P1", "source_sha256": "a" * 64,
            "source_generation_id": "G1", "analysis_run_id": "R1",
            "project_name": "Проект 1"}
    data.update(over)
    return data


def _freeze(root: Path, status: str = STATUS_VALID) -> Path:
    working = root / "working"
    working.mkdir(parents=True, exist_ok=True)
    path = working / "final_control_freeze.json"
    path.write_text(json.dumps({"status": status, "artifacts": []}), encoding="utf-8")
    return path


# ------------------------------------------------------------------ P0-3

def test_opens_latest_valid_review_without_file_dialog():
    decision = decide_review_entry(
        has_project=True, has_import=True, current=IDENT,
        candidates=[(Path("latest.json"), _payload())], project_name="Проект 1")
    assert decision.action == ACTION_OPEN_LATEST
    assert decision.opens_directly
    assert decision.report_path == Path("latest.json")


def test_stale_analysis_run_is_not_opened_silently():
    decision = decide_review_entry(
        has_project=True, has_import=True, current=IDENT,
        candidates=[(Path("old.json"), _payload(analysis_run_id="R0"))],
        project_name="Проект 1")
    assert decision.action == ACTION_ASK_OPERATOR
    assert decision.reason == "ONLY_STALE_REVIEWS"
    assert OPTION_RUN_ANALYSIS in decision.options
    assert OPTION_CHOOSE_HISTORICAL in decision.options


def test_other_project_report_is_rejected():
    decision = decide_review_entry(
        has_project=True, has_import=True, current=IDENT,
        candidates=[(Path("b.json"), _payload(project_id="P2"))],
        project_name="Проект 1")
    assert decision.action == ACTION_ASK_OPERATOR
    assert "PROJECT_ID_MISMATCH" in decision.message


def test_project_name_mismatch_is_rejected():
    decision = decide_review_entry(
        has_project=True, has_import=True, current=IDENT,
        candidates=[(Path("x.json"), _payload(project_name="Друг"))],
        project_name="Проект 1")
    assert "PROJECT_NAME_MISMATCH" in decision.message


def test_newest_valid_candidate_wins_over_stale():
    decision = decide_review_entry(
        has_project=True, has_import=True, current=IDENT,
        candidates=[(Path("stale.json"), _payload(source_generation_id="G0")),
                    (Path("good.json"), _payload())], project_name="Проект 1")
    assert decision.report_path == Path("good.json")


def test_no_candidates_asks_operator():
    decision = decide_review_entry(has_project=True, has_import=True,
                                   current=IDENT, candidates=[])
    assert decision.action == ACTION_ASK_OPERATOR
    assert decision.reason == "NO_REVIEW_FOR_CURRENT_RUN"


def test_missing_project_or_import_is_blocked():
    assert decide_review_entry(has_project=False, has_import=False, current=None,
                               candidates=[]).action == ACTION_BLOCKED
    assert decide_review_entry(has_project=True, has_import=False, current=IDENT,
                               candidates=[]).action == ACTION_BLOCKED


def test_unreadable_report_is_not_opened():
    decision = decide_review_entry(has_project=True, has_import=True, current=IDENT,
                                   candidates=[(Path("broken.json"), None)])
    assert decision.action == ACTION_ASK_OPERATOR
    assert "REPORT_UNREADABLE" in decision.message


# ------------------------------------------------------------------ P0-5

def test_detects_each_production_impacting_field():
    for field in ("master_shape_code", "production_length_cm", "geometry_handles",
                  "beam_name", "non_production_position"):
        impacted, fields = detect_production_impact(
            before={field: "a"}, after={field: "b"})
        assert impacted and field in fields


def test_non_production_field_change_does_not_invalidate():
    impacted, _ = detect_production_impact(before={"note": "x"}, after={"note": "y"})
    assert not impacted


def test_production_event_invalidates_freeze():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _freeze(root)
        assert freeze_status(root) == STATUS_VALID
        result = invalidate_final_control(root, event="ENGINEER_DECISION",
                                          actor="инженер")
        assert result.changed and result.status == STATUS_INVALID
        assert freeze_status(root) == STATUS_INVALID
        assert not result.export_allowed


def test_export_is_blocked_after_invalidation():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _freeze(root)
        assert assert_export_allowed(root) == []
        invalidate_final_control(root, event="NON_PRODUCTION_POSITION")
        problems = assert_export_allowed(root)
        assert problems and "FINAL_CONTROL_INVALID" in problems[0]


def test_export_blocked_when_no_final_control():
    with tempfile.TemporaryDirectory() as d:
        problems = assert_export_allowed(Path(d))
        assert problems and "NO_FINAL_CONTROL" in problems[0]


def test_invalidation_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _freeze(root)
        first = invalidate_final_control(root, event="ENGINEER_DECISION")
        second = invalidate_final_control(root, event="ENGINEER_DECISION")
        assert first.changed and not second.changed
        assert second.reason == "ALREADY_INVALID"


def test_invalidation_writes_audit_trail():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _freeze(root)
        invalidate_final_control(root, event="APPLY_TO_GROUP", actor="инженер",
                                 note="групово решение")
        log = root / "working" / "final_control_invalidations.jsonl"
        assert log.is_file()
        entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert entry["event"] == "APPLY_TO_GROUP"
        assert entry["actor"] == "инженер"


def test_freeze_records_reason_and_fields():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path = _freeze(root)
        invalidate_final_control(root, before={"production_length_cm": "100"},
                                 after={"production_length_cm": "120"})
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["status"] == STATUS_INVALID
        assert "production_length_cm" in payload["invalidated_fields"]
        assert payload["invalidated_at"]


# ------------------------------------------------------------------ P0-6

def test_clean_project_is_isolated():
    with tempfile.TemporaryDirectory() as d:
        report = audit_project_isolation(Path(d), IDENT)
        assert report.isolated and report.checked == 0


def test_foreign_artifact_is_detected():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        working = root / "working"
        working.mkdir()
        (working / "review_center_state.json").write_text(
            json.dumps(_payload(project_id="OTHER")), encoding="utf-8")
        report = audit_project_isolation(root, IDENT)
        assert not report.isolated
        assert report.violations[0].reason == "PROJECT_ID_MISMATCH"


def test_artifact_without_identity_is_not_silently_accepted():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "working").mkdir()
        (root / "working" / "approved_geometry_links.json").write_text(
            json.dumps({"links": []}), encoding="utf-8")
        report = audit_project_isolation(root, IDENT)
        assert not report.isolated
        assert report.violations[0].reason == "MISSING_DATA_IDENTITY"


def test_own_artifact_passes():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "working").mkdir()
        (root / "working" / "review_center_state.json").write_text(
            json.dumps(_payload()), encoding="utf-8")
        assert audit_project_isolation(root, IDENT).isolated


def test_ledger_rows_with_foreign_identity_are_flagged():
    rows = [_payload(), _payload(project_id="P2")]
    problems = ledger_rows_isolated(rows, IDENT)
    assert problems
    assert any("MULTIPLE_IDENTITIES_IN_LEDGER" in p for p in problems)


def test_ledger_rows_all_own_are_clean():
    assert ledger_rows_isolated([_payload(), _payload()], IDENT) == []


def test_new_project_must_not_inherit_artifacts():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "working").mkdir()
        (root / "working" / "final_integrity_ledger.csv").write_text("a\n", encoding="utf-8")
        problems = assert_clean_for_new_project(root)
        assert problems and "final_integrity_ledger.csv" in problems[0]


# ------------------------------------------- operator frame workflow

def test_operator_decision_requires_note_and_author():
    with tempfile.TemporaryDirectory() as d:
        for bad in ({"note": ""}, {"decided_by": ""}):
            kwargs = {"marker_handle": "M1", "category_code": "BEAMS",
                      "decided_by": "инженер", "note": "мотив",
                      "identity": IDENT, "frame_is_unknown": True}
            kwargs.update(bad)
            try:
                record_frame_decision(Path(d), **kwargs)
            except FrameDecisionError:
                continue
            raise AssertionError(f"очаква се отказ за {bad}")


def test_operator_cannot_decide_a_proven_frame():
    with tempfile.TemporaryDirectory() as d:
        try:
            record_frame_decision(Path(d), marker_handle="M1", category_code="BEAMS",
                                  decided_by="инженер", note="мотив",
                                  identity=IDENT, frame_is_unknown=False)
        except FrameDecisionError:
            return
        raise AssertionError("доказана рамка не бива да се презаписва")


def test_decision_is_persisted_and_audited():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        record_frame_decision(root, marker_handle="M1", category_code="BEAMS",
                              decided_by="инженер", note="рамка за греди",
                              identity=IDENT, frame_is_unknown=True)
        assert load_frame_decisions(root, IDENT) == {"M1": "BEAMS"}
        trail = audit_trail(root)
        assert trail and trail[0]["action"] == "DECIDE"
        assert trail[0]["note"] == "рамка за греди"


def test_decision_from_other_identity_is_not_inherited():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        record_frame_decision(root, marker_handle="M1", category_code="BEAMS",
                              decided_by="инженер", note="мотив",
                              identity=IDENT, frame_is_unknown=True)
        other = DataIdentity(project_id="P1", source_sha256="a" * 64,
                             source_generation_id="G2", analysis_run_id="R2")
        assert load_frame_decisions(root, other) == {}


def test_withdrawal_is_audited():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        record_frame_decision(root, marker_handle="M1", category_code="BEAMS",
                              decided_by="инженер", note="мотив",
                              identity=IDENT, frame_is_unknown=True)
        assert withdraw_frame_decision(root, marker_handle="M1",
                                       withdrawn_by="инженер", note="грешка")
        assert load_frame_decisions(root, IDENT) == {}
        actions = [row["action"] for row in audit_trail(root)]
        assert actions == ["DECIDE", "WITHDRAW"]


def test_verticals_is_a_full_category_with_its_own_sheet():
    """Product Owner решение (2026-08-24): вертикалите получават свой лист.

    Реален случай: "АРМИРОВКА ВЕРТИКАЛИ ДО КОТА +8,90" блокираше
    автоматичния Excel като неопределена категория.
    """

    from lion_rebar.export.frame_detection import (
        CATEGORY_ORDER,
        CATEGORY_SHEET_NAMES,
        classify_title,
    )

    assert "VERTICALS" in SELECTABLE_CATEGORIES
    assert "VERTICALS" not in INTERNAL_ONLY_CATEGORIES
    assert CATEGORY_SHEET_NAMES["VERTICALS"] == "вертикали"
    assert classify_title("АРМИРОВКА ВЕРТИКАЛИ ДО КОТА +8,90") == ("VERTICALS", "HIGH")
    # Одобреният ред: ... детайли, греди, вертикали
    order = [code for code, _ in sorted(CATEGORY_ORDER.items(), key=lambda kv: kv[1])]
    assert order[:7] == ["BOTTOM", "BOTTOM_EXTRA", "TOP", "TOP_EXTRA",
                         "DETAILS", "BEAMS", "VERTICALS"]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        record_frame_decision(root, marker_handle="M9", category_code="VERTICALS",
                              decided_by="инженер", note="армировка вертикали",
                              identity=IDENT, frame_is_unknown=True)
        assert load_frame_decisions(root, IDENT) == {"M9": "VERTICALS"}
        assert internal_only_decisions(root, IDENT) == {}


def test_unknown_category_code_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        try:
            record_frame_decision(Path(d), marker_handle="M1",
                                  category_code="NOT_A_CATEGORY",
                                  decided_by="инженер", note="мотив",
                                  identity=IDENT, frame_is_unknown=True)
        except FrameDecisionError:
            return
        raise AssertionError("невалидна категория трябва да се откаже")

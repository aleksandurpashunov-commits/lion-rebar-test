from __future__ import annotations

from lion_rebar.review_center_model import (
    build_review_evidence_note,
    is_review_row_resolved,
    review_queue_rows,
)


def _row(
    handle: str,
    status: str,
    *,
    candidate_handles: tuple[str, ...] = ("G1",),
) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": f"8N10x{handle[-1]}00",
        "batch_status": status,
        "candidate_handles": list(candidate_handles),
        "alternative_candidates": [],
        "master_shape_code": "F015",
        "segment_count": len(candidate_handles),
        "candidate_layer": "ARM-GORNA",
        "geometry_length_raw": 99.4,
        "declared_length_cm": 100,
        "raw_numeric_difference": -0.6,
        "numeric_difference": -0.6,
        "data_quality_flags": [],
    }


def test_live_work_queues_remove_every_final_decision() -> None:
    manually_assigned = _row("A1", "ЗА РЪЧЕН ПРЕГЛЕД")
    rejected = _row("A2", "НЯМА КАНДИДАТ", candidate_handles=())
    still_open = _row("A3", "СИЛЕН")
    approved_in_report = _row("A4", "ОДОБРЕНА ВРЪЗКА")
    f006_bulk = _row("A5", "СИЛНО СТРЕМЕ F006")
    rows = [manually_assigned, rejected, still_open, approved_in_report, f006_bulk]
    decisions = {
        "A1": {"decision": "MANUAL_ASSIGNED"},
        "A2": {"decision": "NO_VALID_CANDIDATE"},
        "A5": {"decision": "APPROVED_BULK_AFTER_F006_SAMPLE"},
    }

    work = review_queue_rows(
        rows,
        queue_code="WORK",
        decisions=decisions,
        approved_handles={"A1"},
    )
    manual = review_queue_rows(
        rows,
        queue_code="MANUAL",
        decisions=decisions,
        approved_handles={"A1"},
    )
    rejected_history = review_queue_rows(
        rows,
        queue_code="REJECTED",
        decisions=decisions,
        approved_handles={"A1"},
    )
    approved_history = review_queue_rows(
        rows,
        queue_code="APPROVED",
        decisions=decisions,
        approved_handles={"A1"},
    )

    assert [row["annotation_handle"] for row in work] == ["A2", "A3"]
    assert [row["annotation_handle"] for row in manual] == ["A2"]
    assert [row["annotation_handle"] for row in rejected_history] == ["A2"]
    assert {row["annotation_handle"] for row in approved_history} == {"A1", "A4", "A5"}
    assert is_review_row_resolved(approved_in_report) is True


def test_work_queue_prioritizes_control_sample_then_unresolved_exceptions() -> None:
    strong = _row("A1", "СИЛЕН")
    no_candidate = _row("A2", "НЯМА КАНДИДАТ", candidate_handles=())
    sampled = _row("A3", "СИЛЕН")

    work = review_queue_rows(
        [strong, no_candidate, sampled],
        queue_code="WORK",
        sample_handles=("A3",),
    )

    assert [row["annotation_handle"] for row in work] == ["A3", "A2", "A1"]


def test_technical_note_is_generated_from_reproducible_evidence() -> None:
    row = _row("A1", "ЗА РЪЧЕН ПРЕГЛЕД", candidate_handles=("9C6", "9C7", "9CF", "A29"))
    row["annotation_text"] = "8N12x483"
    row["geometry_length_raw"] = 481.84
    row["declared_length_cm"] = 483
    row["raw_numeric_difference"] = -1.16

    note = build_review_evidence_note(
        row,
        reason_label="Не е разпознато автоматично",
    )

    assert "Надпис 8N12x483" in note
    assert "кандидат 9C6, 9C7, 9CF, A29" in note
    assert "MASTER F015" in note
    assert "DXF L=481.84 cm" in note
    assert "Δ=-1.16 cm" in note
    assert "причина: Не е разпознато автоматично" in note


def test_ai_queues_keep_clean_control_separate_from_true_exceptions() -> None:
    control = _row("A1", "СИЛЕН")
    control["matcher_disposition"] = "CONTROL"
    manual = _row("A2", "ЗА РЪЧЕН ПРЕГЛЕД")
    manual["matcher_disposition"] = "MANUAL"
    unknown = _row("A3", "НЯМА КАНДИДАТ", candidate_handles=())
    unknown["matcher_disposition"] = "UNKNOWN"

    control_rows = review_queue_rows(
        [control, manual, unknown],
        queue_code="AI_CONTROL",
    )
    exception_rows = review_queue_rows(
        [control, manual, unknown],
        queue_code="AI_EXCEPTIONS",
    )

    assert [row["annotation_handle"] for row in control_rows] == ["A1"]
    assert {row["annotation_handle"] for row in exception_rows} == {"A2", "A3"}


# --------------------------------------------------- Фаза 2 опашка консолидация

def test_problems_queue_unions_every_engineering_case_without_duplicates() -> None:
    """"PROBLEMS" замества 6-те стари диагностични опашки в едно меню.

    Всяка от старите опашки остава реално изпълнима поотделно (обратна
    съвместимост) — сливането е обединение на резултатите им, не нова
    филтърна логика.
    """

    risk = _row("A1", "РИСК")
    no_candidate = _row("A2", "НЯМА КАНДИДАТ", candidate_handles=())
    manual = _row("A3", "ЗА РЪЧЕН ПРЕГЛЕД")
    conflict = _row("A4", "СИЛЕН")
    conflict["global_conflict"] = True
    missing_master = _row("A5", "СИЛЕН")
    missing_master["master_shape_code"] = ""
    unrelated_strong = _row("A6", "СИЛЕН")
    rows = [risk, no_candidate, manual, conflict, missing_master, unrelated_strong]

    problems = review_queue_rows(rows, queue_code="PROBLEMS")

    assert {row["annotation_handle"] for row in problems} == {
        "A1", "A2", "A3", "A4", "A5",
    }
    assert "A6" not in {row["annotation_handle"] for row in problems}
    # Никоя позиция не се дублира, дори да отговаря на повече от една стара категория.
    assert len(problems) == len({row["annotation_handle"] for row in problems})


def test_strong_review_queue_unions_all_automatic_strong_categories() -> None:
    strong = _row("A1", "СИЛЕН")
    geometry_strong = _row("A2", "СИЛЕН ПО ГЕОМЕТРИЯ")
    f006_strong = _row("A3", "СИЛНО СТРЕМЕ F006")
    control = _row("A4", "ЗА РЪЧЕН ПРЕГЛЕД")
    control["matcher_disposition"] = "CONTROL"
    unrelated_risk = _row("A5", "РИСК")
    rows = [strong, geometry_strong, f006_strong, control, unrelated_risk]

    strong_review = review_queue_rows(rows, queue_code="STRONG_REVIEW")

    assert {row["annotation_handle"] for row in strong_review} == {
        "A1", "A2", "A3", "A4",
    }
    assert "A5" not in {row["annotation_handle"] for row in strong_review}


def test_consolidated_queues_still_respect_resolved_decisions() -> None:
    """Обединените опашки остават "work" опашки — решена позиция изчезва."""

    risk = _row("A1", "РИСК")
    rows = [risk]

    still_open = review_queue_rows(rows, queue_code="PROBLEMS")
    assert [row["annotation_handle"] for row in still_open] == ["A1"]

    resolved = review_queue_rows(
        rows, queue_code="PROBLEMS",
        decisions={"A1": {"decision": "ENGINEER_RESOLVED"}},
    )
    assert resolved == []

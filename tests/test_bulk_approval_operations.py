"""P0-2 — Undo/audit trail за масовите одобрения (Project Instructions, т. 12).

Преди тази поправка четирите "Приеми останалите ..." бутона в Review Center
(силни / по геометрия / F006 / финално потвърждение) нямаха никакъв път
назад: грешен масов клик можеше да се оправи само ръчно, позиция по
позиция. Тези тестове изпълняват реалния backend модул
``lion_rebar.review.bulk_approval_operations`` -- запис, зареждане,
връщане, отказ при повторно връщане, отказ при връщане на не-последна
операция, отказ след production export.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.review.bulk_approval_operations import (
    BulkApprovalError,
    active_bulk_operations,
    load_bulk_operations,
    record_bulk_operation,
    undo_bulk_operation,
)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_record_and_undo_restores_both_files_byte_exact(tmp_path: Path):
    ledger = tmp_path / "working" / "approved_geometry_links.json"
    state = tmp_path / "working" / "review_center_state.json"
    ledger_before = b'{"links": []}'
    state_before = b'{"decisions": {}}'
    _write(ledger, ledger_before)
    _write(state, state_before)

    operation_id = record_bulk_operation(
        tmp_path,
        mode="GEOMETRY_SAMPLE",
        applied_by="Тестов инженер",
        affected_handles=["H1", "H2"],
        ledger_snapshot=ledger_before,
        state_snapshot=state_before,
    )
    assert operation_id

    # Симулира реалната последователност: операцията се записва, ПОСЛЕ
    # двата файла се презаписват с новото (масово одобрено) съдържание.
    _write(ledger, b'{"links": [{"annotation_handle": "H1"}, {"annotation_handle": "H2"}]}')
    _write(state, b'{"decisions": {"H1": {"decision": "APPROVED_BULK_AFTER_GEOMETRY_SAMPLE"}}}')

    active = active_bulk_operations(tmp_path)
    assert len(active) == 1
    assert active[0]["operation_id"] == operation_id

    restored_count = undo_bulk_operation(tmp_path, operation_id, undone_by="Тестов инженер")
    assert restored_count == 2
    assert ledger.read_bytes() == ledger_before
    assert state.read_bytes() == state_before

    # Операцията вече е върната -- не е активна, но остава в журнала.
    assert active_bulk_operations(tmp_path) == []
    all_ops = load_bulk_operations(tmp_path)
    assert any(op.get("action") == "UNDO_BULK_APPROVE" for op in all_ops)


def test_missing_files_before_operation_are_deleted_on_undo(tmp_path: Path):
    """Ако файловете не са съществували преди операцията, undo трябва да ги трие, не да ги празни."""

    operation_id = record_bulk_operation(
        tmp_path,
        mode="F006_SAMPLE",
        applied_by="Инж.",
        affected_handles=["H9"],
        ledger_snapshot=None,
        state_snapshot=None,
    )
    ledger = tmp_path / "working" / "approved_geometry_links.json"
    state = tmp_path / "working" / "review_center_state.json"
    _write(ledger, b'{"links": [{"annotation_handle": "H9"}]}')
    _write(state, b'{"decisions": {"H9": {"decision": "APPROVED_BULK_AFTER_F006_SAMPLE"}}}')

    undo_bulk_operation(tmp_path, operation_id, undone_by="Инж.")
    assert not ledger.exists()
    assert not state.exists()


def test_cannot_undo_the_same_operation_twice(tmp_path: Path):
    operation_id = record_bulk_operation(
        tmp_path, mode="STRICT_SAMPLE", applied_by="Инж.",
        affected_handles=["H1"], ledger_snapshot=None, state_snapshot=None,
    )
    undo_bulk_operation(tmp_path, operation_id, undone_by="Инж.")
    with pytest.raises(BulkApprovalError):
        undo_bulk_operation(tmp_path, operation_id, undone_by="Инж.")


def test_can_only_undo_the_last_active_operation(tmp_path: Path):
    first_id = record_bulk_operation(
        tmp_path, mode="STRICT_SAMPLE", applied_by="Инж.",
        affected_handles=["H1"], ledger_snapshot=None, state_snapshot=None,
    )
    record_bulk_operation(
        tmp_path, mode="GEOMETRY_SAMPLE", applied_by="Инж.",
        affected_handles=["H2"], ledger_snapshot=None, state_snapshot=None,
    )
    with pytest.raises(BulkApprovalError):
        undo_bulk_operation(tmp_path, first_id, undone_by="Инж.")


def test_undo_refused_after_production_export(tmp_path: Path):
    operation_id = record_bulk_operation(
        tmp_path, mode="FINAL_USER_CONFIRMATION", applied_by="Инж.",
        affected_handles=["H1"], ledger_snapshot=None, state_snapshot=None,
    )
    with pytest.raises(BulkApprovalError):
        undo_bulk_operation(
            tmp_path, operation_id, undone_by="Инж.",
            exported_after=lambda _op: True,
        )


def test_record_rejects_unknown_mode(tmp_path: Path):
    with pytest.raises(BulkApprovalError):
        record_bulk_operation(
            tmp_path, mode="NOT_A_REAL_MODE", applied_by="Инж.",
            affected_handles=["H1"], ledger_snapshot=None, state_snapshot=None,
        )

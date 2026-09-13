from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.refresh_auto_production_dimensions_rc32 import _missing_auto_vectors
from tools.refresh_auto_production_dimensions_rc32 import (
    _backup_recovery_files,
    _refresh_action,
    _restore_recovery_files,
)


def _position(handle: str, shape: str, segments=()):
    return SimpleNamespace(
        annotation_handle=handle,
        shape_code=shape,
        segment_lengths_cm=tuple(segments),
    )


def test_missing_auto_vectors_selects_bent_auto_links_only() -> None:
    links = {
        "F1": {"approved_by": "AUTO_BATCH_GATE"},
        "U1": {"approved_by": "AUTO_BATCH_GATE"},
        "M1": {"approved_by": "USER"},
    }
    positions = {
        "F1": _position("F1", "F001"),
        "U1": _position("U1", "F003", (15, 710, 15)),
        "M1": _position("M1", "F002", (40, 40)),
    }
    missing = _missing_auto_vectors(links, positions)
    assert list(missing) == ["U1"]


def test_missing_auto_vectors_refuses_unproved_repair() -> None:
    with pytest.raises(RuntimeError, match="no production segment vector"):
        _missing_auto_vectors(
            {"U1": {"approved_by": "AUTO_BATCH_GATE"}},
            {"U1": _position("U1", "F003")},
        )


def test_apply_requires_expected_count_and_zero_is_no_op() -> None:
    with pytest.raises(RuntimeError, match="mandatory"):
        _refresh_action(
            missing_count=1,
            expected_refresh_count=None,
            apply=True,
        )
    assert (
        _refresh_action(
            missing_count=0,
            expected_refresh_count=0,
            apply=True,
        )
        == "NO_OP"
    )


def test_recovery_rollback_restores_existing_and_removes_created_files(
    tmp_path,
) -> None:
    working = tmp_path / "working"
    backup = working / "recovery_backups" / "backup"
    working.mkdir()
    backup.mkdir(parents=True)
    approved = working / "approved_geometry_links.json"
    approved.write_text("before", encoding="utf-8")
    old_auto_backup = working / "approved_geometry_links.before_auto_old.json"
    old_auto_backup.write_text("old", encoding="utf-8")

    existed, auto_before = _backup_recovery_files(working, backup)
    approved.write_text("after", encoding="utf-8")
    (working / "final_control_freeze.json").write_text("created", encoding="utf-8")
    new_auto_backup = working / "approved_geometry_links.before_auto_new.json"
    new_auto_backup.write_text("new", encoding="utf-8")

    _restore_recovery_files(
        working,
        backup,
        existed=existed,
        auto_backups_before=auto_before,
    )

    assert approved.read_text(encoding="utf-8") == "before"
    assert not (working / "final_control_freeze.json").exists()
    assert old_auto_backup.is_file()
    assert not new_auto_backup.exists()

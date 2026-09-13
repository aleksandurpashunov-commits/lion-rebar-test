from __future__ import annotations

import os
from pathlib import Path

import pytest

from lion_rebar.export.bundle import replace_export_bundle


def test_replace_export_bundle_replaces_all_files(tmp_path: Path) -> None:
    old_a = tmp_path / "a.xlsx"
    old_b = tmp_path / "b.dxf"
    new_a = tmp_path / "temp_a.xlsx"
    new_b = tmp_path / "temp_b.dxf"
    old_a.write_text("old-a", encoding="utf-8")
    old_b.write_text("old-b", encoding="utf-8")
    new_a.write_text("new-a", encoding="utf-8")
    new_b.write_text("new-b", encoding="utf-8")

    replace_export_bundle(((new_a, old_a), (new_b, old_b)))

    assert old_a.read_text(encoding="utf-8") == "new-a"
    assert old_b.read_text(encoding="utf-8") == "new-b"
    assert not new_a.exists()
    assert not new_b.exists()


def test_replace_export_bundle_rolls_back_on_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_a = tmp_path / "a.xlsx"
    old_b = tmp_path / "b.dxf"
    new_a = tmp_path / "temp_a.xlsx"
    new_b = tmp_path / "temp_b.dxf"
    old_a.write_text("old-a", encoding="utf-8")
    old_b.write_text("old-b", encoding="utf-8")
    new_a.write_text("new-a", encoding="utf-8")
    new_b.write_text("new-b", encoding="utf-8")

    real_replace = os.replace
    calls = 0

    def failing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal calls
        calls += 1
        # Two old files move to backup, first new file installs, then fail on
        # the second new file. Subsequent calls are rollback operations.
        if calls == 4:
            raise OSError("simulated disk error")
        real_replace(source, destination)

    monkeypatch.setattr("lion_rebar.export.bundle.os.replace", failing_replace)

    with pytest.raises(OSError, match="simulated disk error"):
        replace_export_bundle(((new_a, old_a), (new_b, old_b)))

    assert old_a.read_text(encoding="utf-8") == "old-a"
    assert old_b.read_text(encoding="utf-8") == "old-b"

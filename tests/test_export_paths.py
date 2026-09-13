from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.export.export_paths import build_paired_export_paths


def test_paired_export_temporary_files_are_siblings_of_finals(tmp_path: Path) -> None:
    paths = build_paired_export_paths(tmp_path / "ALFA.xlsx", "abc123")

    all_paths = (
        paths.excel,
        paths.excel_trace,
        paths.dxf,
        paths.dxf_trace,
        *paths.temporary_files,
    )
    assert {path.parent for path in all_paths} == {tmp_path}
    assert paths.temporary_excel.name.startswith(".lion_rebar_abc123_")
    assert paths.temporary_dxf.name.startswith(".lion_rebar_abc123_")
    assert paths.dxf.name == "ALFA_ПОЗИЦИИ.dxf"


def test_paired_export_path_normalizes_excel_extension(tmp_path: Path) -> None:
    paths = build_paired_export_paths(tmp_path / "ALFA", "token")
    assert paths.excel.name == "ALFA.xlsx"


def test_paired_export_path_rejects_empty_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="token"):
        build_paired_export_paths(tmp_path / "ALFA.xlsx", "---")

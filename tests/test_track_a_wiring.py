"""Track A — тестове за реалната свързаност (одит RC1: M1, V1, I1, F1)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.export.frame_decisions import record_frame_decision
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.final_control_lifecycle import STATUS_INVALID, freeze_status

IDENT = DataIdentity("P1", "a" * 64, "G1", "R1")
SOURCE = Path("lion_rebar/ui/export_worker.py").read_text(encoding="utf-8")


# ------------------------------------------------------------- M1

def _ledger(root: Path, rows: list[dict]) -> Path:
    import csv

    working = root / "working"
    working.mkdir(parents=True, exist_ok=True)
    path = working / "final_integrity_ledger.csv"
    fields = ["final_status", "geometry_handles_count", "geometry_provenance",
              "decision_reason_code"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    return path


def test_m1_non_production_count_comes_from_ledger():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_ew_probe", "lion_rebar/ui/export_worker.py")
    # UI модулът иска Qt; вместо импорт проверяваме поведението на брояча,
    # който е чиста функция, чрез копие на нейния код.
    namespace: dict = {}
    source = SOURCE
    start = source.index("_NON_PRODUCTION_LEDGER_STATUSES")
    end = source.index("def _csv_row_count")
    exec("from pathlib import Path\n" + source[start:end], namespace)  # noqa: S102
    counts = namespace["_ledger_counts"]

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _ledger(root, [
            {"final_status": "APPROVED", "geometry_handles_count": "2",
             "geometry_provenance": "DXF_HANDLES"},
            {"final_status": "NON_PRODUCTION_POSITION", "geometry_handles_count": "0",
             "geometry_provenance": "NONE_ENGINEER_OVERRIDE"},
            {"final_status": "APPROVED", "geometry_handles_count": "0",
             "geometry_provenance": "NONE_ENGINEER_OVERRIDE"},
        ])
        result = counts(path)
        assert result["rows"] == 3
        assert result["non_production"] == 1, "броячът трябва да идва от Ledger"
        assert result["without_geometry"] == 2


def test_m1_hardcoded_zero_is_gone():
    assert "non_production_position_count=0," not in SOURCE
    assert 'non_production_position_count=_ledger_counts(ledger)["non_production"]' in SOURCE


def test_m1_missing_ledger_returns_zeros_not_crash():
    namespace: dict = {}
    start = SOURCE.index("_NON_PRODUCTION_LEDGER_STATUSES")
    end = SOURCE.index("def _csv_row_count")
    exec("from pathlib import Path\n" + SOURCE[start:end], namespace)  # noqa: S102
    assert namespace["_ledger_counts"](Path("/nope/missing.csv"))["rows"] == 0


# ------------------------------------------------------------- V1

def test_v1_single_version_source():
    import lion_rebar
    from lion_rebar.config.constants import APP_VERSION

    assert lion_rebar.__version__ == APP_VERSION
    assert "recovery.6.3.1" not in lion_rebar.__version__


def test_v1_no_hardcoded_old_version_in_init():
    init = Path("lion_rebar/__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "0.1.44.19' not in init


# ------------------------------------------------------------- I1

def test_i1_isolation_is_wired_into_create_project():
    manager = Path("lion_rebar/project/manager.py").read_text(encoding="utf-8")
    assert "assert_clean_for_new_project" in manager
    index = manager.index("def create_project")
    body = manager[index:index + 2500]
    assert "assert_clean_for_new_project(root)" in body
    assert "ProjectError" in body


def test_i1_inherited_artifact_blocks_new_project():
    from lion_rebar.project.isolation import assert_clean_for_new_project

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "working").mkdir()
        (root / "working" / "final_integrity_ledger.csv").write_text("a\n",
                                                                     encoding="utf-8")
        problems = assert_clean_for_new_project(root)
        assert problems and "INHERITED_ARTIFACT" in problems[0]


def test_i1_clean_directory_passes():
    from lion_rebar.project.isolation import assert_clean_for_new_project

    with tempfile.TemporaryDirectory() as directory:
        assert assert_clean_for_new_project(Path(directory)) == []


# ------------------------------------------------------------- F1

def test_f1_operator_decisions_are_applied_on_export_path():
    assert "apply_operator_frame_decisions(" in SOURCE
    assert "load_frame_decisions(root, identity)" in SOURCE


def test_f1_frame_decision_invalidates_final_control():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "working").mkdir()
        (root / "working" / "final_control_freeze.json").write_text(
            json.dumps({"status": "VALID"}), encoding="utf-8")
        record_frame_decision(
            root, marker_handle="M1", category_code="BEAMS",
            decided_by="инженер", note="рамка за греди",
            identity=IDENT, frame_is_unknown=True)
        assert freeze_status(root) == STATUS_INVALID


def test_f1_verticals_is_now_a_full_export_category():
    """Product Owner решение 2026-08-24: вертикалите получават свой лист.

    До rc6 категорията беше вътрешна и решението за нея не се прилагаше при
    export. Реален чертеж („АРМИРОВКА ВЕРТИКАЛИ ДО КОТА +8,90“) блокираше
    автоматичния Excel и наложи продуктовото решение.
    """

    from lion_rebar.export.frame_decisions import (
        INTERNAL_ONLY_CATEGORIES,
        SELECTABLE_CATEGORIES,
        internal_only_decisions,
        load_frame_decisions,
    )
    from lion_rebar.export.frame_detection import CATEGORY_SHEET_NAMES

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        record_frame_decision(
            root, marker_handle="M9", category_code="VERTICALS",
            decided_by="инженер", note="армировка вертикали",
            identity=IDENT, frame_is_unknown=True)
        assert "VERTICALS" in SELECTABLE_CATEGORIES
        assert "VERTICALS" not in INTERNAL_ONLY_CATEGORIES
        assert CATEGORY_SHEET_NAMES["VERTICALS"] == "вертикали"
        # Решението вече СЕ прилага при export.
        assert load_frame_decisions(root, IDENT) == {"M9": "VERTICALS"}
        assert internal_only_decisions(root, IDENT) == {}

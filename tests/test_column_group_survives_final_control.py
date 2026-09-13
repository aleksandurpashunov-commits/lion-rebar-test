"""The column-group title must survive Final Control into the Excel (rc44).

Owner-reported 2026-09-13, from a real run: "нов проект - чертеж колони -
82/82 автоматично приети позиции - ексел експорт - 0 отделени колони всичко
е накуп".

The label was already resolved from the drawing and carried on the batch
report rows, but the Excel export does not read those rows. ``main_window.
run_excel_export`` calls ``excel_export.load_export_context``, which builds
every ExportPosition from ``working/final_integrity_ledger.csv`` -- the Final
Control ledger. That file had a column for the beam name and one for the
vertical section title, but none for the column group, so
``row.get("column_group_label")`` was always empty and
``_column_group_sections`` never fired: one flat list instead of one titled
block per column group.

This test pins the whole hand-off, because verifying the pieces in isolation
is exactly what missed it: the label goes into the frozen assignment during
Final Control, and comes back out of the written ledger.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.integrity import _freeze_beam_assignments, _write_ledger
from lion_rebar.review_center_model import ACTIONABLE_CLASS


_LABEL = "%%UK23, K24 (35/35) - 2бр."


def _entities() -> list[DxfEntityRecord]:
    # No ``ЧЕРТЕЖ:`` marker: this designer delivers one logical sheet, so the
    # category comes from the single explicit title, exactly as in the real
    # koloni file.
    return [
        DxfEntityRecord(
            "TITLE",
            "TEXT",
            "0",
            {"insert": [100.0, 0.0, 0.0]},
            "АРМИРОВКА КОЛОНИ ОТ КОТА +18,18 ДО КОТА +21,48",
        ),
        DxfEntityRecord(
            "PXANN_98E88D",
            "BLOCK_ATTR_TEXT",
            "arm_koloni",
            {"insert": [100.0, 500.0, 0.0]},
            "108N8x114",
        ),
    ]


def _extracted_payload() -> dict[str, Any]:
    return {
        "entities": [
            {
                "handle": entity.handle,
                "entity_type": entity.entity_type,
                "layer": entity.layer,
                "geometry": entity.geometry,
                "text": entity.text,
                "raw": entity.raw,
            }
            for entity in _entities()
        ]
    }


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "annotation_class": ACTIONABLE_CLASS,
            "annotation_handle": "PXANN_98E88D",
            "annotation_text": "108N8x114",
            "column_group_label": _LABEL,
        }
    ]


def _links() -> dict[str, dict[str, Any]]:
    return {
        "PXANN_98E88D": {
            "annotation_handle": "PXANN_98E88D",
            "annotation_text": "108N8x114",
            "resolved_annotation_text": "108N8x114",
            "production_count": 108,
            "production_diameter_mm": 8,
            "production_length_cm": 114,
            "master_shape_code": "F006",
            "master_shape_name": "СТРЕМЕ",
            "geometry_handles": [],
        }
    }


def _identity() -> DataIdentity:
    return DataIdentity(
        project_id="P", source_sha256="S", source_generation_id="G", analysis_run_id="R"
    )


def test_the_ledger_the_excel_reads_carries_the_label(tmp_path: Path) -> None:
    """``load_export_context`` builds every ExportPosition from this file and
    reads nothing else, so the label has to survive into it. Before the fix
    the column did not exist at all and the Excel saw an empty string."""

    ledger_path = tmp_path / "final_integrity_ledger.csv"
    frozen = {
        "PXANN_98E88D": {
            "frame_index": 1,
            "frame_category_code": "COLUMNS",
            "frame_title_text": "АРМИРОВКА КОЛОНИ ОТ КОТА +18,18 ДО КОТА +21,48",
            "beam_assignment_frozen": True,
            "column_group_label": _LABEL,
        }
    }

    _write_ledger(
        path=ledger_path,
        rows=_rows(),
        links_by_annotation=_links(),
        decisions={},
        identity=_identity(),
        beam_assignments=frozen,
    )

    with ledger_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert "column_group_label" in (reader.fieldnames or [])
        assert next(reader)["column_group_label"] == _LABEL


def test_a_position_outside_any_column_group_stays_empty(tmp_path: Path) -> None:
    """Beam and vertical sheets share this ledger; their rows must not pick up
    a stray group title."""

    ledger_path = tmp_path / "final_integrity_ledger.csv"

    _write_ledger(
        path=ledger_path,
        rows=_rows(),
        links_by_annotation=_links(),
        decisions={},
        identity=_identity(),
        beam_assignments={"PXANN_98E88D": {"frame_index": 2, "beam_name": "Гр. A.1"}},
    )

    with ledger_path.open(encoding="utf-8-sig", newline="") as stream:
        assert next(csv.DictReader(stream))["column_group_label"] == ""

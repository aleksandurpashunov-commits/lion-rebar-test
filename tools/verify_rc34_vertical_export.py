"""Fail-closed acceptance harness for the real rc34 vertical Excel export.

Without --execute this command is read-only: it validates the selected
verification-project inputs and prints the exact checks that the write phase
will run.  --execute is intentionally required because Final Control rewrites
the verification project's Ledger/freeze before Excel generation.

The workbook itself still requires the separate artifact-tool visual/formula
inspection mandated by the release acceptance procedure.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


RELEASE_ROOT = Path(__file__).resolve().parents[1]
if str(RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(RELEASE_ROOT))

DEFAULT_PROJECT_ROOT = (
    RELEASE_ROOT.parents[1] / "verification_projects" / "ръчно-004_rc34"
)
REPORT_RELATIVE = Path("working") / "final_control_sources" / "batch_report.json"

EXPECTED_POSITION_COUNT = 159
EXPECTED_SECTION_COUNT = 17
EXPECTED_SOURCE_GROUP_COUNT = 114
EXPECTED_COLLAPSED_ANNOTATION_COUNT = 45
EXPECTED_TOTAL_GROUP_COUNT = 38
EXPECTED_F007_SOURCE_GROUP_COUNT = 14
EXPECTED_F002_SOURCE_GROUP_COUNT = 3
EXPECTED_VERTICAL_LABEL_HANDLES = {
    "5EA4",  # Стб.Ядро A.1
    "5569",  # Стб.Ядро A.2
    "637F",  # Стб.Ядро A.3
    "64B4",  # Тип 1
    "6578",  # Тип 1a
    "64DD",  # Тип 1b
    "65E8",  # Тип 1c
    "667E",  # Тип 2
    "6737",  # Тип 2a
    "67FE",  # Тип 2b
    "68B7",  # Тип 2c
    "695A",  # Ш.A-20-1
    "69FC",  # K.A-76
    "6A4F",  # K.Ш.A16
    "6ADB",  # K.A.40
    "7202",  # K.A.11,11a
    "6B31",  # K.A.21
}
EXPECTED_SECTION_KINDS = Counter(
    {"CORE": 3, "TYPE": 8, "NAMED_VERTICAL": 6}
)
EXPECTED_CORE_COUNTS = {
    "5EA4": 30,
    "5569": 31,
    "637F": 33,
}
EXPECTED_F007_HANDLES = {
    "6408::BJ-FI-L",
    "6753::BJ-FI-L",
    "5A04::BJ-FI-L",
    "5A0A::BJ-FI-L",
    "5E52::BJ-FI-L",
    "5E58::BJ-FI-L",
    "6402::BJ-FI-L",
    "656C::BJ-FI-L",
    "65D5::BJ-FI-L",
    "6695::BJ-FI-L",
    "6764::BJ-FI-L",
    "6814::BJ-FI-L",
    "681B::BJ-FI-L",
    "6948::BJ-FI-L",
    "6A48::BJ-FI-L",
    "6AE4::BJ-FI-L",
    "720B::BJ-FI-L",
    "669C::BJ-FI-L",
    "68A9::BJ-FI-L",
    "69E5::BJ-FI-L",
}
EXPECTED_F002_HANDLES = {
    "560C::BJ-FI-L",
    "5E77::BJ-FI-L",
    "638D::BJ-FI-L",
}
EXPECTED_F007_VECTOR = (10.0, 20.0, 10.0)
EXPECTED_F002_VECTOR = (259.627289, 40.574199, 110.000038)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _require(isinstance(payload, dict), f"Expected a JSON object: {path}")
    return payload


def _close(actual: float, expected: float, *, tolerance: float = 1e-5) -> bool:
    return math.isclose(
        float(actual),
        float(expected),
        rel_tol=0.0,
        abs_tol=tolerance,
    )


def _vector_close(
    actual: Iterable[float],
    expected: Sequence[float],
    *,
    tolerance: float = 1e-5,
) -> bool:
    values = tuple(float(value) for value in actual)
    return len(values) == len(expected) and all(
        _close(value, target, tolerance=tolerance)
        for value, target in zip(values, expected)
    )


def _verification_root(path: Path) -> Path:
    root = path.resolve()
    _require(root.is_dir(), f"Verification project does not exist: {root}")
    _require(
        any(part.casefold() == "verification_projects" for part in root.parts),
        "--execute is restricted to a project below verification_projects.",
    )
    return root


def _input_paths(root: Path) -> dict[str, Path]:
    paths = {
        "project": root / "working" / "project.json",
        "report": root / REPORT_RELATIVE,
        "approved_links": root / "working" / "approved_geometry_links.json",
        "extracted_entities": root / "working" / "extracted_entities.json",
        "source_dxf": root / "source" / "original.dxf",
    }
    for label, path in paths.items():
        _require(path.is_file(), f"Missing {label}: {path}")
    return paths


def _assert_raw_inputs(root: Path) -> dict[str, Any]:
    paths = _input_paths(root)
    project_payload = _read_json(paths["project"])
    report_payload = _read_json(paths["report"])
    approved_payload = _read_json(paths["approved_links"])
    report_rows = [
        row for row in report_payload.get("rows", []) if isinstance(row, dict)
    ]
    links = [
        link for link in approved_payload.get("links", []) if isinstance(link, dict)
    ]
    _require(
        len(report_rows) == EXPECTED_POSITION_COUNT,
        f"Batch report rows: {len(report_rows)}/{EXPECTED_POSITION_COUNT}",
    )
    _require(
        len(links) == EXPECTED_POSITION_COUNT,
        f"Approved links: {len(links)}/{EXPECTED_POSITION_COUNT}",
    )

    by_shape = Counter(
        str(link.get("master_shape_code") or "").strip().upper()
        for link in links
    )
    f007 = [
        link
        for link in links
        if str(link.get("master_shape_code") or "").strip().upper() == "F007"
    ]
    f007_handles = {
        str(link.get("annotation_handle") or "") for link in f007
    }
    _require(
        f007_handles == EXPECTED_F007_HANDLES,
        "Raw project F007 annotation set is not the audited 20-handle set.",
    )
    for link in f007:
        _require(
            _vector_close(
                link.get("canonical_segment_lengths_cm") or (),
                EXPECTED_F007_VECTOR,
            ),
            "F007 canonical MASTER vector is missing for "
            + str(link.get("annotation_handle") or ""),
        )
        _require(
            str(link.get("contextual_rule_code") or "")
            == "F007_ESOVE_CURVED_CENTERLINE",
            "F007 contextual MASTER provenance is missing for "
            + str(link.get("annotation_handle") or ""),
        )

    f002_handles = {
        str(link.get("annotation_handle") or "")
        for link in links
        if str(link.get("master_shape_code") or "").strip().upper() == "F002"
    }
    _require(
        f002_handles == EXPECTED_F002_HANDLES,
        "Raw project F002 annotation set is not the audited three-handle set.",
    )

    extensions = (
        project_payload.get("extensions")
        if isinstance(project_payload.get("extensions"), dict)
        else {}
    )
    snapshot_relative = str(extensions.get("engine_snapshot") or "").strip()
    _require(bool(snapshot_relative), "Project has no pinned Engine snapshot.")
    snapshot_path = root / snapshot_relative
    _require(snapshot_path.is_file(), f"Pinned Engine snapshot is missing: {snapshot_path}")

    return {
        "project": str(project_payload.get("name") or root.name),
        "report_rows": len(report_rows),
        "approved_links": len(links),
        "shape_counts": dict(sorted(by_shape.items())),
        "f007_rows": len(f007),
        "f002_rows": len(EXPECTED_F002_HANDLES),
        "engine_snapshot": str(snapshot_path),
    }


def _load_project_engine(root: Path):
    from lion_rebar.engine.models import EnginePackage
    from lion_rebar.export.excel_export import load_pinned_engine
    from lion_rebar.project.manager import ProjectManager

    project = ProjectManager().open_project(root)
    relative = str(project.extensions.get("engine_snapshot") or "").strip()
    _require(bool(relative), "Project has no pinned Engine snapshot.")
    snapshot_path = root / relative
    fallback = EnginePackage.from_dict(_read_json(snapshot_path))
    engine = load_pinned_engine(project, fallback)
    return project, engine


def _frozen_frame_configs(root: Path):
    from lion_rebar.export.excel_export import ExportFrameConfig
    from lion_rebar.export.frozen_export_config import read_frozen_config

    mappings = read_frozen_config(root)
    _require(len(mappings) == 1, f"Frozen frame count: {len(mappings)}/1")
    mapping = mappings[0]
    _require(
        mapping.category_code == "VERTICALS",
        f"Frozen frame category is {mapping.category_code!r}, expected VERTICALS.",
    )
    _require(
        mapping.sheet_name == "вертикали",
        f"Frozen source sheet is {mapping.sheet_name!r}, expected 'вертикали'.",
    )
    return [
        ExportFrameConfig(
            frame_index=mapping.frame_index,
            category_code=mapping.category_code,
            sheet_name=mapping.sheet_name,
            title_text=mapping.title_text,
            confirmed=True,
        )
    ]


def _assert_positions(frames, positions) -> dict[str, Any]:
    _require(len(frames) == 1, f"Resolved frame count: {len(frames)}/1")
    _require(
        str(frames[0].category_code or "") == "VERTICALS",
        "The resolved drawing frame is not VERTICALS.",
    )
    _require(
        len(positions) == EXPECTED_POSITION_COUNT,
        f"Loaded production positions: {len(positions)}/{EXPECTED_POSITION_COUNT}",
    )
    handles = [str(position.annotation_handle) for position in positions]
    _require(
        len(handles) == len(set(handles)),
        "A production annotation appears more than once.",
    )

    section_keys = {
        (
            int(position.frame_index),
            str(position.vertical_section_label_handle),
            str(position.vertical_section_divider_handle),
            str(position.vertical_section_title),
            str(position.vertical_section_kind),
            int(position.vertical_section_order),
        )
        for position in positions
    }
    _require(
        len(section_keys) == EXPECTED_SECTION_COUNT,
        f"Vertical sections: {len(section_keys)}/{EXPECTED_SECTION_COUNT}",
    )
    label_handles = {key[1] for key in section_keys}
    _require(
        label_handles == EXPECTED_VERTICAL_LABEL_HANDLES,
        "Vertical title-handle set does not match the audited 17 sections.",
    )
    _require(
        {key[5] for key in section_keys}
        == set(range(1, EXPECTED_SECTION_COUNT + 1)),
        "Vertical section order is not exactly 1..17.",
    )
    kind_counts = Counter(key[4] for key in section_keys)
    _require(
        kind_counts == EXPECTED_SECTION_KINDS,
        f"Vertical section kinds: {dict(kind_counts)} != {dict(EXPECTED_SECTION_KINDS)}",
    )
    _require(
        all(
            str(position.vertical_assignment_method)
            == "AUTO_VERTICAL_SECTION_V1"
            for position in positions
        ),
        "At least one vertical row has no frozen AUTO_VERTICAL_SECTION_V1 ownership.",
    )
    rows_by_label = Counter(
        str(position.vertical_section_label_handle) for position in positions
    )
    for handle, expected in EXPECTED_CORE_COUNTS.items():
        _require(
            rows_by_label[handle] == expected,
            f"Core section {handle}: {rows_by_label[handle]}/{expected} rows.",
        )

    source_group_keys = {
        (
            int(position.frame_index),
            str(position.vertical_section_label_handle),
            str(position.vertical_section_divider_handle),
            position.group_key,
        )
        for position in positions
    }
    _require(
        len(source_group_keys) == EXPECTED_SOURCE_GROUP_COUNT,
        (
            "Vertical section source groups: "
            f"{len(source_group_keys)}/{EXPECTED_SOURCE_GROUP_COUNT}"
        ),
    )
    _require(
        len(positions) - len(source_group_keys)
        == EXPECTED_COLLAPSED_ANNOTATION_COUNT,
        "Unexpected count of annotations collapsed inside vertical sections.",
    )

    f007 = [
        position
        for position in positions
        if str(position.shape_code).strip().upper() == "F007"
    ]
    _require(
        {str(position.annotation_handle) for position in f007}
        == EXPECTED_F007_HANDLES,
        "Loaded F007 set does not match the audited 20 annotations.",
    )
    _require(
        all(
            _vector_close(position.segment_lengths_cm, EXPECTED_F007_VECTOR)
            and _close(position.production_length_cm, 40.0)
            for position in f007
        ),
        "Not every loaded F007 position uses the canonical 10+20+10 MASTER vector.",
    )
    _require(
        len({position.group_key for position in f007}) == 1,
        "F007 positions split into more than one production identity.",
    )
    f007_source_groups = {
        (
            int(position.frame_index),
            str(position.vertical_section_label_handle),
            str(position.vertical_section_divider_handle),
            position.group_key,
        )
        for position in f007
    }
    _require(
        len(f007_source_groups) == EXPECTED_F007_SOURCE_GROUP_COUNT,
        (
            "F007 source section groups: "
            f"{len(f007_source_groups)}/{EXPECTED_F007_SOURCE_GROUP_COUNT}"
        ),
    )

    f002 = [
        position
        for position in positions
        if str(position.annotation_handle) in EXPECTED_F002_HANDLES
    ]
    _require(len(f002) == 3, f"Loaded F002 audited rows: {len(f002)}/3")
    _require(
        all(str(position.shape_code).strip().upper() == "F002" for position in f002),
        "At least one audited reduction bar is not F002.",
    )
    _require(
        all(
            _vector_close(position.segment_lengths_cm, EXPECTED_F002_VECTOR)
            and _close(position.production_length_cm, 410.201526, tolerance=1e-4)
            for position in f002
        ),
        "F002 reduction bars are not in the canonical long-left/rise-right/short-right order.",
    )
    _require(
        len({position.group_key for position in f002}) == 1,
        "The three audited F002 reduction bars do not share one production identity.",
    )
    f002_source_groups = {
        (
            int(position.frame_index),
            str(position.vertical_section_label_handle),
            str(position.vertical_section_divider_handle),
            position.group_key,
        )
        for position in f002
    }
    _require(
        len(f002_source_groups) == EXPECTED_F002_SOURCE_GROUP_COUNT,
        (
            "F002 source section groups: "
            f"{len(f002_source_groups)}/{EXPECTED_F002_SOURCE_GROUP_COUNT}"
        ),
    )

    total_identities = len({position.group_key for position in positions})
    _require(
        total_identities == EXPECTED_TOTAL_GROUP_COUNT,
        f"Global production identities: {total_identities}/{EXPECTED_TOTAL_GROUP_COUNT}",
    )
    return {
        "vertical_positions": len(positions),
        "vertical_sections": len(section_keys),
        "section_kinds": dict(sorted(kind_counts.items())),
        "section_rows": dict(sorted(rows_by_label.items())),
        "source_group_rows": len(source_group_keys),
        "collapsed_annotations": len(positions) - len(source_group_keys),
        "f007_rows": len(f007),
        "f007_identities": len({position.group_key for position in f007}),
        "f007_source_groups": len(f007_source_groups),
        "f002_rows": len(f002),
        "f002_identities": len({position.group_key for position in f002}),
        "f002_source_groups": len(f002_source_groups),
        "total_identities": total_identities,
    }


def _assert_trace(trace_path: Path, position_handles: set[str]) -> dict[str, Any]:
    trace = _read_json(trace_path)
    _require(
        trace.get("schema") == "lion-rebar-excel-traceability/v3",
        "Unexpected Excel traceability schema.",
    )
    _require(
        int(trace.get("safe_positions") or 0) == EXPECTED_POSITION_COUNT,
        "Trace safe_positions is not 159.",
    )
    _require(
        int(trace.get("global_position_count") or 0)
        == EXPECTED_TOTAL_GROUP_COUNT,
        "Trace global_position_count is not 38.",
    )
    vertical = trace.get("vertical_export")
    _require(isinstance(vertical, dict), "Trace has no vertical_export block.")
    _require(int(vertical.get("section_count") or 0) == EXPECTED_SECTION_COUNT,
             "Trace section_count is not 17.")
    _require(
        int(vertical.get("approved_annotation_count") or 0)
        == EXPECTED_POSITION_COUNT,
        "Trace approved_annotation_count is not 159.",
    )
    _require(
        int(vertical.get("source_group_row_count") or 0)
        == EXPECTED_SOURCE_GROUP_COUNT,
        "Trace source_group_row_count is not 114.",
    )
    _require(
        int(vertical.get("collapsed_annotation_count") or 0)
        == EXPECTED_COLLAPSED_ANNOTATION_COUNT,
        "Trace collapsed_annotation_count is not 45.",
    )
    _require(int(vertical.get("vertical_position_rows") or 0)
             == EXPECTED_SOURCE_GROUP_COUNT,
             "Trace vertical_position_rows is not 114.")
    _require(vertical.get("one_row_per_annotation") is False,
             "Trace still claims one row per annotation.")
    _require(vertical.get("cross_section_merge_on_source") is False,
             "Trace does not prohibit source-row merges across sections.")
    _require(
        vertical.get("source_row_granularity")
        == "PRODUCTION_IDENTITY_WITHIN_FROZEN_VERTICAL_SECTION",
        "Trace has an unexpected vertical source-row granularity.",
    )
    _require(vertical.get("aggregation_sheet") == "Общо",
             "Trace aggregation sheet is not 'Общо'.")

    rows = [row for row in trace.get("rows", []) if isinstance(row, dict)]
    headers = [row for row in rows if row.get("kind") == "VERTICAL_SECTION"]
    drawings = [row for row in rows if row.get("kind") == "DRAWING"]
    totals = [row for row in rows if row.get("kind") == "TOTAL"]
    _require(len(headers) == EXPECTED_SECTION_COUNT,
             f"Trace vertical headers: {len(headers)}/{EXPECTED_SECTION_COUNT}")
    _require(len(drawings) == EXPECTED_SOURCE_GROUP_COUNT,
             f"Trace source drawing rows: {len(drawings)}/{EXPECTED_SOURCE_GROUP_COUNT}")
    _require(len(totals) == EXPECTED_TOTAL_GROUP_COUNT,
             f"Trace total rows: {len(totals)}/{EXPECTED_TOTAL_GROUP_COUNT}")
    _require(
        all(
            row.get("row_granularity")
            == "PRODUCTION_IDENTITY_WITHIN_VERTICAL_SECTION"
            for row in drawings
        ),
        "At least one source row has the wrong granularity.",
    )
    _require(
        all(
            int(row.get("source_annotation_count") or 0)
            == len(row.get("annotation_handles") or [])
            and int(row.get("count_total") or 0)
            == sum(int(value) for value in (row.get("count_formula_terms") or []))
            for row in drawings
        ),
        "A source trace row has inconsistent annotation/count provenance.",
    )
    traced_handles = [
        str(handle)
        for row in drawings
        for handle in (row.get("annotation_handles") or [])
    ]
    _require(
        Counter(traced_handles) == Counter(position_handles),
        "Trace source annotations do not cover the loaded 159 positions exactly once.",
    )
    drawing_by_group_id = {
        str(row.get("source_group_id") or ""): row
        for row in drawings
    }
    _require(
        len(drawing_by_group_id) == EXPECTED_SOURCE_GROUP_COUNT
        and "" not in drawing_by_group_id,
        "Source group ids are missing or duplicated.",
    )

    annotation_map = [
        row
        for row in trace.get("annotation_position_map", [])
        if isinstance(row, dict)
    ]
    _require(
        len(annotation_map) == EXPECTED_POSITION_COUNT,
        "Annotation-to-source map does not contain 159 entries.",
    )
    _require(
        Counter(
            str(row.get("annotation_handle") or "")
            for row in annotation_map
        )
        == Counter(position_handles),
        "Annotation-to-source map does not cover every handle exactly once.",
    )
    for mapped in annotation_map:
        source_group_id = str(mapped.get("source_group_id") or "")
        drawing = drawing_by_group_id.get(source_group_id)
        _require(
            drawing is not None,
            f"Annotation map points to unknown source group {source_group_id!r}.",
        )
        _require(
            int(mapped.get("source_row") or 0) == int(drawing.get("row") or 0)
            and str(mapped.get("source_sheet") or "")
            == str(drawing.get("sheet") or "")
            and str(mapped.get("annotation_handle") or "")
            in {
                str(handle)
                for handle in (drawing.get("annotation_handles") or [])
            },
            "Annotation-to-source map disagrees with its DRAWING trace row.",
        )
    source_rows = {
        int(row["row"])
        for row in headers + drawings
    }
    _require(
        source_rows == set(range(7, 138)),
        "Source trace rows are not the continuous 7..137 section/group ledger.",
    )
    _require(
        {int(row["row"]) for row in totals} == set(range(7, 45)),
        "Total trace rows are not the expected continuous 7..44 aggregation.",
    )
    _require(
        sum(len(row.get("source_cells") or []) for row in totals)
        == EXPECTED_SOURCE_GROUP_COUNT,
        "Total formulas do not reference all 114 section source groups exactly once.",
    )
    total_handles = [
        str(handle)
        for row in totals
        for handle in (row.get("annotation_handles") or [])
    ]
    _require(
        Counter(total_handles) == Counter(position_handles),
        "TOTAL trace does not cover every annotation exactly once.",
    )
    _require(
        all(
            int(row.get("annotation_count") or 0)
            == len(row.get("annotation_handles") or [])
            and int(row.get("source_group_count") or 0)
            == len(row.get("source_cells") or [])
            and int(row.get("count_total") or 0)
            == sum(int(value) for value in (row.get("count_formula_terms") or []))
            for row in totals
        ),
        "A TOTAL trace row has inconsistent source/count provenance.",
    )
    f007_totals = [
        row
        for row in totals
        if str(row.get("shape_code") or "").upper() == "F007"
        and _close(row.get("diameter_mm") or 0, 6.0)
        and _close(row.get("production_length_cm") or 0, 40.0)
    ]
    _require(len(f007_totals) == 1, "Trace has more than one F007 Ø6/L40 total identity.")
    _require(
        len(f007_totals[0].get("source_cells") or [])
        == EXPECTED_F007_SOURCE_GROUP_COUNT,
        "The single F007 total does not aggregate the audited 14 section groups.",
    )
    _require(
        {
            str(handle)
            for handle in (f007_totals[0].get("annotation_handles") or [])
        }
        == EXPECTED_F007_HANDLES,
        "The single F007 total does not retain all 20 annotation handles.",
    )
    f002_totals = [
        row
        for row in totals
        if str(row.get("shape_code") or "").upper() == "F002"
        and _close(row.get("diameter_mm") or 0, 18.0)
        and _close(row.get("production_length_cm") or 0, 410.201526, tolerance=1e-4)
    ]
    _require(len(f002_totals) == 1, "Trace has more than one audited F002 total identity.")
    _require(
        len(f002_totals[0].get("source_cells") or [])
        == EXPECTED_F002_SOURCE_GROUP_COUNT,
        "The F002 total does not aggregate its three audited section groups.",
    )
    _require(
        {
            str(handle)
            for handle in (f002_totals[0].get("annotation_handles") or [])
        }
        == EXPECTED_F002_HANDLES,
        "The F002 total does not retain the three audited annotation handles.",
    )
    return {
        "source_headers": len(headers),
        "source_drawing_rows": len(drawings),
        "collapsed_annotations": EXPECTED_POSITION_COUNT - len(drawings),
        "annotation_source_map": len(annotation_map),
        "total_rows": len(totals),
        "total_source_references": sum(
            len(row.get("source_cells") or []) for row in totals
        ),
    }


def _execute(root: Path, output: Path) -> dict[str, Any]:
    from lion_rebar.export.excel_export import (
        generate_excel_export,
        load_export_context,
    )
    from lion_rebar.project.final_control_lifecycle import assert_export_allowed
    from lion_rebar.project.integrity import reconcile_project_integrity

    output = output.resolve()
    _require(output.suffix.lower() == ".xlsx", "--output must end in .xlsx")
    _require(not output.exists(), f"Refusing to overwrite an existing workbook: {output}")
    report_path = root / REPORT_RELATIVE
    integrity = reconcile_project_integrity(
        root,
        report_path=report_path,
        apply=True,
    )
    _require(integrity.applied, "Final Control did not run in apply mode.")
    _require(integrity.integrity_passed, "Final Control integrity_passed is false.")
    _require(
        integrity.full_specification_complete,
        "Final Control full_specification_complete is false.",
    )
    _require(integrity.approved_after == EXPECTED_POSITION_COUNT,
             "Final Control did not preserve all 159 approved positions.")
    _require(integrity.vertical_assignments_frozen == EXPECTED_POSITION_COUNT,
             "Final Control did not freeze 159 vertical assignments.")
    _require(integrity.vertical_sections_frozen == EXPECTED_SECTION_COUNT,
             "Final Control did not freeze 17 vertical sections.")
    _require(integrity.freeze_required, "Final Control did not create a required freeze.")
    _require(not integrity.export_preflight_error,
             "Final Control export preflight reported an error.")
    blockers = assert_export_allowed(root)
    _require(not blockers, "Export lifecycle blockers: " + "; ".join(blockers))

    project, engine = _load_project_engine(root)
    frames, positions = load_export_context(project, (), engine)
    position_summary = _assert_positions(frames, positions)
    configs = _frozen_frame_configs(root)
    result = generate_excel_export(
        project,
        engine,
        frames,
        positions,
        configs,
        output,
        require_frozen_config=True,
    )
    _require(result.workbook_path.is_file(), "Excel generator returned no workbook.")
    _require(result.safe_position_count == EXPECTED_POSITION_COUNT,
             "Excel result safe_position_count is not 159.")
    _require(result.grouped_row_count == EXPECTED_SOURCE_GROUP_COUNT,
             "Source sheet does not have the audited 114 section groups.")
    _require(result.total_group_count == EXPECTED_TOTAL_GROUP_COUNT,
             "Excel result total_group_count is not 38.")
    trace_summary = _assert_trace(
        result.traceability_path,
        {str(position.annotation_handle) for position in positions},
    )
    return {
        "mode": "execute",
        "project_root": str(root),
        "output": str(result.workbook_path),
        "trace": str(result.traceability_path),
        "integrity": {
            "approved_after": integrity.approved_after,
            "vertical_assignments_frozen": integrity.vertical_assignments_frozen,
            "vertical_sections_frozen": integrity.vertical_sections_frozen,
            "integrity_passed": integrity.integrity_passed,
            "full_specification_complete": integrity.full_specification_complete,
        },
        "positions": position_summary,
        "trace_checks": trace_summary,
        "artifact_tool_verification_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="verification-project root used for rc34 acceptance",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new .xlsx path; required with --execute",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="apply Final Control to the verification copy and generate the workbook",
    )
    args = parser.parse_args()
    root = _verification_root(args.project_root)
    raw_summary = _assert_raw_inputs(root)

    if not args.execute:
        print(
            json.dumps(
                {
                    "mode": "read-only",
                    "project_root": str(root),
                    "inputs": raw_summary,
                    "next_step": (
                        "Run again with --execute --output <new.xlsx> only after "
                        "the spreadsheet operation marker succeeds."
                    ),
                    "execute_assertions": {
                        "positions": EXPECTED_POSITION_COUNT,
                        "vertical_sections": EXPECTED_SECTION_COUNT,
                        "source_group_rows": EXPECTED_SOURCE_GROUP_COUNT,
                        "collapsed_annotations": EXPECTED_COLLAPSED_ANNOTATION_COUNT,
                        "section_kinds": dict(EXPECTED_SECTION_KINDS),
                        "core_rows": EXPECTED_CORE_COUNTS,
                        "f007_rows": len(EXPECTED_F007_HANDLES),
                        "f007_source_groups": EXPECTED_F007_SOURCE_GROUP_COUNT,
                        "f007_vector_cm": EXPECTED_F007_VECTOR,
                        "f007_identities": 1,
                        "f002_rows": len(EXPECTED_F002_HANDLES),
                        "f002_source_groups": EXPECTED_F002_SOURCE_GROUP_COUNT,
                        "f002_vector_cm": EXPECTED_F002_VECTOR,
                        "f002_identities": 1,
                        "total_identities": EXPECTED_TOTAL_GROUP_COUNT,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    _require(args.output is not None, "--output is required with --execute")
    print(json.dumps(_execute(root, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

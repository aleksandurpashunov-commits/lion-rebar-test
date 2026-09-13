"""Fail-closed acceptance harness for the isolated rc35 R-008 full-floor export.

The default mode is strictly read-only: it validates the isolated verification
project, the pinned batch report, the 582 approved production links and the
four evidenced NON_PRODUCTION decisions. The --execute flag is intentionally
required because Final Control rewrites the verification copy's ledger/freeze
before generating the workbook.

The workbook still needs the separate artifact-tool formula and visual review
required by the release procedure. This harness never runs that review and it
never emits the spreadsheet-operation marker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


RELEASE_ROOT = Path(__file__).resolve().parents[1]
if str(RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(RELEASE_ROOT))

DEFAULT_PROJECT_ROOT = (
    RELEASE_ROOT.parents[1] / "verification_projects" / "R-008_rc35"
)

EXPECTED_ACTIONABLE = 586
EXPECTED_APPROVED = 582
EXPECTED_NON_PRODUCTION = 4
EXPECTED_FRAME_ASSIGNMENTS_FROZEN = 582
EXPECTED_VERTICAL_ASSIGNMENTS_FROZEN = 126
EXPECTED_VERTICAL_SECTIONS_FROZEN = 12
IDENTITY_FIELDS = (
    "project_id",
    "source_sha256",
    "source_generation_id",
    "analysis_run_id",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read JSON {path}: {exc}") from exc
    _require(isinstance(payload, dict), f"Expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


def _verification_root(path: Path) -> Path:
    root = path.resolve()
    _require(root.is_dir(), f"Verification project does not exist: {root}")
    _require(
        any(part.casefold() == "verification_projects" for part in root.parts),
        "--execute is restricted to a project below verification_projects.",
    )
    return root


def _assert_exact_handle_coverage(
    actual: Iterable[str],
    expected: Iterable[str],
    *,
    excluded: Iterable[str] = (),
    label: str,
) -> None:
    actual_counter = Counter(str(value) for value in actual if str(value))
    expected_counter = Counter(str(value) for value in expected if str(value))
    _require(
        actual_counter == expected_counter,
        (
            f"{label} coverage mismatch: "
            f"missing={sorted((expected_counter - actual_counter).elements())}; "
            f"unexpected={sorted((actual_counter - expected_counter).elements())}; "
            "duplicates="
            f"{sorted(handle for handle, count in actual_counter.items() if count > 1)}"
        ),
    )
    forbidden = sorted(set(actual_counter) & {str(value) for value in excluded})
    _require(not forbidden, f"{label} contains NON_PRODUCTION handles: {forbidden}")


def _derive_frame_expectations(
    actionable_by_handle: Mapping[str, Mapping[str, Any]],
    production_handles: Iterable[str],
) -> tuple[Counter[int], dict[int, str], Counter[str]]:
    counts: Counter[int] = Counter()
    categories: dict[int, str] = {}
    category_counts: Counter[str] = Counter()
    for handle in production_handles:
        row = actionable_by_handle.get(str(handle))
        _require(row is not None, f"Production handle is absent from batch rows: {handle}")
        try:
            frame_index = int(row.get("frame_index") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid frame_index for {handle}") from exc
        category = _text(row.get("frame_category_code")).upper()
        _require(frame_index > 0, f"Missing frame_index for {handle}")
        _require(category and category != "UNKNOWN", f"Missing frame category for {handle}")
        before = categories.setdefault(frame_index, category)
        _require(
            before == category,
            f"Batch rows disagree about frame {frame_index}: {before}/{category}",
        )
        counts[frame_index] += 1
        category_counts[category] += 1
    return counts, categories, category_counts


def _assert_identity_matches(
    expected: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    label: str,
    allow_empty_analysis_run: bool = False,
) -> None:
    for field in IDENTITY_FIELDS:
        expected_value = _text(expected.get(field))
        actual_value = _text(candidate.get(field))
        _require(expected_value, f"Project data identity has no {field}.")
        if field == "analysis_run_id" and allow_empty_analysis_run and not actual_value:
            continue
        _require(
            actual_value == expected_value,
            f"{label} data identity mismatch for {field}: {actual_value!r}/{expected_value!r}",
        )


@dataclass(frozen=True)
class RawInputs:
    root: Path
    project_name: str
    report_path: Path
    actionable_by_handle: dict[str, dict[str, Any]]
    approved_handles: frozenset[str]
    non_production_handles: frozenset[str]
    production_frame_counts: Counter[int]
    frame_categories: dict[int, str]
    production_category_counts: Counter[str]
    total_report_rows: int
    engine_snapshot: Path

    def summary(self) -> dict[str, Any]:
        return {
            "project": self.project_name,
            "batch_report": str(self.report_path),
            "batch_rows": self.total_report_rows,
            "actionable_positions": len(self.actionable_by_handle),
            "approved_production_positions": len(self.approved_handles),
            "evidenced_non_production_positions": len(self.non_production_handles),
            "non_production_handles": sorted(self.non_production_handles),
            "production_rows_by_frame": {
                str(key): value for key, value in sorted(self.production_frame_counts.items())
            },
            "frame_categories": {
                str(key): value for key, value in sorted(self.frame_categories.items())
            },
            "production_rows_by_category": dict(
                sorted(self.production_category_counts.items())
            ),
            "engine_snapshot": str(self.engine_snapshot),
        }


def _assert_raw_inputs(root: Path) -> RawInputs:
    working = root / "working"
    project_path = working / "project.json"
    approved_path = working / "approved_geometry_links.json"
    extracted_path = working / "extracted_entities.json"
    review_state_path = working / "review_center_state.json"
    source_path = root / "source" / "original.dxf"
    for label, path in {
        "project": project_path,
        "approved links": approved_path,
        "extracted entities": extracted_path,
        "Review Center state": review_state_path,
        "source DXF": source_path,
    }.items():
        _require(path.is_file(), f"Missing {label}: {path}")

    project = _read_json(project_path)
    extensions = project.get("extensions")
    _require(isinstance(extensions, dict), "Project extensions are missing.")
    report_value = _text(extensions.get("latest_batch_report_path"))
    _require(report_value, "Project has no latest_batch_report_path.")
    report_path = Path(report_value)
    if not report_path.is_absolute():
        report_path = root / report_path
    report_path = report_path.resolve()
    _require(report_path.is_file(), f"Pinned batch report is missing: {report_path}")

    snapshot_value = _text(extensions.get("engine_snapshot"))
    _require(snapshot_value, "Project has no pinned Engine snapshot.")
    engine_snapshot = (root / snapshot_value).resolve()
    _require(engine_snapshot.is_file(), f"Pinned Engine snapshot is missing: {engine_snapshot}")

    report = _read_json(report_path)
    approved = _read_json(approved_path)
    extracted = _read_json(extracted_path)
    review_state = _read_json(review_state_path)
    _require(report.get("cancelled") is not True, "Pinned batch analysis is cancelled.")
    _require(isinstance(report.get("rows"), list), "Batch report has no rows list.")
    _require(isinstance(approved.get("links"), list), "Approved-links file has no links list.")
    _require(isinstance(extracted.get("entities"), list), "Extracted-entities file has no entities list.")
    _require(isinstance(review_state.get("decisions"), dict), "Review Center state has no decision map.")
    review_report_value = _text(review_state.get("report_path"))
    _require(review_report_value, "Review Center state has no report_path.")
    review_report_path = Path(review_report_value)
    if not review_report_path.is_absolute():
        review_report_path = root / review_report_path
    _require(
        review_report_path.resolve() == report_path,
        "Review Center and project point to different batch reports.",
    )
    from lion_rebar.review_center_model import (
        load_review_report,
        report_fingerprint,
    )

    _require(
        _text(review_state.get("report_fingerprint")).lower()
        == report_fingerprint(load_review_report(report_path)).lower(),
        "Review Center batch-report fingerprint is stale or invalid.",
    )

    project_identity = extensions.get("data_identity")
    _require(isinstance(project_identity, dict), "Project data identity is missing.")
    for label, payload, allow_empty_run in (
        ("batch report", report, False),
        ("approved links", approved, False),
        ("extracted entities", extracted, True),
        ("Review Center state", review_state, False),
    ):
        identity = payload.get("data_identity")
        _require(isinstance(identity, dict), f"{label} data identity is missing.")
        _assert_identity_matches(
            project_identity,
            identity,
            label=label,
            allow_empty_analysis_run=allow_empty_run,
        )
    expected_source_hash = _text(project_identity.get("source_sha256")).lower()
    _require(
        _sha256(source_path).lower() == expected_source_hash,
        "Source DXF SHA-256 differs from the project data identity.",
    )

    all_rows = [row for row in report["rows"] if isinstance(row, dict)]
    actionable_rows = [
        row for row in all_rows if _text(row.get("annotation_class")).upper() == "ACTIONABLE"
    ]
    actionable_by_handle = {
        _text(row.get("annotation_handle")): row for row in actionable_rows
    }
    _require(
        len(actionable_rows) == len(actionable_by_handle),
        "Batch report contains missing or duplicate actionable annotation handles.",
    )
    _require(
        len(actionable_by_handle) == EXPECTED_ACTIONABLE,
        f"Actionable positions: {len(actionable_by_handle)}/{EXPECTED_ACTIONABLE}",
    )

    links = [link for link in approved["links"] if isinstance(link, dict)]
    approved_handles = [_text(link.get("annotation_handle")) for link in links]
    _require(
        all(approved_handles) and len(approved_handles) == len(set(approved_handles)),
        "Approved links contain a missing or duplicate annotation handle.",
    )
    approved_set = frozenset(approved_handles)
    _require(
        len(approved_set) == EXPECTED_APPROVED,
        f"Approved production links: {len(approved_set)}/{EXPECTED_APPROVED}",
    )

    decisions = review_state["decisions"]
    non_production: dict[str, dict[str, Any]] = {
        _text(handle): dict(value)
        for handle, value in decisions.items()
        if isinstance(value, dict)
        and _text(value.get("decision")).upper() == "NON_PRODUCTION_POSITION"
    }
    _require(
        len(non_production) == EXPECTED_NON_PRODUCTION,
        f"Evidenced NON_PRODUCTION decisions: {len(non_production)}/{EXPECTED_NON_PRODUCTION}",
    )
    for handle, decision in non_production.items():
        _require(handle in actionable_by_handle, f"NON_PRODUCTION handle is not actionable: {handle}")
        _require(
            _text(decision.get("action_class")).upper() == "EXCLUSION"
            and _text(decision.get("reason_code")).upper() == "NON_PRODUCTION_POSITION"
            and bool(_text(decision.get("at")))
            and bool(_text(decision.get("annotation_text")))
            and bool(_text(decision.get("diagnostic_reason_code")))
            and bool(_text(decision.get("correct_specification")))
            and bool(_text(decision.get("note")))
            and bool(_text(decision.get("evidence"))),
            f"NON_PRODUCTION decision has incomplete engineering evidence: {handle}",
        )
    non_production_set = frozenset(non_production)
    _require(
        not (approved_set & non_production_set),
        "An annotation is both production-approved and NON_PRODUCTION.",
    )
    _require(
        approved_set | non_production_set == set(actionable_by_handle),
        "Approved + evidenced NON_PRODUCTION does not account for all actionable rows.",
    )

    production_frame_counts, frame_categories, production_category_counts = (
        _derive_frame_expectations(actionable_by_handle, approved_set)
    )
    all_frame_counts, all_frame_categories, _all_categories = _derive_frame_expectations(
        actionable_by_handle,
        actionable_by_handle,
    )
    _require(
        set(production_frame_counts) == set(all_frame_counts),
        "At least one drawing frame contains no production position.",
    )
    _require(
        frame_categories == all_frame_categories,
        "Production and actionable rows disagree about frame categories.",
    )

    return RawInputs(
        root=root,
        project_name=_text(project.get("name")) or root.name,
        report_path=report_path,
        actionable_by_handle=actionable_by_handle,
        approved_handles=approved_set,
        non_production_handles=non_production_set,
        production_frame_counts=production_frame_counts,
        frame_categories=frame_categories,
        production_category_counts=production_category_counts,
        total_report_rows=len(all_rows),
        engine_snapshot=engine_snapshot,
    )


def _load_project_engine(root: Path):
    from lion_rebar.engine.models import EnginePackage
    from lion_rebar.export.excel_export import load_pinned_engine
    from lion_rebar.project.manager import ProjectManager

    project = ProjectManager().open_project(root)
    relative = _text(project.extensions.get("engine_snapshot"))
    _require(relative, "Project has no pinned Engine snapshot.")
    fallback = EnginePackage.from_dict(_read_json(root / relative))
    return project, load_pinned_engine(project, fallback)


def _frozen_frame_configs(root: Path, frames: Sequence[Any], raw: RawInputs):
    from lion_rebar.export.excel_export import ExportFrameConfig
    from lion_rebar.export.frozen_export_config import read_frozen_config

    mappings = read_frozen_config(root)
    _require(mappings, "Final Control did not freeze an export frame config.")
    indices = [int(item.frame_index) for item in mappings]
    sheets = [_text(item.sheet_name) for item in mappings]
    _require(len(indices) == len(set(indices)), "Frozen export config duplicates a frame.")
    _require(all(sheets) and len(sheets) == len(set(sheets)), "Frozen sheet names are empty or duplicated.")
    _require(
        set(indices) == {int(frame.index) for frame in frames} == set(raw.frame_categories),
        "Frozen/detected/batch frame sets do not match.",
    )
    for item in mappings:
        expected_category = raw.frame_categories[int(item.frame_index)]
        _require(
            _text(item.category_code).upper() == expected_category,
            f"Frozen category mismatch for frame {item.frame_index}: "
            f"{item.category_code}/{expected_category}",
        )
    configs = tuple(
        ExportFrameConfig(
            frame_index=int(item.frame_index),
            category_code=_text(item.category_code).upper(),
            sheet_name=_text(item.sheet_name),
            title_text=_text(item.title_text),
            confirmed=True,
        )
        for item in mappings
    )
    return configs, tuple(mappings)


def _assert_positions(
    frames: Sequence[Any],
    positions: Sequence[Any],
    mappings: Sequence[Any],
    raw: RawInputs,
) -> dict[str, Any]:
    _require(len(positions) == EXPECTED_APPROVED, f"Loaded positions: {len(positions)}/{EXPECTED_APPROVED}")
    handles = [str(position.annotation_handle) for position in positions]
    _assert_exact_handle_coverage(
        handles,
        raw.approved_handles,
        excluded=raw.non_production_handles,
        label="Loaded production positions",
    )
    mapping_by_frame = {int(item.frame_index): item for item in mappings}
    position_by_handle = {str(position.annotation_handle): position for position in positions}
    actual_frame_counts = Counter(int(position.frame_index) for position in positions)
    _require(
        actual_frame_counts == raw.production_frame_counts,
        f"Loaded position counts by frame differ: {actual_frame_counts}/{raw.production_frame_counts}",
    )
    actual_category_counts: Counter[str] = Counter()
    for handle, position in position_by_handle.items():
        expected_row = raw.actionable_by_handle[handle]
        expected_frame = int(expected_row.get("frame_index") or 0)
        _require(
            int(position.frame_index) == expected_frame,
            f"Frozen frame mismatch for {handle}: {position.frame_index}/{expected_frame}",
        )
        category = _text(mapping_by_frame[expected_frame].category_code).upper()
        _require(
            category == _text(expected_row.get("frame_category_code")).upper(),
            f"Frozen category mismatch for {handle}.",
        )
        actual_category_counts[category] += 1
    _require(
        actual_category_counts == raw.production_category_counts,
        f"Loaded category coverage differs: {actual_category_counts}/{raw.production_category_counts}",
    )

    beam_frames = {
        index for index, item in mapping_by_frame.items()
        if _text(item.category_code).upper() == "BEAMS"
    }
    beam_positions = [p for p in positions if int(p.frame_index) in beam_frames]
    _require(
        all(
            _text(p.beam_name)
            and _text(p.beam_label_handle)
            and _text(p.beam_assignment_method)
            for p in beam_positions
        ),
        "At least one BEAMS position has incomplete frozen beam ownership.",
    )
    _require(
        all(
            not _text(p.beam_name) and not _text(p.beam_label_handle)
            for p in positions if int(p.frame_index) not in beam_frames
        ),
        "A non-BEAMS position contains beam ownership.",
    )

    vertical_frames = {
        index for index, item in mapping_by_frame.items()
        if _text(item.category_code).upper() == "VERTICALS"
    }
    vertical_positions = [p for p in positions if int(p.frame_index) in vertical_frames]
    _require(
        len(vertical_positions) == EXPECTED_VERTICAL_ASSIGNMENTS_FROZEN,
        f"Loaded vertical positions: {len(vertical_positions)}/{EXPECTED_VERTICAL_ASSIGNMENTS_FROZEN}",
    )
    _require(
        all(
            _text(p.vertical_section_title)
            and _text(p.vertical_section_label_handle)
            and _text(p.vertical_section_divider_handle)
            and _text(p.vertical_section_kind)
            and p.vertical_section_order is not None
            and _text(p.vertical_assignment_method) == "AUTO_VERTICAL_SECTION_V1"
            for p in vertical_positions
        ),
        "At least one vertical position has incomplete frozen section ownership.",
    )
    _require(
        all(
            not _text(p.vertical_section_title)
            and not _text(p.vertical_section_label_handle)
            and not _text(p.vertical_section_divider_handle)
            for p in positions if int(p.frame_index) not in vertical_frames
        ),
        "A non-VERTICALS position contains vertical-section ownership.",
    )
    section_keys = {
        (
            int(p.frame_index),
            _text(p.vertical_section_label_handle),
            _text(p.vertical_section_divider_handle),
            _text(p.vertical_section_title),
            _text(p.vertical_section_kind),
            int(p.vertical_section_order),
        )
        for p in vertical_positions
    }
    _require(
        len(section_keys) == EXPECTED_VERTICAL_SECTIONS_FROZEN,
        f"Loaded vertical sections: {len(section_keys)}/{EXPECTED_VERTICAL_SECTIONS_FROZEN}",
    )
    for frame_index in vertical_frames:
        orders = {key[5] for key in section_keys if key[0] == frame_index}
        _require(
            orders == set(range(1, len(orders) + 1)),
            f"Vertical section order is not contiguous in frame {frame_index}.",
        )

    return {
        "frames": len(frames),
        "production_positions": len(positions),
        "rows_by_frame": {str(key): value for key, value in sorted(actual_frame_counts.items())},
        "rows_by_category": dict(sorted(actual_category_counts.items())),
        "shape_counts": dict(sorted(Counter(_text(p.shape_code).upper() for p in positions).items())),
        "beam_positions": len(beam_positions),
        "beam_sections": len({_text(p.beam_label_handle) for p in beam_positions}),
        "vertical_positions": len(vertical_positions),
        "vertical_sections": len(section_keys),
        "global_production_identities": len({p.group_key for p in positions}),
    }


def _assert_trace(
    trace_path: Path,
    positions: Sequence[Any],
    mappings: Sequence[Any],
    raw: RawInputs,
) -> dict[str, Any]:
    from lion_rebar.export.frozen_export_config import verify_trace_against_freeze

    trace = _read_json(trace_path)
    _require(
        trace.get("schema") == "lion-rebar-excel-traceability/v3",
        "Unexpected Excel traceability schema.",
    )
    _require(
        int(trace.get("safe_positions") or 0) == len(positions),
        "Trace safe_positions differs from loaded production positions.",
    )
    expected_global_groups = len({position.group_key for position in positions})
    _require(
        int(trace.get("global_position_count") or 0) == expected_global_groups,
        "Trace global_position_count differs from production identities.",
    )
    freeze_problems = verify_trace_against_freeze(raw.root, trace)
    _require(
        not freeze_problems,
        "Trace/freeze mismatch: " + "; ".join(freeze_problems),
    )

    mapping_by_frame = {int(item.frame_index): item for item in mappings}
    trace_frames = [
        row for row in trace.get("frames", []) if isinstance(row, dict)
    ]
    _require(
        len(trace_frames) == len(mappings),
        "Trace does not contain every frozen frame exactly once.",
    )
    _require(
        Counter(int(row.get("frame_index") or 0) for row in trace_frames)
        == Counter(mapping_by_frame.keys()),
        "Trace frame indices do not match the frozen config.",
    )
    for row in trace_frames:
        index = int(row.get("frame_index") or 0)
        frozen = mapping_by_frame[index]
        _require(
            _text(row.get("category_code")).upper()
            == _text(frozen.category_code).upper()
            and _text(row.get("sheet_name")) == _text(frozen.sheet_name),
            f"Trace frame {index} category/sheet differs from freeze.",
        )

    rows = [row for row in trace.get("rows", []) if isinstance(row, dict)]
    drawings = [row for row in rows if row.get("kind") == "DRAWING"]
    totals = [row for row in rows if row.get("kind") == "TOTAL"]
    section_headers = [
        row for row in rows if row.get("kind") == "VERTICAL_SECTION"
    ]
    _require(drawings, "Trace has no source DRAWING rows.")
    _require(totals, "Trace has no TOTAL rows.")
    drawing_ids = [_text(row.get("source_group_id")) for row in drawings]
    _require(
        all(drawing_ids) and len(drawing_ids) == len(set(drawing_ids)),
        "Trace source_group_id values are empty or duplicated.",
    )
    drawing_by_id = {key: row for key, row in zip(drawing_ids, drawings)}

    production_handles = {
        str(position.annotation_handle) for position in positions
    }
    position_by_handle = {
        str(position.annotation_handle): position for position in positions
    }
    drawing_handles: list[str] = []
    source_handles_by_frame: Counter[int] = Counter()
    for row in drawings:
        handles = [
            str(value) for value in (row.get("annotation_handles") or [])
        ]
        _require(
            handles,
            f"Source group {_text(row.get('source_group_id'))} has no annotations.",
        )
        _require(
            int(row.get("source_annotation_count") or 0) == len(handles),
            "A DRAWING row has inconsistent source_annotation_count.",
        )
        terms = list(row.get("count_formula_terms") or [])
        _require(
            len(terms) == len(handles),
            "A DRAWING row does not preserve one count term per annotation.",
        )
        _require(
            int(row.get("count_total") or 0)
            == sum(int(value) for value in terms),
            "A DRAWING row has an inconsistent count total.",
        )
        frame_index = int(row.get("frame_index") or 0)
        frozen = mapping_by_frame.get(frame_index)
        _require(
            frozen is not None,
            f"DRAWING row belongs to unknown frame {frame_index}.",
        )
        _require(
            _text(row.get("category_code")).upper()
            == _text(frozen.category_code).upper()
            and _text(row.get("sheet")) == _text(frozen.sheet_name),
            f"DRAWING row category/sheet differs from frame {frame_index} freeze.",
        )
        drawing_handles.extend(handles)
        source_handles_by_frame[frame_index] += len(handles)
    _assert_exact_handle_coverage(
        drawing_handles,
        production_handles,
        excluded=raw.non_production_handles,
        label="Trace source rows",
    )
    _require(
        source_handles_by_frame == raw.production_frame_counts,
        "Trace source coverage by frame differs from the batch/freeze inputs.",
    )

    annotation_map = [
        row
        for row in trace.get("annotation_position_map", [])
        if isinstance(row, dict)
    ]
    mapped_handles = [
        _text(row.get("annotation_handle")) for row in annotation_map
    ]
    _assert_exact_handle_coverage(
        mapped_handles,
        production_handles,
        excluded=raw.non_production_handles,
        label="Trace annotation_position_map",
    )
    for mapped in annotation_map:
        handle = _text(mapped.get("annotation_handle"))
        source_id = _text(mapped.get("source_group_id"))
        drawing = drawing_by_id.get(source_id)
        _require(
            drawing is not None,
            f"Annotation map points to unknown source group: {source_id}",
        )
        _require(
            handle
            in {
                str(value)
                for value in (drawing.get("annotation_handles") or [])
            }
            and int(mapped.get("source_row") or 0)
            == int(drawing.get("row") or 0)
            and _text(mapped.get("source_sheet"))
            == _text(drawing.get("sheet"))
            and int(mapped.get("frame_index") or 0)
            == int(position_by_handle[handle].frame_index),
            f"Annotation map provenance disagrees for {handle}.",
        )

    total_handles: list[str] = []
    total_source_ids: list[str] = []
    for row in totals:
        handles = [
            str(value) for value in (row.get("annotation_handles") or [])
        ]
        source_ids = [
            str(value) for value in (row.get("source_group_ids") or [])
        ]
        source_cells = list(row.get("source_cells") or [])
        terms = list(row.get("count_formula_terms") or [])
        _require(
            int(row.get("annotation_count") or 0) == len(handles),
            "A TOTAL row has inconsistent annotation_count.",
        )
        _require(
            int(row.get("source_group_count") or 0)
            == len(source_ids)
            == len(source_cells),
            "A TOTAL row has inconsistent source references.",
        )
        _require(
            int(row.get("count_total") or 0)
            == sum(int(value) for value in terms),
            "A TOTAL row has an inconsistent count total.",
        )
        total_handles.extend(handles)
        total_source_ids.extend(source_ids)
    _assert_exact_handle_coverage(
        total_handles,
        production_handles,
        excluded=raw.non_production_handles,
        label="Trace total rows",
    )
    _require(
        Counter(total_source_ids) == Counter(drawing_ids),
        "TOTAL source references do not cover every DRAWING group exactly once.",
    )
    _require(
        len(totals) == expected_global_groups,
        "Trace TOTAL rows differ from global production identities.",
    )

    beam_frames = {
        index
        for index, item in mapping_by_frame.items()
        if _text(item.category_code).upper() == "BEAMS"
    }
    beam_positions = [
        position
        for position in positions
        if int(position.frame_index) in beam_frames
    ]
    beam_drawings = [
        row
        for row in drawings
        if int(row.get("frame_index") or 0) in beam_frames
    ]
    _require(
        all(
            _text(row.get("row_granularity"))
            == "ANNOTATION_WITHIN_BEAM"
            and int(row.get("source_annotation_count") or 0) == 1
            for row in beam_drawings
        ),
        "BEAMS source rows are not one row per annotation.",
    )
    beam_export = trace.get("beam_export")
    _require(isinstance(beam_export, dict), "Trace has no beam_export block.")
    _require(
        int(beam_export.get("beam_position_rows") or 0)
        == len(beam_positions)
        and int(beam_export.get("beam_count") or 0)
        == len(
            {
                _text(position.beam_label_handle)
                for position in beam_positions
            }
        )
        and beam_export.get("one_row_per_annotation") is True,
        "Trace beam summary differs from frozen production positions.",
    )

    vertical_positions = [
        position
        for position in positions
        if _text(
            mapping_by_frame[int(position.frame_index)].category_code
        ).upper()
        == "VERTICALS"
    ]
    vertical_drawings = [
        row
        for row in drawings
        if _text(row.get("vertical_section_label_handle"))
    ]
    expected_section_keys = {
        (
            int(position.frame_index),
            _text(position.vertical_section_label_handle),
            _text(position.vertical_section_divider_handle),
        )
        for position in vertical_positions
    }
    header_keys = {
        (
            int(row.get("frame_index") or 0),
            _text(row.get("vertical_section_label_handle")),
            _text(row.get("vertical_section_divider_handle")),
        )
        for row in section_headers
    }
    _require(
        header_keys == expected_section_keys
        and len(section_headers) == len(expected_section_keys),
        "Trace vertical headers do not match the frozen section set.",
    )
    vertical_export = trace.get("vertical_export")
    _require(
        isinstance(vertical_export, dict),
        "Trace has no vertical_export block.",
    )
    _require(
        int(vertical_export.get("section_count") or 0)
        == len(expected_section_keys)
        and int(vertical_export.get("approved_annotation_count") or 0)
        == len(vertical_positions)
        and int(vertical_export.get("source_group_row_count") or 0)
        == len(vertical_drawings)
        and int(vertical_export.get("collapsed_annotation_count") or 0)
        == len(vertical_positions) - len(vertical_drawings)
        and vertical_export.get("cross_section_merge_on_source") is False
        and vertical_export.get("one_row_per_annotation") is False,
        "Trace vertical summary differs from frozen production positions.",
    )

    return {
        "source_group_rows": len(drawings),
        "source_groups_by_category": dict(
            sorted(
                Counter(
                    _text(row.get("category_code")).upper()
                    for row in drawings
                ).items()
            )
        ),
        "annotation_source_map": len(annotation_map),
        "total_rows": len(totals),
        "total_source_references": len(total_source_ids),
        "vertical_source_groups": len(vertical_drawings),
        "vertical_collapsed_annotations": (
            len(vertical_positions) - len(vertical_drawings)
        ),
    }


def _assert_integrity(integrity: Any, root: Path) -> None:
    _require(integrity.applied, "Final Control did not run in apply mode.")
    _require(
        integrity.actionable_positions == EXPECTED_ACTIONABLE,
        "Final Control actionable count is not 586.",
    )
    _require(
        integrity.approved_after == EXPECTED_APPROVED,
        "Final Control approved count is not 582.",
    )
    _require(
        integrity.non_production_positions == EXPECTED_NON_PRODUCTION,
        "Final Control NON_PRODUCTION count is not 4.",
    )
    _require(
        integrity.accounted_positions == EXPECTED_ACTIONABLE,
        "Final Control does not account for all 586 actionable rows.",
    )
    _require(
        integrity.unresolved_positions == 0,
        "Final Control still has unresolved positions.",
    )
    _require(
        integrity.safe_confirmation_pending == 0,
        "Final Control still has safe confirmations pending.",
    )
    _require(
        integrity.engineering_decisions_pending == 0,
        "Final Control still has engineering decisions pending.",
    )
    _require(
        integrity.integrity_passed,
        "Final Control integrity_passed is false.",
    )
    _require(
        integrity.full_specification_complete,
        "Final Control full_specification_complete is false.",
    )
    _require(
        integrity.duplicate_groups_remaining == 0,
        "Final Control still has duplicate geometry groups.",
    )
    _require(
        integrity.identity_failures == 0,
        "Final Control has data-identity failures.",
    )
    _require(
        integrity.beam_assignments_frozen
        == EXPECTED_FRAME_ASSIGNMENTS_FROZEN,
        "Final Control did not freeze all 582 frame/beam assignments.",
    )
    _require(
        integrity.vertical_assignments_frozen
        == EXPECTED_VERTICAL_ASSIGNMENTS_FROZEN,
        "Final Control did not freeze 126 vertical assignments.",
    )
    _require(
        integrity.vertical_sections_frozen
        == EXPECTED_VERTICAL_SECTIONS_FROZEN,
        "Final Control did not freeze 12 vertical sections.",
    )
    _require(
        integrity.freeze_required,
        "Final Control did not require/create a freeze.",
    )
    _require(
        not _text(integrity.export_preflight_error),
        f"Final Control export preflight error: {integrity.export_preflight_error}",
    )
    _require(
        not integrity.issues,
        "Final Control returned integrity issues: "
        + "; ".join(issue.code for issue in integrity.issues),
    )
    freeze_path = Path(_text(integrity.freeze_manifest_path))
    _require(
        freeze_path == root / "working" / "final_control_freeze.json"
        and freeze_path.is_file(),
        "Final Control freeze manifest is missing or outside the verification project.",
    )


def _execute(root: Path, output: Path, raw: RawInputs) -> dict[str, Any]:
    from lion_rebar.export.excel_export import (
        generate_excel_export,
        load_export_context,
    )
    from lion_rebar.project.final_control_lifecycle import (
        STATUS_VALID,
        assert_export_allowed,
        freeze_state,
    )
    from lion_rebar.project.integrity import reconcile_project_integrity

    output = output.resolve()
    _require(output.suffix.lower() == ".xlsx", "--output must end in .xlsx")
    trace_output = output.with_name(
        output.stem + "_TRACEABILITY.json"
    )
    _require(
        not output.exists(),
        f"Refusing to overwrite an existing workbook: {output}",
    )
    _require(
        not trace_output.exists(),
        f"Refusing to overwrite an existing trace: {trace_output}",
    )

    integrity = reconcile_project_integrity(
        root,
        report_path=raw.report_path,
        apply=True,
    )
    _assert_integrity(integrity, root)
    status, reason = freeze_state(root)
    _require(
        status == STATUS_VALID,
        f"Final Control freeze is not VALID: {status}/{reason}",
    )
    blockers = assert_export_allowed(root)
    _require(
        not blockers,
        "Export lifecycle blockers: " + "; ".join(blockers),
    )

    project, engine = _load_project_engine(root)
    frames, positions = load_export_context(project, (), engine)
    configs, mappings = _frozen_frame_configs(root, frames, raw)
    position_summary = _assert_positions(
        frames,
        positions,
        mappings,
        raw,
    )
    result = generate_excel_export(
        project,
        engine,
        frames,
        positions,
        configs,
        output,
        require_frozen_config=True,
    )
    _require(
        result.workbook_path.is_file(),
        "Excel generator returned no workbook.",
    )
    _require(
        result.traceability_path.is_file(),
        "Excel generator returned no trace file.",
    )
    _require(
        result.frame_count == len(mappings),
        "Excel result frame_count differs from frozen config.",
    )
    _require(
        result.safe_position_count == len(positions),
        "Excel result safe_position_count differs from production input.",
    )
    trace_summary = _assert_trace(
        result.traceability_path,
        positions,
        mappings,
        raw,
    )
    _require(
        result.grouped_row_count == trace_summary["source_group_rows"],
        "Excel grouped_row_count differs from trace.",
    )
    _require(
        result.total_group_count == trace_summary["total_rows"],
        "Excel total_group_count differs from trace.",
    )
    _require(
        not assert_export_allowed(root),
        "Export lifecycle became blocked during workbook generation.",
    )
    return {
        "mode": "execute",
        "project_root": str(root),
        "output": str(result.workbook_path),
        "trace": str(result.traceability_path),
        "integrity": {
            "actionable_positions": integrity.actionable_positions,
            "approved_after": integrity.approved_after,
            "non_production_positions": integrity.non_production_positions,
            "accounted_positions": integrity.accounted_positions,
            "unresolved_positions": integrity.unresolved_positions,
            "beam_assignments_frozen": integrity.beam_assignments_frozen,
            "vertical_assignments_frozen": (
                integrity.vertical_assignments_frozen
            ),
            "vertical_sections_frozen": integrity.vertical_sections_frozen,
            "integrity_passed": integrity.integrity_passed,
            "full_specification_complete": (
                integrity.full_specification_complete
            ),
            "freeze_status": status,
            "export_allowed": True,
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
        help="isolated verification-project root used for rc35 acceptance",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new .xlsx path; required with --execute",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "apply Final Control to the verification copy and generate "
            "the workbook"
        ),
    )
    args = parser.parse_args()
    root = _verification_root(args.project_root)
    raw = _assert_raw_inputs(root)

    if not args.execute:
        _require(
            args.output is None,
            "--output is accepted only together with --execute",
        )
        print(
            json.dumps(
                {
                    "mode": "read-only",
                    "project_root": str(root),
                    "inputs": raw.summary(),
                    "next_step": (
                        "Run the spreadsheet operation marker externally, "
                        "then run again with --execute --output <new.xlsx>."
                    ),
                    "execute_assertions": {
                        "accounting": (
                            f"{EXPECTED_ACTIONABLE} = "
                            f"{EXPECTED_APPROVED} production + "
                            f"{EXPECTED_NON_PRODUCTION} evidenced "
                            "non-production"
                        ),
                        "unresolved_positions": 0,
                        "integrity_passed": True,
                        "full_specification_complete": True,
                        "frame_beam_assignments_frozen": (
                            EXPECTED_FRAME_ASSIGNMENTS_FROZEN
                        ),
                        "vertical_assignments_frozen": (
                            EXPECTED_VERTICAL_ASSIGNMENTS_FROZEN
                        ),
                        "vertical_sections_frozen": (
                            EXPECTED_VERTICAL_SECTIONS_FROZEN
                        ),
                        "freeze_status": "VALID",
                        "export_allowed": True,
                        "production_trace_coverage": "exactly once",
                        "non_production_trace_coverage": "absent",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    _require(
        args.output is not None,
        "--output is required with --execute",
    )
    print(
        json.dumps(
            _execute(root, args.output, raw),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import csv
import html
import json
import math
import re
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from lion_rebar.dxf.geometry_candidates import (
    F006_HOOKED_FAMILY_CONFIDENCE,
    F006_STRONG_CONFIDENCE,
    GeometryCandidateAnalysis,
    GeometryCandidateContext,
    build_geometry_candidate_context,
    find_geometry_candidates,
)
from lion_rebar.dxf.disagreement_report import (
    build_disagreement_report,
    disagreement_summary_lines,
)
from lion_rebar.export.frame_detection import assign_point_to_frame, detect_drawing_frames
from lion_rebar.export.excel_export import (
    ExcelExportError,
    F006AsymmetricHookError,
    _entity_insert,
    _segments_for_link,
)
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    parameters_from_segments,
)
from lion_rebar.dxf.matcher_evidence import (
    build_review_group_key,
    evaluate_report_row,
    review_group_components,
)
from lion_rebar.dxf.local_detail_resolver import build_local_detail_index
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.printed_detail_dimensions import (
    PrintedShape,
    near_miss_note,
    printed_dimensions_for_detail,
    shape_from_printed_dimensions,
)
from lion_rebar.dxf.position_key_resolver import (
    build_position_key_legend_index,
    resolve_position_key_annotation,
    table_row_position_key,
)
from lion_rebar.dxf.proxy_adapter import (
    PROXY_ASSOCIATION_CODE,
    PROXY_DECLARED_LENGTH_CODE,
    PROXY_DECLARED_SHAPE_CODE,
    is_proxy_rebar_annotation,
    proxy_geometry_handles,
    three_leg_shape_from_directions,
)
from lion_rebar.dxf.proxy_adapter import (
    _THREE_LEG_PARALLEL_TOLERANCE_DEG,
    _angle_between_deg,
    _perpendicular_offset,
    reduction_offset_angle_deg,
)
from lion_rebar.manual_assignment import (
    ManualGeometryError,
    summarize_selected_geometry,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.stirrup_group_resolver import (
    resolve_stirrup_length_group_annotation,
    resolve_stirrup_operator_area_annotation,
)
from lion_rebar.dxf.text_parser import (
    ParsedRebarText,
    looks_like_rebar_text,
    parse_rebar_text,
)
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.engine.shape_index import EngineShapeIndex
from lion_rebar.project.approval_origin import approval_origin
from lion_rebar.project.data_identity import DataIdentity, IDENTITY_KEY
from lion_rebar.utils.files import atomic_write_text


@dataclass(frozen=True)
class BatchGeometryReportResult:
    report_dir: Path
    json_path: Path
    csv_path: Path
    html_path: Path
    zip_path: Path
    total_annotations: int
    strong_count: int
    review_count: int
    risk_count: int
    no_candidate_count: int
    approved_count: int = 0
    processed_annotations: int = 0
    cancelled: bool = False
    error_count: int = 0
    geometry_strong_count: int = 0
    f006_strong_count: int = 0
    context_count: int = 0
    actionable_count: int = 0
    candidate_count: int = 0
    conflict_count: int = 0
    master_ambiguity_count: int = 0
    ownership_conflict_count: int = 0
    review_group_count: int = 0
    ownership_reassigned_count: int = 0
    ownership_tiebreak_resolved_count: int = 0
    dimension_evidence_applied_count: int = 0
    dimension_master_resolved_count: int = 0
    dimension_candidate_tiebreak_count: int = 0
    f007_extended_association_count: int = 0
    ownership_recovery_verified_count: int = 0
    position_key_legend_resolved_count: int = 0
    position_key_manual_override_count: int = 0
    stirrup_length_group_resolved_count: int = 0
    f001_extended_exact_length_count: int = 0
    proxy_parent_provenance_count: int = 0
    named_u_subtype_context_resolved_count: int = 0
    f006_form_strong_count: int = 0
    f006_production_exact_count: int = 0
    f006_production_derived_count: int = 0
    f006_production_review_count: int = 0
    f006_asymmetric_hook_count: int = 0
    f006_printed_dimensions_missing_count: int = 0
    f006_printed_dimensions_mismatch_count: int = 0

    def to_dict(self) -> dict:
        payload = asdict(self)
        for key in ("report_dir", "json_path", "csv_path", "html_path", "zip_path"):
            payload[key] = str(payload[key])
        return payload


def count_batch_annotations(
    entities: Sequence[DxfEntityRecord],
    *,
    policy: RecognitionPolicy | dict | None = None,
) -> int:
    resolved_policy = (
        policy if isinstance(policy, RecognitionPolicy) else RecognitionPolicy.from_settings(policy)
    )
    allowed_annotation_types = {"BLOCK_ATTR_TEXT"}
    if resolved_policy.include_text_mtext_annotations:
        allowed_annotation_types.update({"TEXT", "MTEXT"})
    return sum(
        entity.entity_type.upper() in allowed_annotation_types
        and looks_like_rebar_text(entity.text or "")
        for entity in entities
    )


def build_batch_geometry_report(
    entities: list[DxfEntityRecord],
    output_parent: Path,
    *,
    project_name: str,
    approved_links: dict[str, dict] | None = None,
    generated_at: datetime | None = None,
    shape_definitions: Iterable[ShapeDefinition] | None = None,
    control_tests: Iterable[Mapping[str, Any]] | None = None,
    policy: RecognitionPolicy | dict | None = None,
    engine_metadata: Mapping[str, Any] | None = None,
    data_identity: DataIdentity | Mapping[str, Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    checkpoint_every: int = 10,
    skip_approved: bool = True,
    operator_areas: Sequence[Any] = (),
    checkpoint_parent: Path | None = None,
) -> BatchGeometryReportResult:
    """Analyze all valid annotations without blocking the Qt user interface.

    The caller may run this function in a worker thread and provide a progress
    callback plus a thread-safe cancellation check. Geometry and endpoint graphs
    are indexed once for the whole DXF. A checkpoint is written atomically after
    each chunk, and one bad annotation becomes an error row instead of aborting
    the complete report.

    ``checkpoint_parent`` мести МЕЖДИННИЯ ``progress.json`` другаде. Крайните
    файлове -- отчетът и ``progress_final.json`` -- винаги остават в
    ``report_dir``.

    ЗАЩО. Междинната точка е целият отчет дотук, записан наново с ``fsync``
    на всеки ``checkpoint_every`` надписа. Измерено 21.09.2026 на „т-1“
    (3 006 надписа): 300 записа, файлът расте до 108 MB, сборът е около 16 GB.
    Когато ``report_dir`` е в Google Drive, тези 16 GB минават през
    стриймващата файлова система. Същият по размер чертеж „KOFR_B“ през
    ``full_path.py`` (локален запис) дава 6 435 s анализ.

    Разходът е квадратичен по брой надписи, затова важи точно за големите
    чертежи, където и без това боли. По подразбиране е ``None`` -- тогава
    точката е в ``report_dir`` и поведението е непроменено.
    """

    generated_at = generated_at or datetime.now().astimezone()
    identity_payload = (
        data_identity.to_dict()
        if isinstance(data_identity, DataIdentity)
        else dict(data_identity or {})
    )
    timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
    safe_project = _safe_name(project_name or "LION_REBAR_PROJECT")
    report_dir = output_parent / f"{safe_project}_BATCH_REVIEW_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)
    if checkpoint_parent is None:
        checkpoint_dir = report_dir
    else:
        checkpoint_dir = Path(checkpoint_parent) / report_dir.name
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

    approved_links = approved_links or {}
    resolved_policy = (
        policy if isinstance(policy, RecognitionPolicy) else RecognitionPolicy.from_settings(policy)
    )
    allowed_annotation_types = {"BLOCK_ATTR_TEXT"}
    if resolved_policy.include_text_mtext_annotations:
        allowed_annotation_types.update({"TEXT", "MTEXT"})
    annotations = [
        entity
        for entity in entities
        if entity.entity_type.upper() in allowed_annotation_types
        and looks_like_rebar_text(entity.text or "")
    ]
    total_annotations = len(annotations)
    checkpoint_every = max(1, int(checkpoint_every))
    rows: list[dict] = []
    annotations_by_handle = {str(entity.handle): entity for entity in annotations}
    analyses_by_handle: dict[str, GeometryCandidateAnalysis] = {}
    errors: list[str] = []
    cancelled = False

    if progress_callback is not None:
        progress_callback(0, total_annotations, "Подготовка на геометричния индекс...")
    context = build_geometry_candidate_context(entities, policy=resolved_policy)
    shape_definitions = tuple(shape_definitions or ())
    # Compile the approved ENGINE topology once for the complete batch.  Every
    # candidate chain then compares only with its segment/turn/closed shortlist,
    # rather than walking the whole library for every annotation.
    shape_index = EngineShapeIndex.from_shapes(shape_definitions)
    control_tests = tuple(control_tests or ())
    local_detail_index = build_local_detail_index(
        annotations,
        entities,
        policy=resolved_policy,
    )

    for index, annotation in enumerate(annotations, start=1):
        if cancel_check is not None and cancel_check():
            cancelled = True
            break

        parsed = parse_rebar_text(annotation.text or "")
        annotation_class = _annotation_class(parsed)
        approved = approved_links.get(str(annotation.handle))
        try:
            if annotation_class != "ACTIONABLE":
                row = _context_row(annotation, parsed, resolved_policy, annotation_class)
                selected_handles = ()
                analysis = None
            else:
                approved_handles = tuple(
                    str(value) for value in (approved or {}).get("geometry_handles", [])
                )
                approved_is_valid = bool(approved_handles) and all(
                    handle in context.primitive_by_handle for handle in approved_handles
                )
                # Only an explicit owner decision may bypass the Matcher.  A
                # previous AUTO_BATCH_GATE record is merely cached evidence and
                # must be recomputed on every run.  Unknown legacy records are
                # also recomputed rather than silently receiving manual status.
                approval_kind = approval_origin(approved)
                approved_is_manual = approval_kind == "MANUAL"
                if skip_approved and approved and approved_is_valid and approved_is_manual:
                    row = _approved_row(annotation, approved, resolved_policy)
                    selected_handles = approved_handles
                    analysis = None
                else:
                    local_detail = local_detail_index.assignment_for(
                        str(annotation.handle)
                    )
                    analysis = find_geometry_candidates(
                        annotation,
                        entities,
                        max_candidates=8,
                        shape_definitions=shape_definitions,
                        control_tests=control_tests,
                        policy=resolved_policy,
                        context=context,
                        shape_index=shape_index,
                        local_detail_shape_code=(
                            local_detail.shape_code if local_detail else None
                        ),
                        local_detail_rule_code=(
                            local_detail.rule_code if local_detail else None
                        ),
                        local_detail_rule_note=(
                            local_detail.note if local_detail else None
                        ),
                        allowed_dimension_handles=(
                            local_detail.dimension_handles
                            if local_detail
                            and local_detail.shape_code == "F006"
                            else None
                        ),
                        excluded_dimension_handles=(),
                    )
                    analysis = _prefer_annotation_axis_candidate(
                        annotation, analysis, context
                    )
                    row = _analysis_row(analysis, approved)
                    if local_detail is not None:
                        row["local_detail_context_resolved"] = True
                        row["local_detail_shape_code"] = local_detail.shape_code
                        row["local_detail_label_handle"] = local_detail.label_handle
                        row["local_detail_label_text"] = local_detail.label_text
                        row["local_detail_label_distance_raw"] = (
                            local_detail.label_distance_raw
                        )
                        row["local_detail_dimension_handles"] = list(
                            local_detail.dimension_handles
                        )
                        row["local_detail_exclusive_dimension_handles"] = list(
                            local_detail.exclusive_dimension_handles
                        )
                        row["analysis_warnings"] = [
                            *list(row.get("analysis_warnings", [])),
                            local_detail.note,
                        ]
                    analyses_by_handle[str(annotation.handle)] = analysis
                    selected_handles = tuple(row.get("candidate_handles", ()))
                    if approval_kind == "AUTO":
                        row["previous_auto_approval_present"] = True
                        row["previous_auto_geometry_match"] = bool(
                            approved_handles
                            and tuple(sorted(approved_handles))
                            == tuple(sorted(selected_handles))
                        )
                        row["previous_auto_approved_at"] = str(
                            (approved or {}).get("approved_at") or ""
                        )
                    elif approval_kind == "UNKNOWN":
                        warnings = list(row.get("analysis_warnings", []))
                        warnings.append(
                            "Записът за предишно одобрение няма доказан USER/AUTO произход "
                            "и беше анализиран отново."
                        )
                        row["analysis_warnings"] = warnings
                    if approved and not approved_is_valid:
                        warnings = list(row.get("analysis_warnings", []))
                        warnings.append(
                            "Записаното одобрение сочи липсваща DXF геометрия и беше анализирано отново."
                        )
                        row["analysis_warnings"] = warnings
            row["annotation_type"] = annotation.entity_type
            row["annotation_count"] = parsed.count
            row["annotation_class"] = annotation_class
            row["data_quality_flags"] = _data_quality_flags(parsed)
            row["data_quality_notes"] = _data_quality_notes(parsed)
            row["quantity_review_required"] = _quantity_review_required(parsed)
            _apply_proxy_annotation_provenance(row, annotation)
            row.setdefault("global_conflict", False)
            row["svg_preview"] = _svg_preview(
                annotation,
                analysis,
                context=context,
                selected_handles=selected_handles,
            )
        except Exception as exc:  # noqa: BLE001 - one annotation must not destroy the report
            message = f"{annotation.handle}: {type(exc).__name__}: {exc}"
            errors.append(message)
            row = _error_row(annotation, message, resolved_policy)
            row["annotation_count"] = parsed.count
            row["annotation_class"] = annotation_class
            row["data_quality_flags"] = _data_quality_flags(parsed)
            row["data_quality_notes"] = _data_quality_notes(parsed)
            row["quantity_review_required"] = _quantity_review_required(parsed)
            row["global_conflict"] = False
            row["svg_preview"] = _svg_preview(
                annotation,
                None,
                context=context,
                selected_handles=(),
            )
        if identity_payload:
            row[IDENTITY_KEY] = dict(identity_payload)
        rows.append(row)

        if progress_callback is not None:
            progress_callback(index, total_annotations, annotation.text or annotation.handle)
        if index % checkpoint_every == 0:
            _write_checkpoint(
                checkpoint_dir,
                project_name=project_name,
                generated_at=generated_at,
                total_annotations=total_annotations,
                rows=rows,
                cancelled=False,
                errors=errors,
                data_identity=identity_payload,
            )

    processed_annotations = len(rows)
    # A proven legend key is stronger evidence than a weak local proximity
    # candidate.  Apply reference-only rows before local ownership allocation
    # so their discarded local handles cannot create a false conflict.
    _apply_position_key_legend_references(
        rows,
        annotations_by_handle=annotations_by_handle,
        entities=entities,
        context=context,
        shape_definitions=shape_definitions,
        control_tests=control_tests,
        policy=resolved_policy,
        shape_index=shape_index,
    )
    _resolve_global_geometry_ownership(rows, resolved_policy)
    for row in rows:
        if not row.get("ownership_reassigned"):
            continue
        handle = str(row.get("annotation_handle") or "")
        annotation = annotations_by_handle.get(handle)
        analysis = analyses_by_handle.get(handle)
        if annotation is None or analysis is None:
            continue
        row["svg_preview"] = _svg_preview(
            annotation,
            analysis,
            context=context,
            selected_handles=tuple(row.get("candidate_handles", ())),
        )
    _apply_global_geometry_conflicts(rows)
    _apply_stirrup_length_group_references(
        rows,
        annotations_by_handle=annotations_by_handle,
        entities=entities,
        context=context,
        shape_definitions=shape_definitions,
        control_tests=control_tests,
        policy=resolved_policy,
        shape_index=shape_index,
    )
    # Рамката на елемента решава останалите стремена — само когато всички
    # копия с тази L вътре са една форма. След по-строгото правило отгоре.
    _apply_operator_area_stirrup_references(
        rows,
        annotations_by_handle=annotations_by_handle,
        entities=entities,
        context=context,
        shape_definitions=shape_definitions,
        control_tests=control_tests,
        policy=resolved_policy,
        shape_index=shape_index,
        operator_areas=operator_areas,
    )
    _apply_f006_printed_production_dimensions(
        rows,
        entities=entities,
        shape_definitions=shape_definitions,
    )
    _apply_borderline_curved_f006_owned_dimension_recovery(
        rows,
        entities=entities,
        shape_definitions=shape_definitions,
        control_tests=control_tests,
        policy=resolved_policy,
        shape_index=shape_index,
    )
    # Beam staircase subtypes must be normalized to their approved
    # production identity before strict segment validation. Otherwise a valid
    # F013 -> F004 row is tested against the unsupported source subtype and is
    # terminally blocked before this engineering law can run.
    _apply_beam_engineering_shape_law(
        rows,
        annotations_by_handle=annotations_by_handle,
        entities=entities,
        shape_definitions=shape_definitions,
        policy=resolved_policy,
    )
    # Frame classification is produced by the engineering-shape pass above.
    # Only now can the strict proof distinguish VERTICALS from other frames.
    # It still runs before the proximity DIMENSION fallback, so nearby numbers
    # can neither overwrite nor manufacture segment sizes.
    _apply_single_open_lwpolyline_production_segments(rows, entities)
    # Втори източник за производствените размери на F006: DIMENSION
    # котировките. Прилага се САМО върху позиции, останали в production
    # review след доказания път по-горе, и само при точно уравнение.
    _apply_dimension_derived_production_sizes(rows, entities)
    _apply_born_from_case_production_sizes(rows, shape_definitions)
    # Owner-requested 2026-09-11 ("много позиции има за ръчно решение, това
    # не ми харесва"): surface the arm_koloni/arm_shaibi declared-shape
    # evidence as the operator's on-screen SUGGESTION, replacing whatever
    # the pass above just guessed from unrelated nearby DIMENSION entities
    # (real case: an irrelevant DIMENSION pair elsewhere on the sheet
    # numerically solved a plausible-looking but WRONG F003 for what the
    # owner confirmed live in Review Center was really an F002 diameter-
    # reduction splice, 310+21+120). Runs AFTER the dimension-hint pass
    # above so it always has the final say for these two dialects only.
    # Deliberately scoped to the suggestion display fields alone --
    # `master_shape_code`, `batch_status` and auto-approval eligibility are
    # untouched; the operator still confirms by hand, just no longer has to
    # reconstruct the right numbers themselves.
    _apply_proxy_declared_shape_suggestions(
        rows, annotations_by_handle=annotations_by_handle,
    )
    # Owner-requested 2026-09-11/12 ("искам програмата да е ТОП, без
    # компромиси"): when a position has NO real geometric candidate at all
    # (`candidate_handles` empty -- the "НЯМА КАНДИДАТ"/"Без DXF връзка"
    # case), the operator previously had to manually click through every
    # individual proxy fragment by hand to see anything in the drawing at
    # all (confirmed live: 10 separate ARC/LINE fragments for one stirrup).
    # That reference geometry is ALREADY attached to the annotation itself
    # (`proxy_geometry_handles`, set in proxy_adapter.py whenever
    # PROXY_TEXT_DECLARED_LENGTH applies) -- surface it as
    # `reference_geometry_handles` so the UI can show it automatically.
    # Deliberately a SEPARATE field, never written into `candidate_handles`
    # itself: that field feeds 15+ other subsystems (ownership-conflict
    # detection, review grouping, export) that must keep meaning "a proven
    # geometric match", which this reference-only geometry explicitly is
    # NOT -- see ui/main_window.py::_review_preview_reference_geometry.
    _apply_proxy_reference_geometry_fallback(
        rows, annotations_by_handle=annotations_by_handle,
    )
    # Owner-requested 2026-09-12: the strongest of those declared shapes stop
    # being a suggestion and become a real production result, so they no
    # longer queue for a manual review that adds no safety (proven live: a
    # whole review pass confirmed 11 arc-verified F006 stirrups as straight
    # bars instead). Strictly gated, and re-proven independently by
    # auto_gate.validate_production_segment_evidence before AUTO accepts it.
    _apply_proxy_declared_shape_auto_promotion(
        rows,
        annotations_by_handle=annotations_by_handle,
        shape_definitions=shape_definitions,
    )
    # „Форма от котите до надписа" (собственик, 19.09.2026: „действай").
    # Идва СЛЕД всички геометрични и proxy доказателства: пише само по
    # позиции без кандидат и без вече доказана форма, за да не отменя нищо.
    _apply_printed_detail_dimension_segments(
        rows,
        entities=entities,
        annotations_by_handle=annotations_by_handle,
        policy=resolved_policy,
        shape_definitions=shape_definitions,
    )
    _apply_proxy_column_group_labels(
        rows, annotations_by_handle=annotations_by_handle,
    )
    # Рамките, които операторът е начертал ПРЕДИ анализа. Идват СЛЕД
    # автоматичните етикети и попълват само празното: доказана автоматична
    # група не се пипа. Без това Review Center щеше да показва групата чак
    # след Финален контрол, а собственикът чертае рамките точно за да ги
    # види в прегледа.
    _apply_operator_area_group_labels(
        rows,
        annotations_by_handle=annotations_by_handle,
        operator_areas=operator_areas,
    )
    _apply_named_u_subtype_context(
        rows,
        shape_definitions=shape_definitions,
        policy=resolved_policy,
    )
    _apply_unanimous_family_consensus(rows)
    _apply_matcher_decisions(
        rows,
        shape_definitions=shape_definitions,
        policy=resolved_policy,
    )
    _apply_review_groups(rows)
    counts = _status_counts(rows)
    decision_counts = _matcher_decision_counts(rows)
    group_counts = _review_group_counts(rows)
    review_groups = _review_group_metadata(rows)
    # Разногласията се мерят върху ГОТОВИТЕ редове и не ги пипат: отчет, не
    # решение. Виж ``dxf/disagreement_report.py`` защо влизат само три
    # проверки и защо всяка пита самата програма вместо плосък допуск.
    disagreements = build_disagreement_report(
        rows,
        shape_definitions=shape_definitions,
    )
    base_name = f"{safe_project}_geometry_batch"
    json_path = report_dir / f"{base_name}.json"
    csv_path = report_dir / f"{base_name}.csv"
    html_path = report_dir / f"{base_name}.html"
    disagreement_path = report_dir / f"{base_name}_razminavaniya.csv"
    zip_path = output_parent / f"{report_dir.name}.zip"

    payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": project_name,
        "analysis_engine": dict(engine_metadata or {}),
        IDENTITY_KEY: dict(identity_payload),
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "automatic_approval": False,
        "cancelled": cancelled,
        "processed_annotations": processed_annotations,
        "total_annotations": total_annotations,
        "summary": {
            "actionable_positions": counts["actionable"],
            "candidate_coverage": counts["candidate"],
            "strong": counts["strong"],
            "geometry_strong": counts["geometry_strong"],
            "f006_strong": counts["f006_strong"],
            "f006_form_strong": counts["f006_form_strong"],
            "f006_production_exact": counts["f006_production_exact"],
            "f006_production_derived": counts["f006_production_derived"],
            "f006_production_review": counts["f006_production_review"],
            "f006_asymmetric_hook": counts["f006_asymmetric_hook"],
            "f006_printed_dimensions_missing": counts["f006_printed_dimensions_missing"],
            "f006_printed_dimensions_mismatch": counts["f006_printed_dimensions_mismatch"],
            "family_inferred": counts["family_inferred"],
            "manual_review": counts["review"],
            "risk": counts["risk"],
            "no_candidate": counts["none"],
            "approved_in_project": counts["approved"],
            "context_or_incomplete": counts["context"],
            # Backward-compatible historical name.  It remains the union of
            # MASTER ambiguity and exact geometry ownership conflicts.
            "global_conflicts": counts["conflict"],
            "master_ambiguities": counts["master_ambiguity"],
            "ownership_conflicts": counts["ownership_conflict"],
            "ownership_reassigned": counts["ownership_reassigned"],
            "ownership_tiebreak_resolved": counts["ownership_tiebreak_resolved"],
            "dimension_evidence_applied": counts["dimension_evidence_applied"],
            "dimension_master_resolved": counts["dimension_master_resolved"],
            "dimension_candidate_tiebreak": counts["dimension_candidate_tiebreak"],
            "f007_extended_association": counts["f007_extended_association"],
            "ownership_recovery_verified": counts["ownership_recovery_verified"],
            "position_key_legend_resolved": counts[
                "position_key_legend_resolved"
            ],
            "position_key_manual_override": counts[
                "position_key_manual_override"
            ],
            "stirrup_length_group_resolved": counts[
                "stirrup_length_group_resolved"
            ],
            "f001_extended_exact_length": counts[
                "f001_extended_exact_length"
            ],
            "proxy_parent_provenance": counts["proxy_parent_provenance"],
            "named_u_subtype_context_resolved": counts[
                "named_u_subtype_context_resolved"
            ],
            "total_conflicts": counts["conflict"],
            "review_groups": group_counts["actionable_groups"],
            "multi_position_review_groups": group_counts["multi_position_groups"],
            "grouped_positions_saved": group_counts["positions_saved"],
            "largest_review_group": group_counts["largest_group"],
            "errors": counts["error"],
            "matcher_dispositions": decision_counts,
        },
        "review_groups": review_groups,
        "recognition_policy": resolved_policy.to_dict(),
        "recognition_laws": list(resolved_policy.laws_bg()),
        "disagreements": disagreements,
        "errors": errors,
        "rows": [{key: value for key, value in row.items() if key != "svg_preview"} for row in rows],
    }
    atomic_write_text(
        json_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    _write_csv(csv_path, rows)
    _write_disagreement_csv(disagreement_path, disagreements)
    _write_category_csvs(report_dir, base_name, rows)
    html_path.write_text(
        _render_html(
            project_name,
            generated_at,
            rows,
            counts,
            total_annotations=total_annotations,
            cancelled=cancelled,
        ),
        encoding="utf-8",
    )

    readme = report_dir / "README_BG.txt"
    state_label = "ЧАСТИЧЕН — ПРЕКРАТЕН ОТ ПОТРЕБИТЕЛЯ" if cancelled else "ЗАВЪРШЕН"
    readme.write_text(
        "LION REBAR пакетен геометричен анализ\n"
        "=====================================\n\n"
        f"Статус: {state_label}\n"
        f"Обработени надписи: {processed_annotations} от {total_annotations}\n"
        f"Реални позиции с пълен брой/Ø/L: {counts['actionable']}\n"
        f"Контекстни или непълни надписи: {counts['context']}\n"
        f"Позиции с кандидат: {counts['candidate']}\n"
        f"F003/F005, разрешени по именуван контекст: {counts['named_u_subtype_context_resolved']}\n"
        f"Еднозначни за контролна извадка: {decision_counts.get('CONTROL', 0)}\n"
        f"Истински ръчни казуси: {decision_counts.get('MANUAL', 0)}\n"
        f"Недостатъчно доказателство: {decision_counts.get('UNKNOWN', 0)}\n"
        f"MASTER двусмислия: {counts['master_ambiguity']}\n"
        f"Конфликти за DXF собственост: {counts['ownership_conflict']}\n"
        f"Разрешени еднакви диапазони с тройно доказателство: {counts['ownership_tiebreak_resolved']}\n"
        f"Позиции с приложени размери A–E: {counts['dimension_evidence_applied']}\n"
        f"MASTER двусмислия, разрешени по размери A–E: {counts['dimension_master_resolved']}\n"
        f"Възстановени чрез свободен резервен кандидат: {counts['ownership_reassigned']}\n"
        f"Локални равенства, разрешени по размери A–E: {counts['dimension_candidate_tiebreak']}\n"
        f"Точни F007 в тесния разширен радиус: {counts['f007_extended_association']}\n"
        f"Проверени силни резервни кандидати: {counts['ownership_recovery_verified']}\n"
        f"Разрешени чрез позиционен ключ и легенда: {counts['position_key_legend_resolved']}\n"
        f"От тях заместени слаби кандидати: {counts['position_key_manual_override']}\n"
        f"Разрешени чрез точна дъгова L/група стремена: {counts['stirrup_length_group_resolved']}\n"
        f"Разрешени чрез отделния F001 допуск: {counts['f001_extended_exact_length']}\n"
        f"Разрешени чрез proxy parent provenance: {counts['proxy_parent_provenance']}\n"
        f"Представителни групи за преглед: {group_counts['actionable_groups']} "
        f"(спестени повторения: {group_counts['positions_saved']})\n"
        f"Грешки по отделни надписи: {counts['error']}\n\n"
        "Този пакет е диагностичен. Няма автоматично одобрени връзки.\n"
        "Отвори HTML файла за общ визуален преглед.\n"
        "JSON е пълният машинен отчет, а CSV е табличният изход.\n"
        "Отделните CSV файлове групират одобрени, силни, рискови и нерешени позиции.\n\n"
        + "\n".join(disagreement_summary_lines(disagreements))
        + f"\nПодробно: {disagreement_path.name}\n",
        encoding="utf-8",
    )

    _write_checkpoint(
        report_dir,
        project_name=project_name,
        generated_at=generated_at,
        total_annotations=total_annotations,
        rows=rows,
        cancelled=cancelled,
        errors=errors,
        final=True,
        data_identity=identity_payload,
    )

    # Крайната точка е записана: междинната вече не пази нищо. Чисти се САМО
    # когато е в отделна папка, която сме направили ние -- в ``report_dir``
    # ``progress.json`` е част от отчета и не се пипа.
    if checkpoint_dir != report_dir:
        stale = checkpoint_dir / "progress.json"
        try:
            stale.unlink(missing_ok=True)
            if not any(checkpoint_dir.iterdir()):
                checkpoint_dir.rmdir()
        except OSError:
            # Неуспешното чистене не е повод да се провали готов отчет.
            pass

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(report_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_parent))

    return BatchGeometryReportResult(
        report_dir=report_dir,
        json_path=json_path,
        csv_path=csv_path,
        html_path=html_path,
        zip_path=zip_path,
        total_annotations=total_annotations,
        strong_count=counts["strong"],
        review_count=counts["review"],
        risk_count=counts["risk"],
        no_candidate_count=counts["none"],
        approved_count=counts["approved"],
        processed_annotations=processed_annotations,
        cancelled=cancelled,
        error_count=counts["error"],
        geometry_strong_count=counts["geometry_strong"],
        f006_strong_count=counts["f006_strong"],
        f006_form_strong_count=counts["f006_form_strong"],
        f006_production_exact_count=counts["f006_production_exact"],
        f006_production_derived_count=counts["f006_production_derived"],
        f006_production_review_count=counts["f006_production_review"],
        f006_asymmetric_hook_count=counts["f006_asymmetric_hook"],
        f006_printed_dimensions_missing_count=counts["f006_printed_dimensions_missing"],
        f006_printed_dimensions_mismatch_count=counts["f006_printed_dimensions_mismatch"],
        context_count=counts["context"],
        actionable_count=counts["actionable"],
        candidate_count=counts["candidate"],
        conflict_count=counts["conflict"],
        master_ambiguity_count=counts["master_ambiguity"],
        ownership_conflict_count=counts["ownership_conflict"],
        review_group_count=group_counts["actionable_groups"],
        ownership_reassigned_count=counts["ownership_reassigned"],
        ownership_tiebreak_resolved_count=counts["ownership_tiebreak_resolved"],
        dimension_evidence_applied_count=counts["dimension_evidence_applied"],
        dimension_master_resolved_count=counts["dimension_master_resolved"],
        dimension_candidate_tiebreak_count=counts["dimension_candidate_tiebreak"],
        f007_extended_association_count=counts["f007_extended_association"],
        ownership_recovery_verified_count=counts["ownership_recovery_verified"],
        position_key_legend_resolved_count=counts[
            "position_key_legend_resolved"
        ],
        position_key_manual_override_count=counts[
            "position_key_manual_override"
        ],
        stirrup_length_group_resolved_count=counts[
            "stirrup_length_group_resolved"
        ],
        f001_extended_exact_length_count=counts[
            "f001_extended_exact_length"
        ],
        proxy_parent_provenance_count=counts["proxy_parent_provenance"],
        named_u_subtype_context_resolved_count=counts[
            "named_u_subtype_context_resolved"
        ],
    )


def _status_counts(rows: Sequence[dict]) -> dict[str, int]:
    actionable = sum(row.get("annotation_class") == "ACTIONABLE" for row in rows)
    candidate_statuses = {
        "ОДОБРЕНА ВРЪЗКА",
        "СИЛЕН",
        "СИЛЕН ПО ГЕОМЕТРИЯ",
        "СИЛНО СТРЕМЕ F006",
        "ЗА РЪЧЕН ПРЕГЛЕД",
        "РИСК",
    }
    master_ambiguity = sum(
        bool(row.get("master_conflict"))
        or bool(row.get("master_ambiguous_shape_codes"))
        for row in rows
    )
    ownership_conflict = sum(
        bool(row.get("global_conflict"))
        or bool(row.get("geometry_owner_conflict"))
        for row in rows
    )
    total_conflict = sum(
        bool(row.get("global_conflict"))
        or bool(row.get("geometry_owner_conflict"))
        or bool(row.get("master_conflict"))
        or bool(row.get("master_ambiguous_shape_codes"))
        for row in rows
    )
    return {
        "strong": sum(row.get("batch_status") == "СИЛЕН" for row in rows),
        "geometry_strong": sum(
            row.get("batch_status") == "СИЛЕН ПО ГЕОМЕТРИЯ" for row in rows
        ),
        "f006_strong": sum(
            row.get("batch_status") == "СИЛНО СТРЕМЕ F006" for row in rows
        ),
        "f006_form_strong": sum(
            str(row.get("master_shape_code") or "").strip().upper() == "F006"
            and row.get("batch_status")
            in ("СИЛНО СТРЕМЕ F006", "СИЛЕН", "СИЛЕН ПО ГЕОМЕТРИЯ", "ОДОБРЕНА ВРЪЗКА")
            for row in rows
        ),
        "f006_production_exact": sum(
            row.get("f006_production_status") == "F006_PRODUCTION_EXACT" for row in rows
        ),
        "f006_production_derived": sum(
            row.get("f006_production_status") == "F006_PRODUCTION_DERIVED"
            for row in rows
        ),
        "f006_production_review": sum(
            row.get("f006_production_status") == "F006_PRODUCTION_REVIEW"
            for row in rows
        ),
        "f006_asymmetric_hook": sum(
            bool(row.get("f006_asymmetric_hooks")) for row in rows
        ),
        "f006_printed_dimensions_missing": sum(
            bool(row.get("f006_printed_dimensions_missing")) for row in rows
        ),
        "f006_printed_dimensions_mismatch": sum(
            bool(row.get("f006_printed_dimensions_mismatch")) for row in rows
        ),
        "family_inferred": sum(bool(row.get("inferred_family_code")) for row in rows),
        "review": sum(row.get("batch_status") == "ЗА РЪЧЕН ПРЕГЛЕД" for row in rows),
        "risk": sum(row.get("batch_status") == "РИСК" for row in rows),
        "none": sum(
            row.get("batch_status") == "НЯМА КАНДИДАТ"
            and row.get("annotation_class") == "ACTIONABLE"
            for row in rows
        ),
        "approved": sum(row.get("batch_status") == "ОДОБРЕНА ВРЪЗКА" for row in rows),
        "context": sum(row.get("annotation_class") != "ACTIONABLE" for row in rows),
        "actionable": actionable,
        "candidate": sum(
            row.get("annotation_class") == "ACTIONABLE"
            and row.get("batch_status") in candidate_statuses
            for row in rows
        ),
        "master_ambiguity": master_ambiguity,
        "ownership_conflict": ownership_conflict,
        "ownership_reassigned": sum(
            bool(row.get("ownership_reassigned")) for row in rows
        ),
        "ownership_tiebreak_resolved": sum(
            bool(row.get("ownership_tiebreak_resolved")) for row in rows
        ),
        "dimension_evidence_applied": sum(
            bool(row.get("dimension_evidence_applied")) for row in rows
        ),
        "dimension_master_resolved": sum(
            bool(row.get("dimension_master_resolved")) for row in rows
        ),
        "dimension_candidate_tiebreak": sum(
            "DIMENSION_CANDIDATE_TIEBREAK"
            in set(str(value) for value in row.get("review_reason_codes", []))
            for row in rows
        ),
        "f007_extended_association": sum(
            "F007_EXACT_EXTENDED_ASSOCIATION"
            in set(str(value) for value in row.get("review_reason_codes", []))
            for row in rows
        ),
        "ownership_recovery_verified": sum(
            bool(row.get("ownership_recovery_verified")) for row in rows
        ),
        "position_key_legend_resolved": sum(
            bool(row.get("position_key_resolved")) for row in rows
        ),
        "position_key_manual_override": sum(
            bool(row.get("position_key_manual_override")) for row in rows
        ),
        "stirrup_length_group_resolved": sum(
            bool(row.get("stirrup_length_group_resolved")) for row in rows
        ),
        "f001_extended_exact_length": sum(
            "F001_EXTENDED_EXACT_LENGTH"
            in set(str(value) for value in row.get("review_reason_codes", []))
            for row in rows
        ),
        "proxy_parent_provenance": sum(
            bool(row.get("proxy_parent_provenance")) for row in rows
        ),
        "named_u_subtype_context_resolved": sum(
            bool(row.get("named_u_subtype_context_resolved")) for row in rows
        ),
        "conflict": total_conflict,
        "error": sum(row.get("batch_status") == "ГРЕШКА" for row in rows),
    }


def _apply_proxy_annotation_provenance(
    row: dict[str, Any],
    annotation: DxfEntityRecord,
) -> None:
    """Attach proxy source identity and make proven geometry reference-only.

    Some GA details intentionally reuse one schematic proxy geometry for more
    than one production annotation.  These are references, not claims that a
    physical DXF object can have only one owner, so the ordinary ownership
    conflict resolver must not reinterpret that structural evidence.
    """

    if not is_proxy_rebar_annotation(annotation):
        return
    expected = tuple(sorted(proxy_geometry_handles(annotation)))
    selected = tuple(sorted(str(value) for value in row.get("candidate_handles", [])))
    proven = bool(expected and selected == expected)
    raw = annotation.raw
    row.update(
        {
            "proxy_parent_provenance": proven,
            "proxy_association_code": str(
                raw.get("association_code") or PROXY_ASSOCIATION_CODE
            ),
            "proxy_annotation_parent_handle": str(
                raw.get("parent_proxy_handle") or ""
            ),
            "proxy_geometry_parent_handle": str(
                raw.get("proxy_geometry_parent_handle") or ""
            ),
            "proxy_geometry_handles": list(expected),
            "proxy_position_key": raw.get("position_key"),
            "proxy_spacing_cm": raw.get("spacing_cm"),
            "proxy_scale_factor": raw.get("scale_factor"),
            "proxy_exact_total_cm": raw.get("exact_total_cm"),
            "proxy_predecessor_distance": raw.get("predecessor_distance"),
            "proxy_modelspace_distance": raw.get("modelspace_distance"),
        }
    )
    if proven:
        row["ownership_reference_only"] = True


def _apply_matcher_decisions(
    rows: Sequence[dict],
    *,
    shape_definitions: Sequence[ShapeDefinition],
    policy: RecognitionPolicy,
) -> None:
    """Attach a pure, explainable recommendation after global conflict checks.

    This function never approves a link.  Without a released real-world
    calibration even a clean, unique match remains ``CONTROL``.  Existing
    project approvals are kept as such and are not reclassified by incomplete
    historical report evidence.
    """

    approved_master_codes = tuple(
        shape.code
        for shape in shape_definitions
        if shape.active
        and str(shape.state).strip().upper() in {"APPROVED", "LOCKED"}
    )
    for row in rows:
        if row.get("batch_status") == "ОДОБРЕНА ВРЪЗКА":
            row["matcher_disposition"] = "APPROVED_IN_PROJECT"
            row["matcher_evidence_band"] = "APPROVED"
            row["form_classification_confidence"] = "APPROVED"
            row["geometry_ownership_confidence"] = "APPROVED"
            row["form_classification_evidence"] = []
            row["geometry_ownership_evidence"] = []
            row["matcher_blocker_codes"] = []
            row["matcher_decision_summary"] = (
                "Връзката вече е одобрена в този проект; matcher-ът не я променя."
            )
            row["matcher_decision"] = {
                "disposition": "APPROVED_IN_PROJECT",
                "evidence_band": "APPROVED",
                "form_classification_confidence": "APPROVED",
                "geometry_ownership_confidence": "APPROVED",
                "summary": row["matcher_decision_summary"],
                "safety_gates": [],
                "evidence": [],
                "form_classification_evidence": [],
                "geometry_ownership_evidence": [],
            }
            continue

        decision = evaluate_report_row(
            row,
            approved_master_codes=approved_master_codes,
            policy=policy,
            calibration=None,
        )
        payload = decision.to_dict()
        row["matcher_decision"] = payload
        row["matcher_disposition"] = decision.disposition.value
        row["matcher_evidence_band"] = decision.evidence_band.value
        row["form_classification_confidence"] = (
            decision.form_classification_confidence.value
        )
        row["geometry_ownership_confidence"] = (
            decision.geometry_ownership_confidence.value
        )
        row["form_classification_evidence"] = payload[
            "form_classification_evidence"
        ]
        row["geometry_ownership_evidence"] = payload[
            "geometry_ownership_evidence"
        ]
        row["matcher_blocker_codes"] = [
            gate.code for gate in decision.safety_gates
        ]
        row["matcher_decision_summary"] = decision.summary


# Слаб локален кандидат, който доказаният ключ от легендата може да замени.
# „РИСК“ е по-слаб от „ЗА РЪЧЕН ПРЕГЛЕД“: до 17.09.2026 оставаше извън пасa и
# табличните 12N18x373 и 24N8x373 в s-11 (ключове 4e и 4g) се сблъскваха в
# чужд U-прът от 301 cm. Собственик: „защо е риск — има си позиция с кръгче,
# така че легендата се гледа“. Силните кандидати остават извън обхвата.
_WEAK_LOCAL_CANDIDATE_STATUSES = frozenset({"ЗА РЪЧЕН ПРЕГЛЕД", "РИСК"})


def _apply_position_key_legend_references(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
    entities: Sequence[DxfEntityRecord],
    context: GeometryCandidateContext,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
    shape_index: EngineShapeIndex,
) -> None:
    """Recover eligible rows linked to a proven remote legend.

    NO_CANDIDATE is the historical scope.  Engine policy may additionally admit
    rows with a weak local candidate — MANUAL_REVIEW and RISK — because a
    complete key/Ø/L/legend/MASTER chain is stronger evidence than a weak
    local proximity candidate.  Repeated table rows may legitimately point to
    the same legend template, so those handles are audit provenance, not local
    ownership claims.
    """

    eligible = [
        row
        for row in rows
        if row.get("annotation_class") == "ACTIONABLE"
        and (
            row.get("batch_status") == "НЯМА КАНДИДАТ"
            or (
                policy.position_key_manual_review_override_enabled
                and row.get("batch_status") in _WEAK_LOCAL_CANDIDATE_STATUSES
            )
        )
    ]
    if not eligible or not policy.position_key_legend_rule_enabled:
        return

    legend_index = build_position_key_legend_index(
        entities,
        shape_definitions=shape_definitions,
        control_tests=control_tests,
        policy=policy,
        context=context,
        shape_index=shape_index,
    )
    for row in eligible:
        annotation_handle = str(row.get("annotation_handle") or "")
        annotation = annotations_by_handle.get(annotation_handle)
        if annotation is None:
            continue
        resolution = resolve_position_key_annotation(
            annotation,
            legend_index,
            policy=policy,
        )
        if resolution is None:
            # Ключ на реда без съгласна легенда: позицията остава нерешена,
            # но е таблична — правилото за стремената в рамката не я взима.
            row_key = table_row_position_key(annotation, legend_index, policy=policy)
            if row_key:
                row["position_key_row_key"] = row_key
            continue

        candidate = resolution.candidate
        произход = (
            " Двойката ключ/спецификация е последната свободна в затворено "
            "множество с равен брой ключове и спецификации, а не намерена в "
            "радиуса на легендата."
            if resolution.legend.closed_set_residual
            else ""
        )
        note = (
            f"ПОЗИЦИОНЕН КЛЮЧ {resolution.key}: таблично кръгче "
            f"{resolution.table_circle_handle} на "
            f"{resolution.table_circle_distance_raw:.3f} единици от ARM блока "
            f"сочи към единственото легендно кръгче "
            f"{resolution.legend.legend_circle_handle}, спецификация "
            f"{resolution.legend.legend_spec_handle} "
            f"({resolution.legend.legend_spec_text}) и еднозначна MASTER форма "
            f"{candidate.master_shape_code}. Геометрията "
            f"{', '.join(candidate.handles)} е легендна референция, а не "
            "локална собственост на табличния надпис."
            f"{произход}"
        )
        reference_analysis = GeometryCandidateAnalysis(
            annotation_handle=annotation_handle,
            label=str(annotation.text or row.get("annotation_text") or ""),
            diameter_mm=float(row.get("diameter_mm")),
            declared_length_cm=float(row.get("declared_length_cm")),
            candidates=(candidate,),
            status="РАЗРЕШЕНО ЧРЕЗ ПОЗИЦИОНЕН КЛЮЧ И ЛЕГЕНДА",
            warnings=(*resolution.legend.analysis.warnings, note),
            rejection_counts=dict(resolution.legend.analysis.rejection_counts),
            policy=dict(resolution.legend.analysis.policy),
        )
        original_status = str(row.get("batch_status") or "")
        original_rejection_counts = dict(row.get("rejection_counts", {}))
        original_candidate = (
            _candidate_snapshot_from_row(row)
            if row.get("candidate_handles")
            else None
        )
        original_laws = list(row.get("recognition_laws", []))
        resolved = _analysis_row(reference_analysis, None)
        row.update(resolved)

        reasons = [str(value) for value in row.get("review_reason_codes", [])]
        if resolution.code not in reasons:
            reasons.append(resolution.code)
        notes = list(row.get("candidate_notes", []))
        if note not in notes:
            notes.append(note)
        warnings = list(row.get("analysis_warnings", []))
        if note not in warnings:
            warnings.append(note)
        row.update(
            {
                "candidate_basis": (
                    f"{candidate.basis}; {resolution.code}"
                    if candidate.basis
                    else resolution.code
                ),
                "candidate_notes": notes,
                "analysis_warnings": warnings,
                "recognition_laws": original_laws,
                "review_reason_codes": reasons,
                "global_conflict": False,
                "geometry_owner_conflict": False,
                "ownership_reference_only": True,
                "ownership_rejected": False,
                "ownership_reassigned": False,
                "ownership_recovery_verified": False,
                "ownership_winner_annotations": [],
                "position_key_resolved": True,
                "position_key_manual_override": (
                    original_status in _WEAK_LOCAL_CANDIDATE_STATUSES
                ),
                "position_key_resolution_code": resolution.code,
                "position_key_code": resolution.key,
                "position_key_table_circle_handle": resolution.table_circle_handle,
                "position_key_table_distance_raw": resolution.table_circle_distance_raw,
                "position_key_legend_circle_handle": (
                    resolution.legend.legend_circle_handle
                ),
                "position_key_legend_spec_handle": (
                    resolution.legend.legend_spec_handle
                ),
                "position_key_legend_spec_text": (
                    resolution.legend.legend_spec_text
                ),
                "position_key_occurrence_count": resolution.legend.occurrence_count,
                "position_key_legend_geometry_handles": list(candidate.handles),
                "position_key_legend_geometry_distance_raw": candidate.distance_raw,
                "position_key_original_batch_status": original_status,
                "position_key_original_rejection_counts": (
                    original_rejection_counts
                ),
                "position_key_original_candidate": original_candidate,
                "position_key_legend_reference": True,
            }
        )
        row["svg_preview"] = _svg_preview(
            resolution.legend.probe_annotation,
            resolution.legend.analysis,
            context=context,
            selected_handles=tuple(candidate.handles),
        )


def _apply_stirrup_length_group_references(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
    entities: Sequence[DxfEntityRecord],
    context: GeometryCandidateContext,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
    shape_index: EngineShapeIndex,
) -> None:
    """Recover only unresolved curved stirrups with exact arc-length proof.

    This runs after ordinary ownership allocation.  A group annotation may
    legitimately refer to several repeated stirrup symbols, so the chosen
    representative handle is preview provenance and not an exclusive physical
    ownership claim.
    """

    if not policy.stirrup_length_group_rule_enabled:
        return
    unresolved = [
        row
        for row in rows
        if row.get("annotation_class") == "ACTIONABLE"
        and row.get("batch_status") == "НЯМА КАНДИДАТ"
    ]
    for row in unresolved:
        annotation_handle = str(row.get("annotation_handle") or "")
        annotation = annotations_by_handle.get(annotation_handle)
        if annotation is None:
            continue
        resolution = resolve_stirrup_length_group_annotation(
            annotation,
            entities,
            shape_definitions=shape_definitions,
            control_tests=control_tests,
            policy=policy,
            context=context,
            shape_index=shape_index,
        )
        if resolution is None:
            continue
        _install_stirrup_group_reference(
            row,
            annotation,
            resolution,
            context=context,
            status="РАЗРЕШЕНО ЧРЕЗ ТОЧНА ДЪГОВА L И ГРУПА СТРЕМЕНА",
        )


def _apply_operator_area_stirrup_references(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
    entities: Sequence[DxfEntityRecord],
    context: GeometryCandidateContext,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
    shape_index: EngineShapeIndex,
    operator_areas: Sequence[Any] = (),
) -> int:
    """Стремето в рамката на елемента, когато надписът е изнесен встрани.

    Собственик, 2026-09-16, за s-11: в групата B 38 позиции оставаха „НЯМА
    КАНДИДАТ“, макар стремето да е начертано вътре в рамката — по едно копие
    във всеки разрез. Избраното правило: една форма в рамката е доказателство,
    различни форми — отказ (виж resolve_stirrup_operator_area_annotation).

    Идва СЛЕД групата по точна дъгова L: доказаното по-строго правило остава
    първо, рамката решава само останалото. Рамката трябва да е една
    (area_for_point отказва при спор между заглавия).
    """

    if not operator_areas or not policy.stirrup_length_group_rule_enabled:
        return 0
    from lion_rebar.export.operator_areas import KIND_GROUP, area_for_point

    applied = 0
    for row in rows:
        if (
            row.get("annotation_class") != "ACTIONABLE"
            or row.get("batch_status") != "НЯМА КАНДИДАТ"
            # Позиция с кръгче на реда се описва от легендата. Противоречи ли
            # ѝ, решава операторът — не стреме със случайно подходяща L.
            or row.get("position_key_row_key")
        ):
            continue
        annotation_handle = str(row.get("annotation_handle") or "")
        annotation = annotations_by_handle.get(annotation_handle)
        if annotation is None:
            continue
        anchor = _entity_insert(annotation)
        if anchor is None:
            continue
        area = area_for_point(operator_areas, anchor[0], anchor[1], kind=KIND_GROUP)
        if area is None:
            continue
        resolution = resolve_stirrup_operator_area_annotation(
            annotation,
            entities,
            area_bounds=(area.x0, area.y0, area.x1, area.y1),
            shape_definitions=shape_definitions,
            control_tests=control_tests,
            policy=policy,
            context=context,
            shape_index=shape_index,
        )
        if resolution is None:
            continue
        _install_stirrup_group_reference(
            row,
            annotation,
            resolution,
            context=context,
            status="РАЗРЕШЕНО ЧРЕЗ РАМКА И ЕДНАКВИ СТРЕМЕНА",
            note_prefix=(
                f"Рамка „{area.title_text}“: всички {len(resolution.member_handles)} "
                "стремена с тази L в рамката са една и съща форма. "
            ),
        )
        row["stirrup_operator_area_id"] = area.area_id
        row["stirrup_operator_area_title"] = area.title_text
        applied += 1
    return applied


def _install_stirrup_group_reference(
    row: dict,
    annotation: DxfEntityRecord,
    resolution: Any,
    *,
    context: GeometryCandidateContext,
    status: str,
    note_prefix: str = "",
) -> None:
    annotation_handle = str(row.get("annotation_handle") or "")
    candidate = resolution.candidate
    member_text = ", ".join(
        f"{handle}:L={length:.3f}@{distance:.3f}"
        for handle, length, distance in zip(
            resolution.member_handles,
            resolution.exact_arc_lengths_cm,
            resolution.member_distances_raw,
        )
    )
    note = note_prefix + (
        f"{resolution.code}: декларирана L={resolution.declared_length_cm:.3f}, "
        f"брой={resolution.declared_count}; точната дъгова L избра "
        f"еднозначна MASTER форма {candidate.master_shape_code}. "
        f"Представител {resolution.representative_handle} на "
        f"{resolution.representative_distance_raw:.3f} единици. "
        f"Проверени членове [{member_text}]. Геометрията е групова "
        "референция и не се заявява като изключителна локална собственост."
    )
    reference_analysis = GeometryCandidateAnalysis(
        annotation_handle=annotation_handle,
        label=str(annotation.text or row.get("annotation_text") or ""),
        diameter_mm=float(row.get("diameter_mm")),
        declared_length_cm=float(row.get("declared_length_cm")),
        candidates=(candidate,),
        status=status,
        warnings=(*resolution.analysis.warnings, note),
        rejection_counts=dict(resolution.analysis.rejection_counts),
        policy=dict(resolution.analysis.policy),
    )
    original_rejection_counts = dict(row.get("rejection_counts", {}))
    original_laws = list(row.get("recognition_laws", []))
    resolved = _analysis_row(reference_analysis, None)
    row.update(resolved)

    reasons = [str(value) for value in row.get("review_reason_codes", [])]
    if resolution.code not in reasons:
        reasons.append(resolution.code)
    notes = list(row.get("candidate_notes", []))
    if note not in notes:
        notes.append(note)
    warnings = list(row.get("analysis_warnings", []))
    if note not in warnings:
        warnings.append(note)
    row.update(
        {
            "candidate_basis": (
                f"{candidate.basis}; {resolution.code}"
                if candidate.basis
                else resolution.code
            ),
            "candidate_notes": notes,
            "analysis_warnings": warnings,
            "recognition_laws": original_laws,
            "review_reason_codes": reasons,
            "global_conflict": False,
            "geometry_owner_conflict": False,
            "ownership_reference_only": True,
            "stirrup_length_group_resolved": True,
            "stirrup_length_group_resolution_code": resolution.code,
            "stirrup_length_group_representative_handle": (
                resolution.representative_handle
            ),
            "stirrup_length_group_representative_distance_raw": (
                resolution.representative_distance_raw
            ),
            "stirrup_length_group_member_handles": list(
                resolution.member_handles
            ),
            "stirrup_length_group_member_distances_raw": list(
                resolution.member_distances_raw
            ),
            "stirrup_length_group_exact_arc_lengths_cm": list(
                resolution.exact_arc_lengths_cm
            ),
            "stirrup_length_group_declared_count": (
                resolution.declared_count
            ),
            "stirrup_length_group_original_batch_status": "НЯМА КАНДИДАТ",
            "stirrup_length_group_original_rejection_counts": (
                original_rejection_counts
            ),
            "stirrup_length_group_reference": True,
        }
    )
    row["svg_preview"] = _svg_preview(
        resolution.probe_annotation,
        resolution.analysis,
        context=context,
        selected_handles=tuple(candidate.handles),
    )


def _apply_unanimous_family_consensus(rows: Sequence[dict]) -> None:
    """Infer only a MASTER family for repeated labels with missing geometry.

    A minimum of three distinct real geometry owners must agree on one
    unambiguous MASTER code for the exact same normalized annotation text.
    This never creates candidate handles, never changes NO_CANDIDATE and never
    becomes an approval.  It only avoids asking the operator to rediscover the
    same form family for repeated schematic labels whose local bar is absent or
    outside the association window.
    """

    evidence_by_label: dict[str, list[dict]] = {}
    for row in rows:
        label = str(row.get("annotation_text") or "").strip().upper()
        code = str(row.get("master_shape_code") or "").strip().upper()
        handles = tuple(
            sorted(str(value) for value in row.get("candidate_handles", []) if str(value))
        )
        if (
            label
            and code
            and handles
            and not bool(row.get("master_conflict"))
            and not row.get("master_ambiguous_shape_codes")
            and row.get("batch_status") != "НЯМА КАНДИДАТ"
        ):
            evidence_by_label.setdefault(label, []).append(row)

    for row in rows:
        if row.get("batch_status") != "НЯМА КАНДИДАТ":
            continue
        label = str(row.get("annotation_text") or "").strip().upper()
        peers = evidence_by_label.get(label, [])
        codes = {
            str(peer.get("master_shape_code") or "").strip().upper()
            for peer in peers
            if peer.get("master_shape_code")
        }
        owners = {
            tuple(
                sorted(
                    str(value)
                    for value in peer.get("candidate_handles", [])
                    if str(value)
                )
            )
            for peer in peers
        }
        owners.discard(())
        if len(codes) != 1 or len(owners) < 3:
            continue
        code = next(iter(codes))
        row["inferred_family_code"] = code
        row["inferred_family_source"] = "UNANIMOUS_EXACT_LABEL_PEERS"
        row["inferred_family_evidence_count"] = len(owners)
        note = (
            f"Формата {code} е изведена от {len(owners)} отделни реални "
            "геометрии със същия надпис. Локална геометрична връзка не е "
            "намерена и позицията остава в НЯМА КАНДИДАТ."
        )
        row["candidate_notes"] = [*list(row.get("candidate_notes", [])), note]
        row["analysis_warnings"] = [*list(row.get("analysis_warnings", [])), note]


def _apply_named_u_subtype_context(
    rows: Sequence[dict],
    *,
    shape_definitions: Sequence[ShapeDefinition],
    policy: RecognitionPolicy,
) -> None:
    """Resolve only explicitly named F003/F005 subtypes.

    The two forms share a valid three-segment U topology when the end arms are
    equal.  The batch may nevertheless contain a separate specification text
    such as ``фиби 32N8/s=20``.  That row has no cut length, but its count and
    diameter are usable semantic evidence.  It is applied only when all U rows
    with the same count/diameter have one canonical segment signature.  This
    prevents a generic word elsewhere in the drawing from relabelling several
    different bars.
    """

    if not policy.named_u_subtype_context_rule_enabled:
        return

    shape_map = {
        str(shape.code).strip().upper(): shape
        for shape in shape_definitions
    }
    if not {"F003", "F005"} <= set(shape_map):
        return

    contexts: dict[tuple[int, float], list[tuple[dict, str, str]]] = {}
    for row in rows:
        target = _named_u_subtype_code(str(row.get("annotation_text") or ""))
        if target is None:
            continue
        count = _positive_int(row.get("annotation_count"))
        diameter = _positive_float(row.get("diameter_mm"))
        if count is None or diameter is None:
            continue
        rule_code = (
            "EXPLICIT_FIBA_TEXT_F005"
            if target == "F005"
            else "EXPLICIT_OPEN_U_TEXT_F003"
        )
        contexts.setdefault((count, round(diameter, 4)), []).append(
            (row, target, rule_code)
        )

    for key, context_rows in contexts.items():
        target_codes = {target for _row, target, _rule in context_rows}
        if len(target_codes) != 1:
            for context_row, _target, _rule in context_rows:
                note = (
                    "Противоречиви именувани U-контексти имат еднакъв брой и Ø; "
                    "F003/F005 не е избрана автоматично."
                )
                context_row["candidate_notes"] = [
                    *list(context_row.get("candidate_notes", [])),
                    note,
                ]
            continue

        target = next(iter(target_codes))
        candidates = [
            row
            for row in rows
            if row.get("annotation_class") == "ACTIONABLE"
            and _positive_int(row.get("annotation_count")) == key[0]
            and (
                (diameter := _positive_float(row.get("diameter_mm"))) is not None
                and round(diameter, 4) == key[1]
            )
            and (
                str(row.get("master_shape_code") or "").strip().upper()
                in {"F003", "F005"}
                or {"F003", "F005"}.intersection(
                    str(value).strip().upper()
                    for value in row.get("master_ambiguous_shape_codes", [])
                )
            )
        ]
        signatures = {
            signature
            for row in candidates
            if (signature := _u_context_signature(row))
        }
        if not candidates or len(signatures) != 1:
            note = (
                f"Именуваният контекст за {target} не е приложен: брой {key[0]} "
                f"и Ø{key[1]:g} не водят до една-единствена U-сигнатура."
            )
            for context_row, _target, _rule in context_rows:
                context_row["candidate_notes"] = [
                    *list(context_row.get("candidate_notes", [])),
                    note,
                ]
            continue

        target_shape = shape_map[target]
        source_handles = [
            str(context_row.get("annotation_handle") or "")
            for context_row, _target, _rule in context_rows
        ]
        rule_code = context_rows[0][2]
        note = (
            f"{rule_code}: изричният текст ({', '.join(source_handles)}) с "
            f"брой {key[0]} и Ø{key[1]:g} избира {target}; геометрията сама "
            "не е използвана за разграничаване на F003/F005."
        )
        for row in candidates:
            was_ambiguous = bool(row.get("master_conflict")) or bool(
                {"F003", "F005"}.intersection(
                    str(value).strip().upper()
                    for value in row.get("master_ambiguous_shape_codes", [])
                )
            )
            row["master_shape_code"] = target
            row["master_shape_name"] = target_shape.name
            row["master_ambiguous_shape_codes"] = []
            row["master_conflict"] = False
            row["named_u_subtype_context_resolved"] = True
            row["named_u_subtype_context_handles"] = source_handles
            row["contextual_rule_code"] = rule_code
            row["contextual_rule_note"] = note
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                note,
            ]
            row["analysis_warnings"] = [
                *list(row.get("analysis_warnings", [])),
                note,
            ]
            row["review_reason_codes"] = [
                str(value)
                for value in row.get("review_reason_codes", [])
                if str(value).strip().upper()
                not in {"AMBIGUOUS_MASTER", "MASTER_AMBIGUITY"}
            ]

            # Promote only when the MASTER ambiguity was the sole reason for
            # review.  Ownership, data, mirror and gap gates remain untouched.
            if (
                was_ambiguous
                and row.get("batch_status") == "ЗА РЪЧЕН ПРЕГЛЕД"
                and not row.get("global_conflict")
                and not row.get("geometry_owner_conflict")
                and not row.get("near_gap_healed")
                and not row.get("mirror_requires_review")
                and not row.get("quantity_review_required")
                and not row.get("data_quality_flags")
                and not row.get("review_reason_codes")
            ):
                row["batch_status"] = "СИЛЕН ПО ГЕОМЕТРИЯ"
                row["candidate_confidence"] = (
                    "СИЛЕН ПО ГЕОМЕТРИЯ — ИЗРИЧЕН ИМЕНУВАН КОНТЕКСТ"
                )

        for context_row, _target, _rule in context_rows:
            context_row["named_u_subtype_context_target"] = target
            context_row["named_u_subtype_context_matches"] = [
                str(row.get("annotation_handle") or "") for row in candidates
            ]
            context_row["candidate_notes"] = [
                *list(context_row.get("candidate_notes", [])),
                note,
            ]


def _derive_f006_dimensions_from_candidate(
    row: Mapping[str, Any],
    *,
    entity_map: Mapping[str, DxfEntityRecord],
) -> dict | None:
    """Извлича реалните шест F006 размера от геометрията на кандидата.

    Никога не измисля стойности: използва само канонизираните сегментни
    дължини, които matcher-ът вече е измерил от DXF обектите. Връща None,
    когато сегментите не образуват еднозначен шестсегментен контур —
    тогава позицията остава F006_PRODUCTION_REVIEW.
    """

    lengths = [
        float(value)
        for value in row.get("canonical_segment_lengths_cm", [])
        if _positive_float(value) is not None
    ]
    if len(lengths) != 6:
        return None
    hooks = (lengths[0], lengths[-1])
    sides = lengths[1:-1]
    if any(value <= 0 for value in lengths):
        return None
    handles = ",".join(str(v) for v in row.get("candidate_handles", []) if str(v))
    provenance = (
        "DXF candidate handles "
        f"[{handles}]; canonical_segment_lengths_cm измерени от matcher-а; "
        f"куки {hooks[0]:g}/{hooks[1]:g} cm запазени асиметрични"
    )
    return {"segments": tuple(lengths), "provenance": provenance}


def _single_open_lwpolyline_topology(
    row: Mapping[str, Any],
    *,
    entity_map: Mapping[str, DxfEntityRecord],
) -> dict[str, Any] | None:
    """Derive F003/F006 production sizes from one exact curved centreline.

    The row gates stay here: VERTICALS frame, no conflict or ambiguity and one
    owned candidate.  The geometric proof itself lives in
    ``curved_centreline_proof`` so the operator-area stirrup rule can use the
    same proof for the copies inside a frame.
    """

    from lion_rebar.dxf.curved_centreline_proof import (
        curved_centreline_production_proof,
    )

    shape_code = str(row.get("master_shape_code") or "").strip().upper()
    if (
        str(row.get("frame_category_code") or "").strip().upper()
        != "VERTICALS"
        or shape_code not in {"F003", "F006"}
        or bool(row.get("global_conflict"))
        or bool(row.get("geometry_owner_conflict"))
        or bool(row.get("ownership_conflict"))
        or bool(row.get("ownership_rejected"))
        or bool(row.get("near_gap_healed"))
        or bool(row.get("master_ambiguous_shape_codes"))
    ):
        return None
    handles = tuple(
        str(value) for value in (row.get("candidate_handles") or ()) if str(value)
    )
    if len(handles) != 1:
        return None
    declared = _positive_float(
        row.get("production_length_cm")
        if row.get("production_length_cm") is not None
        else row.get("declared_length_cm")
    )
    if declared is None:
        return None
    return curved_centreline_production_proof(
        entity_map.get(handles[0]),
        shape_code,
        declared,
    )


def _apply_single_open_lwpolyline_production_segments(
    rows: Sequence[dict],
    entities: Sequence[DxfEntityRecord],
) -> int:
    entity_map = {str(entity.handle): entity for entity in entities}
    promoted = 0
    for row in rows:
        if row.get("production_segment_evidence"):
            continue
        derived = _single_open_lwpolyline_topology(row, entity_map=entity_map)
        if derived is None:
            continue
        canonical = tuple(float(value) for value in derived["segments"])
        row["canonical_segment_lengths_cm"] = list(canonical)
        row["canonical_turn_angles_deg"] = [90.0] * (len(canonical) - 1)
        if str(row.get("master_shape_code") or "").strip().upper() == "F006":
            row["f006_production_status"] = "F006_PRODUCTION_DERIVED"
            row["f006_production_reason"] = "SINGLE_OPEN_LWPOLYLINE_TOPOLOGY"
            row["f006_printed_dimensions_resolved"] = False
        if _install_authoritative_production_segments(
            row,
            canonical,
            source="SINGLE_OPEN_LWPOLYLINE_TOPOLOGY",
            evidence_details=derived["evidence"],
        ):
            if (
                str(row.get("master_shape_code") or "").strip().upper() == "F006"
                and row.get("production_export_blocked") is True
                and str(row.get("production_export_block_reason") or "").strip().upper()
                == "F006_PRINTED_DIMENSIONS_MISSING"
            ):
                # The earlier printed-dimension pass can only report that its
                # own source is missing.  This exact topology is a stronger,
                # self-contained production proof, so that one soft block is
                # superseded.  Mismatch, conflict and ownership blocks remain.
                row["production_export_blocked"] = False
                row["production_export_block_reason"] = ""
            promoted += 1
    return promoted


def _nearby_dimension_values(point, dimensions, limit: int = 10) -> list[float]:
    """Котировките около позицията, подредени по близост.

    Служат на оператора като доказателство, когато автоматичното извеждане
    се е отказало.
    """

    import math

    from lion_rebar.dxf.vertical_shape_resolver import DIMENSION_RADIUS, snap

    near: list[tuple[float, float]] = []
    for item in dimensions:
        measurement = item.get("measurement")
        position = item.get("point")
        if not measurement or not position:
            continue
        distance = math.hypot(position[0] - point[0], position[1] - point[1])
        if distance <= DIMENSION_RADIUS:
            near.append((distance, snap(float(measurement))))
    near.sort()
    values: list[float] = []
    for _distance, value in near:
        if value >= 4.0 and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def _apply_proven_f007_canonical_segments(
    row: dict,
    declared_length_cm: float,
    *,
    owned_dimension_records: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> bool:
    """Promote F007 canonical sizes without consulting unowned nearby dims."""

    from lion_rebar.engine.bend_allowance import segments_match_total

    if str(row.get("master_shape_code") or "").strip().upper() != "F007":
        return False
    raw_canonical = row.get("canonical_segment_lengths_cm")
    # Never filter invalid entries into a seemingly valid triplet.  The raw
    # matcher vector itself must contain exactly three finite positive values.
    if not isinstance(raw_canonical, (list, tuple)) or len(raw_canonical) != 3:
        return False
    try:
        canonical = tuple(float(value) for value in raw_canonical)
    except (TypeError, ValueError):
        return False
    if any(not math.isfinite(value) or value <= 0 for value in canonical):
        return False

    strict_evidence = _strict_f007_owned_dimension_evidence(
        row,
        canonical,
        owned_dimension_records or {},
    )
    contextual_master = (
        str(row.get("contextual_rule_code") or "").strip().upper()
        == "F007_ESOVE_CURVED_CENTERLINE"
    )
    canonical_is_valid = (
        len(canonical) == 3
        and abs(canonical[0] - canonical[2]) <= 0.11
        and segments_match_total(
            "F007",
            sum(canonical),
            declared_length_cm,
            tolerance=0.11,
        )
    )
    if not canonical_is_valid or not (
        strict_evidence is not None or contextual_master
    ):
        return False

    source = (
        "STRICT_LOCAL_DIMENSIONS"
        if strict_evidence is not None
        else "F007_CONTEXTUAL_MASTER_CANONICAL"
    )
    row["production_segments_cm"] = list(canonical)
    row["canonical_turn_angles_deg"] = [90.0, 90.0]
    row["production_segment_evidence"] = {
        "shape_code": "F007",
        "segments": list(canonical),
        "turn_angles_deg": [90.0, 90.0],
        "source": source,
        "confidence": "HIGH",
        "contextual_rule_code": (
            "F007_ESOVE_CURVED_CENTERLINE"
            if source == "F007_CONTEXTUAL_MASTER_CANONICAL"
            else ""
        ),
        **(strict_evidence or {}),
    }
    row["candidate_notes"] = [
        *list(row.get("candidate_notes", [])),
        (
            "F007_PRODUCTION_FROM_CANONICAL_MASTER: "
            f"{canonical[0]:g} + {canonical[1]:g} + "
            f"{canonical[2]:g} cm; nearby коти не заменят "
            "притежаваното MASTER доказателство"
        ),
    ]
    return True


def _strict_f007_owned_dimension_evidence(
    row: Mapping[str, Any],
    canonical: tuple[float, float, float],
    owned_dimension_records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    """Bind every F007 segment to a claimed, contour-owned DIMENSION."""

    if row.get("dimension_evidence_applied") is not True:
        return None
    claimed_handles = [
        str(value)
        for value in (row.get("dimension_text_handles") or [])
        if str(value)
    ]
    if not claimed_handles or len(set(claimed_handles)) != len(claimed_handles):
        return None
    candidate_handles = [
        str(value)
        for value in (row.get("candidate_handles") or [])
        if str(value)
    ]
    if not candidate_handles:
        return None

    records: dict[str, tuple[float, str]] = {}
    for owner in candidate_handles:
        for record in owned_dimension_records.get(owner, ()):
            handle = str(record.get("handle") or "")
            if handle not in claimed_handles or handle in records:
                continue
            measurement = _positive_float(record.get("measurement_cm"))
            if measurement is not None and math.isfinite(measurement):
                records[handle] = (measurement, owner)
    if set(records) != set(claimed_handles):
        return None

    bindings: list[dict[str, Any]] = []
    used_handles: set[str] = set()
    for index, segment in enumerate(canonical, start=1):
        matched = next(
            (
                (handle, measurement)
                for handle, (measurement, _owner) in records.items()
                if abs(measurement - segment) <= 0.11
            ),
            None,
        )
        if matched is None:
            return None
        handle, measurement = matched
        used_handles.add(handle)
        bindings.append(
            {
                "segment_index": index,
                "dimension_handle": handle,
                "measurement_cm": measurement,
            }
        )
    if used_handles != set(claimed_handles):
        return None
    return {
        "owner_geometry_handles": sorted(
            {records[handle][1] for handle in claimed_handles}
        ),
        "dimension_handles": claimed_handles,
        "dimension_measurements_cm": [
            {
                "handle": handle,
                "measurement_cm": records[handle][0],
            }
            for handle in claimed_handles
        ],
        "segment_bindings": bindings,
    }


def _owned_dimension_records_by_contour(
    entities: Sequence[DxfEntityRecord],
    ownership_resolver: Callable[[Sequence[DxfEntityRecord]], Mapping[str, Sequence[float]]],
) -> dict[str, list[dict[str, Any]]]:
    """Return owned DIMENSION handles and values without proximity guessing."""

    contours = [
        entity for entity in entities if entity.entity_type == "LWPOLYLINE"
    ]
    result: dict[str, list[dict[str, Any]]] = {}
    for dimension in entities:
        if dimension.entity_type != "DIMENSION" or not str(dimension.handle):
            continue
        measurement = _positive_float(dimension.geometry.get("measurement"))
        if measurement is None or not math.isfinite(measurement):
            continue
        ownership = ownership_resolver([*contours, dimension])
        for owner, values in ownership.items():
            if not values:
                continue
            result.setdefault(str(owner), []).append(
                {
                    "handle": str(dimension.handle),
                    "measurement_cm": float(values[0]),
                }
            )
    return result


def _apply_contextual_f007_without_dimensions(
    rows: Sequence[dict],
    owned_dimension_records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> int:
    """Apply complete contextual F007 proofs when the DXF has no DIMENSION."""

    resolved = 0
    for row in rows:
        if row.get("production_segments_cm"):
            continue
        declared = _positive_float(row.get("declared_length_cm"))
        if declared is not None and _apply_proven_f007_canonical_segments(
            row,
            declared,
            owned_dimension_records=owned_dimension_records,
        ):
            resolved += 1
    return resolved


_MATCHER_PRODUCTION_DIMENSION_STRATEGIES = frozenset(
    {
        "A_E_DIRECT_SEGMENTS",
        "A_E_MASTER_PARAMETER_FORMULA",
        "A_E_TOTAL_MINUS_PRINTED",
        "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS",
    }
)
_NUMERIC_PRODUCTION_EVIDENCE_RE = re.compile(
    r"\s*(\d+(?:[.,]\d+)?)\s*(?:CM)?\s*",
    flags=re.IGNORECASE,
)


def _finite_positive_segments(raw: Any) -> tuple[float, ...] | None:
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    values: list[float] = []
    for item in raw:
        if isinstance(item, bool):
            return None
        try:
            value = float(item)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value <= 0.0:
            return None
        values.append(value)
    return tuple(values)


def _segments_equivalent(
    left: Sequence[float],
    right: Sequence[float],
    *,
    tolerance: float = 0.11,
) -> bool:
    if len(left) != len(right):
        return False
    direct = all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))
    reverse = all(
        abs(float(a) - float(b)) <= tolerance
        for a, b in zip(left, reversed(right))
    )
    return direct or reverse


def _numeric_evidence_records(
    handles: Sequence[str],
    *,
    entity_map: Mapping[str, DxfEntityRecord],
) -> list[dict[str, Any]] | None:
    from lion_rebar.dxf.vertical_shape_resolver import snap

    if not handles or len(set(handles)) != len(handles):
        return None
    records: list[dict[str, Any]] = []
    for handle in handles:
        entity = entity_map.get(str(handle))
        if entity is None or entity.entity_type.upper() not in {
            "TEXT",
            "MTEXT",
            "DIMENSION",
        }:
            return None
        if entity.entity_type.upper() == "DIMENSION":
            raw_value = (entity.geometry or {}).get("measurement")
            if isinstance(raw_value, bool):
                return None
            try:
                # DXF stores the measured geometry (often 34.9897) separately
                # from the optional display override.  The resolver and the
                # canonical production vector use the snapped production
                # value, so persist that same authoritative value here.
                value = snap(float(raw_value))
            except (TypeError, ValueError):
                return None
        else:
            match = _NUMERIC_PRODUCTION_EVIDENCE_RE.fullmatch(
                str(entity.text or "")
            )
            if match is None:
                return None
            value = float(match.group(1).replace(",", "."))
        if not math.isfinite(value) or value <= 0.0:
            return None
        records.append({"handle": str(handle), "measurement_cm": value})
    return records


def _block_authoritative_segment_evidence(row: dict, reason: str) -> None:
    """Fail closed when two production proofs disagree or one is malformed."""

    row.pop("production_segments_cm", None)
    row.pop("production_segment_evidence", None)
    row["production_export_blocked"] = True
    row["production_export_block_reason"] = reason
    if str(row.get("master_shape_code") or "").strip().upper() == "F006":
        row["f006_production_status"] = "F006_PRODUCTION_REVIEW"
        row["f006_production_reason"] = reason
    row["review_reason_codes"] = sorted(
        {
            *[str(value) for value in (row.get("review_reason_codes") or [])],
            reason,
        }
    )
    row["candidate_notes"] = [
        *list(row.get("candidate_notes", [])),
        (
            f"{reason}: авторитетните производствени доказателства са "
            "невалидни или си противоречат; radius fallback е забранен."
        ),
    ]


def _install_authoritative_production_segments(
    row: dict,
    canonical: tuple[float, ...],
    *,
    source: str,
    evidence_details: Mapping[str, Any],
) -> bool:
    shape_code = str(row.get("master_shape_code") or "").strip().upper()
    declared = _positive_float(
        row.get("production_length_cm")
        if row.get("production_length_cm") is not None
        else row.get("declared_length_cm")
    )
    if declared is None:
        _block_authoritative_segment_evidence(
            row, "AUTHORITATIVE_DIMENSION_TOTAL_INVALID"
        )
        return False
    raw_turns = row.get("canonical_turn_angles_deg")
    try:
        canonical_turns = tuple(float(value) for value in (raw_turns or ()))
    except (TypeError, ValueError):
        canonical_turns = ()
    if any(not math.isfinite(value) for value in canonical_turns):
        _block_authoritative_segment_evidence(
            row, "AUTHORITATIVE_DIMENSION_SHAPE_INVALID"
        )
        return False
    try:
        parameters_from_segments(
            shape_code,
            canonical,
            production_length_cm=declared,
            turn_angles_deg=canonical_turns,
        )
    except (MasterDimensionError, TypeError, ValueError):
        _block_authoritative_segment_evidence(
            row, "AUTHORITATIVE_DIMENSION_SHAPE_INVALID"
        )
        return False

    existing = row.get("production_segments_cm")
    if existing not in (None, []):
        existing_segments = _finite_positive_segments(existing)
        if (
            existing_segments is None
            or not _segments_equivalent(existing_segments, canonical)
        ):
            _block_authoritative_segment_evidence(
                row, "CONFLICTING_PRODUCTION_SEGMENT_EVIDENCE"
            )
            return False
    existing_evidence = row.get("production_segment_evidence")
    if isinstance(existing_evidence, Mapping):
        existing_evidence_segments = _finite_positive_segments(
            existing_evidence.get("segments")
        )
        if (
            existing_evidence_segments is None
            or not _segments_equivalent(existing_evidence_segments, canonical)
        ):
            _block_authoritative_segment_evidence(
                row, "CONFLICTING_PRODUCTION_SEGMENT_EVIDENCE"
            )
            return False

    row["production_segments_cm"] = list(canonical)
    row["production_segment_evidence"] = {
        "shape_code": shape_code,
        "segments": list(canonical),
        "turn_angles_deg": list(canonical_turns),
        "source": source,
        "confidence": "HIGH",
        "candidate_handles": [
            str(value)
            for value in (row.get("candidate_handles") or [])
            if str(value)
        ],
        **dict(evidence_details),
    }
    row["candidate_notes"] = [
        *list(row.get("candidate_notes", [])),
        (
            "PRODUCTION_FROM_AUTHORITATIVE_CANONICAL: "
            f"{' + '.join(f'{value:g}' for value in canonical)} cm; "
            f"source={source}; generic radius fallback не се изпълнява"
        ),
    ]
    return True


#: Начертаното отместване на редукцията се закръгля до цял сантиметър само
#: когато разликата е чертожен шум. Реалният случай (собственик, 2026-09-25,
#: „т-1"): начертани 4.0061 cm при надписана редукция 40 — 0.06 mm разлика, а
#: проектантът е чертал 4. По-голяма разлика е ДРУГО число и се пази каквото е.
_F002_OFFSET_ROUNDING_TOLERANCE_CM = 0.1

#: Изведеният ъгъл трябва да описва СЪЩИЯ завой, който е начертан. Разминаване
#: над този праг значи, че надписът и чертежът не говорят за един и същ прът,
#: и тогава нищо не се извежда.
_F002_TURN_AGREEMENT_TOLERANCE_DEG = 1.0


def _f002_reduction_turn_angles_deg(
    row: Mapping[str, Any],
    canonical: Sequence[float],
    *,
    entity_map: Mapping[str, DxfEntityRecord],
) -> tuple[float, float] | None:
    """Двата завоя на редукцията, изведени от НАЧЕРТАНОТО отместване.

    Редукцията завива ВЪРХУ наклона и после обратно ОТ него: двата завоя са
    противоположни по знак и равни по големина (``excel_export.
    _validate_f002_turn_consistency`` иска точно това). Наум сложени 90/90 не
    са формален проблем — заключеният MASTER смята ``DY2 = редукция *
    sin(ъгъл)`` и при 90° обявява отместване, равно на цялата редукция, тоест
    Z вместо редукция.

    Големината идва от двете числа, които са точни: перпендикулярното
    разстояние между двете успоредни рамена и НАДПИСАНАТА дължина на
    редукцията — същото правило като при proxy пътя от 13.09
    (``reduction_offset_angle_deg``). Знакът следва начертаната посока.

    Отказва при всичко недоказано: друга форма, липсваща или разклонена
    геометрия, прът, чиито завои не са противоположни (значи не е редукция),
    и ъгъл, който не описва начертания завой.
    """

    if str(row.get("master_shape_code") or "").strip().upper() != "F002":
        return None
    if len(canonical) != 3:
        return None
    try:
        declared_reduction = float(canonical[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(declared_reduction) or declared_reduction <= 0:
        return None

    handles = [str(value) for value in (row.get("candidate_handles") or []) if str(value)]
    if not handles or any(handle not in entity_map for handle in handles):
        return None
    try:
        summary = summarize_selected_geometry([entity_map[handle] for handle in handles])
    except ManualGeometryError:
        return None
    if (
        not summary.connected
        or summary.closed
        or summary.branched
        or summary.segment_count != 3
        or len(summary.points) != 4
        or len(summary.turn_angles_deg) != 2
    ):
        return None

    drawn_first, drawn_second = (float(value) for value in summary.turn_angles_deg)
    # Начертаният прът трябва САМ да е редукция. Два завоя в една посока са
    # Z или U и не стават редукция, защото надписът твърди F002.
    if drawn_first * drawn_second >= 0:
        return None

    offset = _perpendicular_offset(
        (summary.points[0], summary.points[1]), summary.points[-1]
    )
    if offset is None or not math.isfinite(offset) or offset <= 0:
        return None
    rounded = round(offset)
    if rounded > 0 and abs(offset - rounded) <= _F002_OFFSET_ROUNDING_TOLERANCE_CM:
        offset = float(rounded)

    angle = reduction_offset_angle_deg(offset, declared_reduction)
    if angle is None or not math.isfinite(angle) or angle <= 0:
        return None
    if (
        abs(angle - abs(drawn_first)) > _F002_TURN_AGREEMENT_TOLERANCE_DEG
        or abs(angle - abs(drawn_second)) > _F002_TURN_AGREEMENT_TOLERANCE_DEG
    ):
        return None
    return (angle, -angle) if drawn_first > 0 else (-angle, angle)


def _promote_authoritative_matcher_or_f006_segments(
    row: dict,
    *,
    entity_map: Mapping[str, DxfEntityRecord],
) -> tuple[bool, bool]:
    """Return handled/promoted flags for authoritative production evidence."""

    canonical = _finite_positive_segments(row.get("canonical_segment_lengths_cm"))
    # F007 already has a stricter contour-ownership/context proof below. Keep
    # that established path; this helper covers the remaining matcher A-E and
    # exact F006 cases.
    if str(row.get("master_shape_code") or "").strip().upper() == "F007":
        return False, False
    if row.get("dimension_evidence_applied") is True:
        handles = tuple(
            str(value)
            for value in (row.get("dimension_text_handles") or [])
            if str(value)
        )
        strategy = str(row.get("dimension_resolution_strategy") or "").strip().upper()
        records = _numeric_evidence_records(handles, entity_map=entity_map)
        difference_raw = row.get("dimension_total_difference_cm")
        try:
            difference = float(difference_raw)
        except (TypeError, ValueError):
            difference = math.inf
        if (
            canonical is None
            or strategy not in _MATCHER_PRODUCTION_DIMENSION_STRATEGIES
            or records is None
            or not math.isfinite(difference)
            or difference < 0.0
            or difference > 0.51
            or bool(row.get("global_conflict"))
            or bool(row.get("ownership_conflict"))
            or bool(row.get("master_ambiguous_shape_codes"))
        ):
            _block_authoritative_segment_evidence(
                row, "MATCHER_DIMENSION_EVIDENCE_INVALID"
            )
            return True, False
        # Редукцията носи производствен смисъл в ЗНАКА на завоите си, а редът,
        # изведен от отпечатаните коти, няма пренесен вектор. Извежда се от
        # начертаното отместване; при отказ полето остава празно и нататък
        # никой не слага 90/90 наум.
        if not row.get("canonical_turn_angles_deg"):
            reduction_turns = _f002_reduction_turn_angles_deg(
                row, canonical, entity_map=entity_map
            )
            if reduction_turns is not None:
                row["canonical_turn_angles_deg"] = list(reduction_turns)
        promoted = _install_authoritative_production_segments(
            row,
            canonical,
            source="MATCHER_LOCAL_A_E_CANONICAL",
            evidence_details={
                "dimension_handles": list(handles),
                "dimension_measurements_cm": records,
                "dimension_resolution_strategy": strategy,
                "dimension_total_difference_cm": difference,
            },
        )
        return True, promoted

    if (
        str(row.get("master_shape_code") or "").strip().upper() != "F006"
        or str(row.get("f006_production_status") or "").strip().upper()
        != "F006_PRODUCTION_EXACT"
        or row.get("f006_printed_dimensions_resolved") is not True
    ):
        return False, False
    if canonical is None:
        _block_authoritative_segment_evidence(
            row, "F006_EXACT_CANONICAL_INVALID"
        )
        return True, False

    rounded = "ЗАКРЪГЛЕНО СТРЕМЕ" in str(
        row.get("candidate_basis") or ""
    ).upper()
    if rounded:
        local_handles = tuple(
            str(value)
            for value in (
                row.get("local_f006_printed_dimension_handles") or []
            )
            if str(value)
        )
        local_records = _numeric_evidence_records(
            local_handles,
            entity_map=entity_map,
        )
        required_values = {
            round(value, 3) for value in (canonical[0], canonical[1], canonical[2])
        }
        selected_records = [
            record
            for record in (local_records or [])
            if round(float(record["measurement_cm"]), 3) in required_values
        ]
        if (
            not selected_records
            or {round(float(item["measurement_cm"]), 3) for item in selected_records}
            != required_values
        ):
            _block_authoritative_segment_evidence(
                row, "F006_ROUNDED_LOCAL_DIMENSIONS_INVALID"
            )
            return True, False
        selected_handles = tuple(str(item["handle"]) for item in selected_records)
        source = "F006_ROUNDED_LOCAL_PRINTED"
        details = {
            "dimension_handles": list(selected_handles),
            "dimension_measurements_cm": selected_records,
            "local_detail_label_handle": str(
                row.get("local_detail_label_handle") or ""
            ),
        }
    elif (
        str(row.get("contextual_rule_code") or "").strip().upper()
        == "F006_PRINTED_MASTER_EQUATION"
    ):
        source = "F006_PRINTED_MASTER_EXACT"
        details = {}
    else:
        _block_authoritative_segment_evidence(
            row, "F006_EXACT_PROVENANCE_INVALID"
        )
        return True, False

    promoted = _install_authoritative_production_segments(
        row,
        canonical,
        source=source,
        evidence_details=details,
    )
    return True, promoted


def _apply_borderline_curved_f006_owned_dimension_recovery(
    rows: Sequence[dict],
    *,
    entities: Sequence[DxfEntityRecord],
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
    shape_index: EngineShapeIndex,
) -> int:
    """Resolve only the four measured H-3 rounded-F006 false negatives.

    The generic curve normalizer and its 3% connector gate stay untouched.
    This recovery requires the complete conjunction below:

    * one conflict-free LWPOLYLINE on an exact configured stirrup alias;
    * the strict 6-LINE/5-ARC boundary signature from ``geometry_core_v2``;
    * at least three distinct DIMENSION entities whose two endpoints each
      have the same unique contour owner, yielding exactly three size values
      which close the F006 production equation (including bend allowance);
    * an unchanged, unique, non-mirrored match to the locked F006 MASTER,
      including all three repeated-length relations.

    Any missing or ambiguous evidence leaves the original review row intact;
    promotion is staged and committed only after the production installer
    accepts the complete evidence bundle.
    """

    from lion_rebar.dxf.geometry_core_v2 import (
        polyline_path_from_geometry,
        recover_borderline_f006_semantic_path,
    )
    from lion_rebar.dxf.shape_recognition import (
        fingerprint_from_points,
        match_master_shapes,
    )
    from lion_rebar.dxf.vertical_shape_resolver import (
        read_owned_stirrup,
        strict_unique_dimension_records_by_contours,
    )
    from lion_rebar.engine.bend_allowance import segments_match_total

    f006_shapes = [
        shape
        for shape in shape_definitions
        if str(shape.code).strip().upper() == "F006"
        and shape.active
        and shape.locked
        and str(shape.state).strip().upper() in {"APPROVED", "LOCKED"}
        and shape.closed
        and int(shape.recognition.get("segment_count") or 0) == 6
        and int(shape.recognition.get("bend_count") or 0) == 5
    ]
    if len(f006_shapes) != 1:
        return 0
    f006 = f006_shapes[0]
    ordered_segments = sorted(f006.segments, key=lambda segment: segment.index)
    if len(ordered_segments) != 6:
        return 0
    bend_sequence = tuple(
        float(segment.bend_after_deg)
        for segment in ordered_segments[:-1]
        if segment.bend_after_deg is not None
    )
    length_codes = tuple(
        str(segment.length_code or "").strip().upper()
        for segment in ordered_segments
    )
    if bend_sequence != (135.0, 90.0, 90.0, 90.0, 135.0):
        return 0
    if length_codes != ("L1", "L2", "L3", "L2", "L3", "L1"):
        return 0

    aliases = {
        str(value).strip().casefold()
        for value in policy.stirrup_length_group_layer_aliases
        if str(value).strip()
    }
    entity_map = {str(entity.handle): entity for entity in entities}
    owned_records = strict_unique_dimension_records_by_contours(entities)
    recovered_count = 0

    for row in rows:
        if row.get("annotation_class") != "ACTIONABLE":
            continue
        if str(row.get("master_shape_code") or "").strip():
            continue
        if row.get("approved_in_project") or row.get("production_export_blocked"):
            continue
        if any(
            bool(row.get(key))
            for key in (
                "global_conflict",
                "geometry_owner_conflict",
                "ownership_conflict",
                "master_conflict",
            )
        ):
            continue
        if row.get("master_ambiguous_shape_codes"):
            continue
        handles = tuple(
            str(value)
            for value in (row.get("candidate_handles") or ())
            if str(value)
        )
        if len(handles) != 1:
            continue
        owner = handles[0]
        entity = entity_map.get(owner)
        if entity is None or entity.entity_type.upper() != "LWPOLYLINE":
            continue
        if str(entity.layer).strip().casefold() not in aliases:
            continue
        if str(row.get("candidate_layer") or "").strip().casefold() not in aliases:
            continue
        review_codes = {
            str(value).strip().upper()
            for value in (row.get("review_reason_codes") or ())
            if str(value).strip()
        }
        if review_codes != {
            "UNRESOLVED_CURVE_GEOMETRY",
            "CURVED_LAYER_ALIAS",
        }:
            continue
        if (
            int(row.get("source_curve_count") or 0) != 5
            or int(row.get("normalized_bend_count") or 0) != 4
            or int(row.get("unresolved_curve_count") or 0) != 1
        ):
            continue

        records = list(owned_records.get(owner, ()))
        dimension_handles = {
            str(record.get("handle") or "")
            for record in records
            if str(record.get("handle") or "")
        }
        if len(dimension_handles) < 3:
            continue
        owned_values = tuple(
            float(record.get("measurement_cm") or 0.0)
            for record in records
        )
        if len({value for value in owned_values if value > 0.0}) != 3:
            continue
        owned = read_owned_stirrup(owned_values)
        if owned is None:
            continue
        width, height, hook = (float(value) for value in owned)
        canonical = (hook, height, width, height, width, hook)
        declared = _positive_float(row.get("declared_length_cm"))
        if declared is None or not segments_match_total("F006", sum(canonical), declared):
            continue

        path = polyline_path_from_geometry(entity.geometry)
        if path is None:
            continue
        semantic = recover_borderline_f006_semantic_path(path)
        if semantic is None:
            continue
        master_match = match_master_shapes(
            semantic.points,
            shape_definitions,
            policy=policy,
            control_tests=control_tests,
            shape_index=shape_index,
            candidate_closed_override=True,
            implicit_closure=True,
        )
        if (
            master_match is None
            or str(master_match.shape_code).strip().upper() != "F006"
            or master_match.ambiguous_shape_codes
            or master_match.mirror_requires_review
            or master_match.required_relation_count < 3
        ):
            continue
        fingerprint = fingerprint_from_points(
            semantic.points,
            closed=True,
            implicit_closure=True,
            policy=policy,
        )
        if fingerprint is None or fingerprint.segment_count != 6:
            continue

        ordered_dimension_handles = [
            str(record.get("handle") or "")
            for record in records
            if str(record.get("handle") or "")
        ]
        original_confidence = row.get("candidate_confidence")
        note = (
            "F006_BORDERLINE_CURVE_OWNED_DIMENSIONS: един краен 135° hook "
            f"има offset residual {semantic.offset_residual_ratio * 100:.4f}% "
            "(строго над общите 3.0%, в тесния лимит 3.25%); петте радиуса "
            f"са равномерни (spread {semantic.radius_relative_spread * 100:.6f}%). "
            f"Собствени котировки {width:g} x {height:g}, кука {hook:g}; "
            "непромененият MASTER matcher потвърди еднозначно F006 и трите "
            "задължителни отношения."
        )
        staged_row = deepcopy(row)
        staged_row.update(
            {
                "candidate_original_confidence": original_confidence,
                "batch_status": "СИЛНО СТРЕМЕ F006",
                "analysis_status": (
                    "НАМЕРЕН Е СИЛЕН КАНДИДАТ — ИЗИСКВА ВИЗУАЛНА ПРОВЕРКА"
                ),
                "candidate_confidence": F006_STRONG_CONFIDENCE,
                "candidate_basis": (
                    "СТРОГО F006 — КОНТУРНИ КОТИРОВКИ + ГРАНИЧНА КРАЙНА ДЪГА"
                ),
                "segment_count": 6,
                "canonical_signature": fingerprint.canonical_signature,
                "master_shape_code": master_match.shape_code,
                "master_shape_name": master_match.shape_name,
                "master_shape_revision": master_match.shape_revision,
                "master_shape_mirrored": master_match.mirrored,
                "master_shape_reversed": master_match.reversed_direction,
                "master_shape_score": master_match.score,
                "master_reference_profile_id": master_match.reference_profile_id,
                "master_ambiguous_shape_codes": [],
                "mirror_requires_review": False,
                "uniform_scale_factor": master_match.uniform_scale_factor,
                "normalized_bend_count": 5,
                "unresolved_curve_count": 0,
                "hooked_closure": True,
                "hooked_closure_gap_raw": semantic.hooked_closure_gap_raw,
                "canonical_segment_lengths_cm": list(canonical),
                "canonical_turn_angles_deg": [135.0, 90.0, 90.0, 90.0, 135.0],
                "effective_geometry_length_cm": declared,
                "numeric_difference": 0.0,
                "contextual_rule_code": (
                    "F006_BORDERLINE_CURVE_OWNED_DIMENSIONS"
                ),
                "contextual_rule_note": note,
                "review_reason_codes": [
                    "CURVED_LAYER_ALIAS",
                    "F006_BORDERLINE_CURVE_OWNED_DIMENSIONS",
                ],
                "f006_borderline_curve_recovery": {
                    "missing_arc_index": semantic.missing_arc_index,
                    "offset_residual_ratio": semantic.offset_residual_ratio,
                    "radius_relative_spread": semantic.radius_relative_spread,
                    "hooked_closure_gap_raw": semantic.hooked_closure_gap_raw,
                    "owner_geometry_handle": owner,
                    "dimension_handles": ordered_dimension_handles,
                    "dimension_measurements_cm": records,
                },
                "f006_production_status": "F006_PRODUCTION_DERIVED",
                "f006_production_reason": (
                    "F006_BORDERLINE_CURVE_OWNED_DIMENSIONS"
                ),
                "f006_printed_dimensions_resolved": True,
                "production_export_blocked": False,
                "production_export_block_reason": "",
                "dimension_derived_evidence": {
                    "shape_code": "F006",
                    "segments": [width, height, hook],
                    "source": "СОБСТВЕНИ КОТИРОВКИ НА КОНТУРА",
                    "confidence": "HIGH",
                    "measured_dimensions": 3,
                    "computed_dimensions": 0,
                    "bend_allowance_cm": declared - sum(canonical),
                },
            }
        )
        if not _install_authoritative_production_segments(
            staged_row,
            canonical,
            source="F006_BORDERLINE_CURVE_OWNED_DIMENSIONS",
            evidence_details={
                "owner_geometry_handles": [owner],
                "dimension_handles": ordered_dimension_handles,
                "dimension_measurements_cm": records,
                "offset_residual_ratio": semantic.offset_residual_ratio,
                "radius_relative_spread": semantic.radius_relative_spread,
            },
        ):
            continue
        staged_row["candidate_notes"] = [
            *list(staged_row.get("candidate_notes", [])),
            note,
        ]
        staged_row["analysis_warnings"] = [
            *list(staged_row.get("analysis_warnings", [])),
            note,
        ]
        row.clear()
        row.update(staged_row)
        recovered_count += 1
    return recovered_count


def _blocked_only_for_missing_printed_dimensions(row: Mapping[str, Any]) -> bool:
    """F006, блокирано единствено защото надписаните размери „липсват“.

    Този блок се слага от пасa за отпечатаните размери, когато той не успее
    да ги сглоби от измерената геометрия (s-11, Тип 1b/1c: едната опашка е
    начертана 10,2 вместо 10). Той не твърди, че котите са грешни — твърди,
    че ги няма. Собствена кота върху контура е точно липсващото
    доказателство, затова само тя може да го отмени. Всеки друг блок остава
    окончателен.
    """

    return (
        bool(row.get("production_export_blocked"))
        and str(row.get("master_shape_code") or "").strip().upper() == "F006"
        and str(row.get("production_export_block_reason") or "").strip().upper()
        == "F006_PRINTED_DIMENSIONS_MISSING"
    )


#: Две страни на правоъгълник се котират в перпендикулярни посоки. Допускът
#: е за чертожен шум в опорните точки, не за наклонени страни.
_EXPLICIT_SQUARE_MAX_COSINE = math.sin(math.radians(2.0))


def _explicit_square_stirrup(
    records: Sequence[Mapping[str, Any]],
    entity_map: Mapping[str, DxfEntityRecord],
) -> list[float] | None:
    """Квадратно стреме от три собствени коти: две равни страни и кука.

    s-11, Тип 1b/1c (2026-09-17): детайлът 25×25 с кука 10 носи три коти
    върху самия контур — хоризонтална 25, вертикална 25 и 10 по куката.
    ``read_owned_stirrup`` вижда само стойностите {25, 10} и при две
    стойности иска обичайната кука на чертежа. В s-11 най-честата малка кота
    е страна 20, затова 25×25 с кука 10 се отказваше. Собственик: „по-редно е
    да взимаме надписите за верни, а не да мерим всеки един елемент“.

    Три коти, от които двете равни мерят в перпендикулярни посоки, не
    оставят съмнение: те са ширината и височината, а по-малката е куката.
    Всяко друго разположение се връща като неизвестно. Сборът се проверява
    срещу L от извикващия код, както при всички собствени коти.
    """

    if len(records) != 3:
        return None
    values = [_positive_float(item.get("measurement_cm")) for item in records]
    if any(value is None for value in values):
        return None
    side, hook = max(values), min(values)
    sides = [item for item, value in zip(records, values) if value == side]
    hooks = [item for item, value in zip(records, values) if value == hook]
    if len(sides) != 2 or len(hooks) != 1 or not hook < side:
        return None
    directions = []
    for item in sides:
        dimension = entity_map.get(str(item.get("handle") or ""))
        geometry = dimension.geometry if dimension is not None else {}
        first, second = geometry.get("defpoint2"), geometry.get("defpoint3")
        if not first or not second:
            return None
        dx = float(second[0]) - float(first[0])
        dy = float(second[1]) - float(first[1])
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return None
        directions.append((dx / length, dy / length))
    cosine = abs(
        directions[0][0] * directions[1][0] + directions[0][1] * directions[1][1]
    )
    if cosine > _EXPLICIT_SQUARE_MAX_COSINE:
        return None
    return [side, side, hook]


#: Колко може да се разминат предложен участък и надписана кота, за да се
#: смята, че котата Е този участък. Същият обсег като при раждането на форма
#: от случай (``engine.shape_from_case.DIMENSION_SNAP_CM``).
_SUGGESTION_PRINTED_TOLERANCE_CM = 1.2


def _apply_born_from_case_production_sizes(
    rows: list[dict],
    shape_definitions: Sequence[Any],
) -> int:
    """Размерите на форма, РОДЕНА ОТ СЛУЧАЙ, се четат както е родена тя.

    ЗАЩО. Формата от отказан случай се ражда, като отношенията от каноничната
    сигнатура посочват коя НАДПИСАНА кота е кой участък
    (``engine.shape_from_case``). Matcher-ът после я разпознава, но
    производствените ѝ размери оставаха празни и портата спираше позицията с
    ``MASTER_LENGTH_CHECK``.

    Измерено на „тест-001“ след раждането на F020: и петте кобилици излизат
    „СИЛЕН ПО ГЕОМЕТРИЯ“ с форма F020, а ``production_segments_cm`` е ``None``.
    Двете ``2x3N14x210`` падат, защото начертаното (205,25) сочи закръгляне към
    205, а надписът е 210 — докато надписаните 50/23/60 дават 206 и сочат 210.

    Прилага се САМО върху форми, които носят ``born_from_case`` в дефиницията
    си: те са родени с това правило и е редно да се мерят с него. Останалите
    форми не се пипат.
    """

    from lion_rebar.engine.shape_from_case import (
        ShapeFromCaseError,
        parse_signature,
        read_segment_values,
    )

    from lion_rebar.export.auto_gate import validate_production_segment_evidence

    родени = {
        str(shape.code or "").strip().upper(): shape
        for shape in (shape_definitions or ())
        if isinstance(getattr(shape, "recognition", None), dict)
        and bool(shape.recognition.get("born_from_case"))
    }
    if not родени:
        return 0

    попълнени = 0
    for row in rows:
        код = str(row.get("master_shape_code") or "").strip().upper()
        if код not in родени or row.get("production_segments_cm"):
            continue
        if row.get("suggested_segments_cm"):
            continue
        try:
            _closed, ratios, _angles = parse_signature(
                str(row.get("canonical_signature") or "")
            )
        except ShapeFromCaseError:
            continue
        сурова = _positive_float(row.get("geometry_length_raw"))
        if сурова is None:
            continue
        стойности = read_segment_values(
            ratios, сурова, [
                стойност
                for стойност in (row.get("nearby_dimensions_cm") or ())
                if isinstance(стойност, (int, float))
            ],
        )
        if not стойности:
            continue
        прочит = [round(float(v), 3) for v in стойности]
        доказателство = {
            "source": "BORN_FROM_CASE_PRINTED",
            "shape_code": код,
            "segments": list(прочит),
        }

        # Доказан размер се твърди САМО ако независимият проверител се съгласи.
        #
        # Гейтът вече познава формите от ДЕФИНИЦИЯ (``auto_gate`` подава
        # закотвената форма на ``engine_shape_parameters.parameters_for_shape``),
        # затова тук не се преценява наум дали прочитът е годен: строи се
        # пробен ред и се пита самият проверител. Той преизвежда прочита от
        # котите наново и проверява дали уравнението затваря с обявената
        # стъпка за закръгляне на формата.
        #
        # Защо не се пише направо: дотук измереното от чертежа изместваше
        # надписаното. На „Тест-003“ трите кобилици влизаха в описа с
        # ``50 · 16,9706 · 60 · 16,9706 · 50`` при надписани 50/17/60, защото
        # предложението не се признаваше за доказателство и портата падаше на
        # геометрията. Отказът остава отказ: случай, който не затваря, си
        # остава предложение за оператора.
        проба = dict(row)
        проба["production_segments_cm"] = list(прочит)
        проба["production_segment_evidence"] = dict(доказателство)
        if validate_production_segment_evidence(проба, shape=родени[код]):
            row["production_segments_cm"] = list(прочит)
            row["production_segment_evidence"] = dict(доказателство)
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                (
                    f"{код}_BORN_FROM_CASE_SIZES: производствените размери са "
                    "прочетени от надписаните коти по същото правило, по което "
                    "е родена формата"
                ),
            ]
        else:
            row["suggested_shape_code"] = код
            row["suggested_segments_cm"] = list(прочит)
            row["suggested_evidence"] = dict(доказателство)
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                (
                    f"{код}_BORN_FROM_CASE_SIZES: размерите са прочетени от "
                    "надписаните коти по същото правило, по което е родена "
                    "формата; остават предложение до одобрение от оператора"
                ),
            ]
        попълнени += 1
    return попълнени


def _every_segment_is_printed(segments, printed) -> bool:
    """Всеки предложен участък трябва да е НАДПИСАН в чертежа.

    Без разпозната форма нищо не ограничава досмятането: уравнението приема
    всяка близка кота и довършва остатъка наум. Реален случай (собственик,
    22.09.2026, `тест-001`): за „4N14x310“ — прав прът, който той сам реши
    като F001 с причина „липсва или не е необходима отделна геометрия“ —
    предложението излизаше „F003 · 51 + 208 + 51“, където 51 е кота на
    съседна фигура, а 208 е 310 − 2×51.
    """

    стойности = []
    for item in printed or ():
        try:
            стойности.append(float(item))
        except (TypeError, ValueError):
            continue
    if not стойности or not segments:
        return False
    for участък in segments:
        try:
            цел = float(участък)
        except (TypeError, ValueError):
            return False
        if min(abs(цел - кота) for кота in стойности) > _SUGGESTION_PRINTED_TOLERANCE_CM:
            return False
    return True


def _apply_dimension_derived_production_sizes(
    rows: list[dict],
    entities: Sequence[DxfEntityRecord],
) -> int:
    """Извежда производствени размери само от доказан източник.

    Предназначено за чертежи на вертикални елементи, при които размерите на
    стремената са изписани като котировки, а не като свободен текст. Без
    този източник позицията остава ``F006_PRODUCTION_REVIEW``, защото
    формата е известна, а размерите — не.

    Прилага се САМО върху редове, които вече са в production review, и
    само когато уравнението се затваря точно. Никога не презаписва вече
    доказани размери и никога не гадае.
    """

    from lion_rebar.dxf.vertical_shape_resolver import (
        derive_shape,
        dimensions_owned_by_contours,
        infer_preferred_gap,
        infer_preferred_hook,
        read_owned_stirrup,
    )
    from lion_rebar.engine.bend_allowance import segments_match_total

    # ПЪРВИ ИЗТОЧНИК: котировки, чиито опорни точки лежат върху контура.
    # Това е връзка, начертана от проектанта -- не преценка по разстояние.
    # Комбиниране и проверка срещу дължината НЕ се прилагат: в описа влиза
    # каквото е надписано.
    owned_dimensions = dimensions_owned_by_contours(entities)
    owned_dimension_records = _owned_dimension_records_by_contour(
        entities,
        dimensions_owned_by_contours,
    )
    entity_map = {str(entity.handle): entity for entity in entities}
    resolved = 0
    for row in rows:
        _handled, promoted = _promote_authoritative_matcher_or_f006_segments(
            row,
            entity_map=entity_map,
        )
        if promoted:
            resolved += 1

    dimensions = [
        {
            "measurement": entity.geometry.get("measurement"),
            "point": (
                entity.geometry.get("text_midpoint")
                or entity.geometry.get("defpoint")
                or (0.0, 0.0)
            ),
        }
        for entity in entities
        if entity.entity_type == "DIMENSION" and entity.geometry.get("measurement")
    ]
    if not dimensions:
        return resolved + _apply_contextual_f007_without_dimensions(
            rows,
            owned_dimension_records,
        )

    # Обичайната кука на ТОЗИ проектант, изведена от самия чертеж.
    preferred_hook = infer_preferred_hook(
        [float(item["measurement"]) for item in dimensions]
    )

    positions = {
        str(entity.handle): entity.geometry.get("insert")
        for entity in entities
        if entity.geometry.get("insert")
    }

    # Легендата с кръгчета: позиция без собствени котировки може да получи
    # Ø и дължина от легендата долу в чертежа.
    # ДВЕ МИНАВАНИЯ: първото измерва обичайната добавка за огъване на този
    # проектант, второто я ползва за разчистване на двусмислие. Реален
    # случай: 1092N8x255 се затваря и като 95x22 (разлика 1), и като 95x20
    # (разлика 5). Проектантът ползва 5 последователно.
    measured_gaps: list[float] = []
    for probe in rows:
        if str(probe.get("master_shape_code") or "").strip().upper() != "F006":
            continue
        probe_point = positions.get(str(probe.get("annotation_handle") or ""))
        probe_length = _positive_float(probe.get("declared_length_cm"))
        if not probe_point or probe_length is None:
            continue
        probe_evidence = derive_shape(probe_point, dimensions, probe_length,
                                      expected_shape="F006",
                                      preferred_hook=preferred_hook)
        if probe_evidence is not None:
            measured_gaps.append(probe_evidence.allowance)
    preferred_gap = infer_preferred_gap(measured_gaps)

    from lion_rebar.dxf.legend_key_resolver import (
        collect_legend,
        key_for_position,
        legend_texts_from_entities,
        resolve_from_legend,
    )

    legend_texts = legend_texts_from_entities(entities)
    legend = collect_legend(legend_texts)

    # Нечетимите множители се пренасят от импортера до реда, за да могат
    # да блокират автоматичния изход. Без това невалиден BX/BP се губи.
    multiplier_problems = {
        str(entity.handle): entity.geometry.get("count_multiplier_problems")
        for entity in entities
        if entity.geometry.get("count_multiplier_problems")
    }

    for row in rows:
        master = str(row.get("master_shape_code") or "").strip().upper()
        point_for_row = positions.get(str(row.get("annotation_handle") or ""))
        problems = multiplier_problems.get(str(row.get("annotation_handle") or ""))
        if problems:
            row["count_multiplier_problems"] = list(problems)
            flags = list(row.get("data_quality_flags") or [])
            flags.append("MULTIPLIER_UNREADABLE")
            row["data_quality_flags"] = sorted(set(flags))
        if point_for_row and legend:
            key = key_for_position(point_for_row, legend_texts)
            if key:
                row["legend_key"] = key
                entry = resolve_from_legend(
                    point_for_row, legend_texts, legend,
                    diameter_mm=_positive_float(row.get("diameter_mm")),
                    length_cm=_positive_float(row.get("declared_length_cm")))
                if entry is not None:
                    row["legend_entry"] = entry.to_dict()
                    row["legend_confirmed"] = True
        if point_for_row and not row.get("nearby_dimensions_cm"):
            # Котировките се записват за ВСЯКА позиция, включително без
            # разпозната MASTER форма. Точно там операторът има най-голяма
            # нужда от тях: панелът иначе показва „Форма: — —“ и празно поле,
            # а размерите стоят в чертежа.
            row["nearby_dimensions_cm"] = _nearby_dimension_values(
                point_for_row, dimensions)
        printed_dimensions_missing_only = _blocked_only_for_missing_printed_dimensions(row)
        if bool(row.get("production_export_blocked")) and not printed_dimensions_missing_only:
            # Invalid/conflicting authoritative evidence is terminal for this
            # run. Nearby DIMENSION entities must never repair it silently.
            continue
        if master in ("F003", "F004", "F007", "F002"):
            # ЕКСПЕРИМЕНТ C: отворените форми също имат нужда от доказани
            # сегменти. Без тях Engine-ът отказва с MASTER_LENGTH_CHECK
            # ("няма доказани сегментни размери"), макар котировките да са
            # в чертежа.
            if row.get("production_segments_cm"):
                continue
            declared = _positive_float(row.get("declared_length_cm"))
            point = positions.get(str(row.get("annotation_handle") or ""))
            if declared is None or not point:
                continue

            # A proven canonical F007 vector is owned evidence. It stays
            # above the generic proximity-only dimension fallback.
            if master == "F007" and _apply_proven_f007_canonical_segments(
                row,
                declared,
                owned_dimension_records=owned_dimension_records,
            ):
                resolved += 1
                continue
            row["nearby_dimensions_cm"] = _nearby_dimension_values(point, dimensions)
            evidence = derive_shape(point, dimensions, declared,
                                    expected_shape=master,
                                    preferred_hook=preferred_hook,
                                preferred_gap=preferred_gap)
            if evidence is None:
                continue
            if evidence.confidence not in ("HIGH", "MEDIUM"):
                continue
            row["production_segments_cm"] = list(evidence.segments)
            row["canonical_turn_angles_deg"] = [90.0] * (len(evidence.segments) - 1)
            row["dimension_derived_evidence"] = evidence.to_dict()
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                (
                    f"{master}_SEGMENTS_DERIVED: размерите са изведени от "
                    "котировките; сумата съвпада с производствената дължина"
                ),
            ]
            resolved += 1
            continue
        if row.get("production_segments_cm"):
            continue
        if master != "F006":
            # An explicit one-to-one ЕСОВЕ caption proves the family but not
            # the production axis or its segment lengths. Preserve that useful
            # evidence as an operator suggestion: без точните сегменти още не
            # може да се твърди какво е производственото решение. Stop here:
            # nearby overall or projection dimensions must never manufacture
            # F003/F004.
            if (
                not master
                and str(row.get("local_detail_shape_code") or "").upper()
                == "F007"
            ):
                label_handle = str(row.get("local_detail_label_handle") or "")
                label_text = str(row.get("local_detail_label_text") or "")
                # Без форма не може да се твърди какво е производството;
                # това поле е само предложение към оператора.
                row["suggested_shape_code"] = "F007"
                row["suggested_segments_cm"] = []
                row["suggested_evidence"] = {
                    "shape_code": "F007",
                    "segments": [],
                    "source": "ИЗРИЧЕН ЛОКАЛЕН НАДПИС ЕСОВЕ",
                    "confidence": "MEDIUM",
                    "family_only": True,
                    "label_handle": label_handle,
                    "label_text": label_text,
                }
                row["candidate_notes"] = [
                    *list(row.get("candidate_notes", [])),
                    (
                        "F007_FAMILY_SUGGESTED: надписът доказва S/C-образна "
                        "форма; точните сегменти остават за инженерно "
                        "потвърждение, защото няма сигурна производствена ос."
                    ),
                ]
                continue
            # Позиция БЕЗ разпозната MASTER форма също получава предложени
            # размери, стига котировките да се връзват. Те НЕ стават
            # производствени -- влизат само като предложение за оператора,
            # защото без форма не може да се твърди какво е.
            #
            # Реален случай: 2x(28N8x115) е стреме 25x20 с кука 10; котите
            # са в чертежа, но панелът показваше само сурови числа, защото
            # тази стъпка изобщо не достигаше позицията.
            if master or not point_for_row or not row.get("nearby_dimensions_cm"):
                continue
            declared_for_hint = _positive_float(row.get("declared_length_cm"))
            if declared_for_hint is None:
                continue
            hint = derive_shape(point_for_row, dimensions, declared_for_hint,
                                preferred_hook=preferred_hook,
                                preferred_gap=preferred_gap)
            # ВСЕКИ участък да е НАДПИСАН. Без форма и без геометрия няма
            # какво да ограничи досмятането: уравнението приема всяка близка
            # кота и довършва остатъка наум.
            #
            # Реален случай (собственик, 22.09.2026, `тест-001`): за
            # „4N14x310“ — прав прът, който той сам е решил като F001 с
            # причина „липсва или не е необходима отделна геометрия“ —
            # предложението излизаше „F003 · 51 + 208 + 51“. Петдесет и едно
            # е кота на съседна фигура, а 208 е 310 − 2×51, тоест изчислено.
            # Такова предложение изглежда точно и затова е по-опасно от
            # никакво: операторът вижда три размера вместо въпрос.
            #
            # Позиция с разпозната форма НЕ минава оттук — там досмятането е
            # ограничено от самата форма.
            #
            # Проверява се срещу САМИТЕ коти, не по ``computed_count``: при
            # F004 то е ``3 − измерени`` и при две надписани дава 1, тоест не
            # брои досметнатите.
            if hint is not None and _every_segment_is_printed(
                hint.segments, row.get("nearby_dimensions_cm")
            ):
                row["suggested_shape_code"] = hint.shape_code
                row["suggested_segments_cm"] = list(hint.segments)
                row["suggested_evidence"] = hint.to_dict()
            continue
        declared = _positive_float(row.get("declared_length_cm"))
        if declared is None:
            continue
        point = positions.get(str(row.get("annotation_handle") or ""))
        if not point:
            continue
        # Котировките около позицията се записват ВИНАГИ, дори когато
        # извеждането не успее. Без тях операторът вижда „Форма: — —“ и
        # няма от какво да вземе решение -- размерите съществуват в
        # чертежа, но не стигат до екрана.
        row["nearby_dimensions_cm"] = _nearby_dimension_values(point, dimensions)
        # 1) Собствени котировки на контура -- четат се както са надписани.
        owned_sets = [
            (str(candidate), list(owned_dimensions[str(candidate)]))
            for candidate in (row.get("candidate_handles") or [])
            if str(candidate) in owned_dimensions
        ]
        # Котировки от два отделни контура никога не се обединяват. Това би
        # създало несъществуващ размерен комплект от съседни кандидати.
        owned_owner = owned_sets[0][0] if len(owned_sets) == 1 else ""
        owned_values = owned_sets[0][1] if len(owned_sets) == 1 else []
        owned = read_owned_stirrup(owned_values, preferred_hook)
        if not owned and owned_owner:
            owned = _explicit_square_stirrup(
                owned_dimension_records.get(owned_owner, ()),
                entity_map,
            )
        if owned:
            width, height, hook = owned
            production_segments = [hook, height, width, height, width, hook]
            if not segments_match_total(
                "F006", sum(production_segments), declared,
            ):
                row["f006_production_status"] = "F006_PRODUCTION_REVIEW"
                row["f006_production_reason"] = (
                    "OWNED_DIMENSIONS_LENGTH_MISMATCH"
                )
                row["production_export_blocked"] = True
                row["production_export_block_reason"] = (
                    "Собствените котировки не съвпадат с общата "
                    "производствена дължина в допустимата добавка за огъване."
                )
                row["candidate_notes"] = [
                    *list(row.get("candidate_notes", [])),
                    (
                        f"F006_OWNED_DIMENSIONS_BLOCKED: сегменти "
                        f"{sum(production_segments):g} cm срещу L={declared:g} cm"
                    ),
                ]
                continue
            row["f006_production_status"] = "F006_PRODUCTION_DERIVED"
            row["f006_production_reason"] = "OWNED_DIMENSIONS"
            row["production_export_blocked"] = False
            row["production_export_block_reason"] = ""
            row["f006_printed_dimensions_resolved"] = True
            row["dimension_derived_evidence"] = {
                "shape_code": "F006",
                "segments": [width, height, hook],
                "source": "СОБСТВЕНИ КОТИРОВКИ НА КОНТУРА",
                "confidence": "HIGH",
                "measured_dimensions": len({width, height, hook}),
                "computed_dimensions": 0,
            }
            owner_records = list(owned_dimension_records.get(owned_owner, ()))
            row["canonical_segment_lengths_cm"] = list(production_segments)
            row["canonical_turn_angles_deg"] = [90.0, 90.0, 90.0, 90.0]
            if not _install_authoritative_production_segments(
                row,
                tuple(float(value) for value in production_segments),
                source="F006_OWNED_CONTOUR_DIMENSIONS",
                evidence_details={
                    "owner_geometry_handles": [owned_owner],
                    "dimension_handles": [
                        str(item.get("handle") or "") for item in owner_records
                    ],
                    "dimension_measurements_cm": owner_records,
                },
            ):
                continue
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                (f"F006_OWNED_DIMENSIONS: {width:g} x {height:g}, кука {hook:g} "
                 "— котировките лежат върху контура"),
            ]
            resolved += 1
            continue

        if printed_dimensions_missing_only:
            # Блокът казва „липсват надписани размери“. Отменя го само
            # собствена кота на контура; размери по близост не го поправят.
            continue
        # 2) Резервен път: търсене по близост с уравнение като проверка.
        evidence = derive_shape(point, dimensions, declared, expected_shape="F006",
                                preferred_hook=preferred_hook,
                                preferred_gap=preferred_gap)
        if evidence is None or evidence.shape_code != "F006":
            continue
        if evidence.confidence not in ("HIGH", "MEDIUM"):
            # Слабо доказателство -> позицията остава при оператора.
            continue
        width, height, hook = evidence.segments
        row["f006_production_status"] = "F006_PRODUCTION_DERIVED"
        row["f006_production_reason"] = "DIMENSION_EQUATION"
        row["production_export_blocked"] = False
        row["production_export_block_reason"] = ""
        row["production_segments_cm"] = [hook, height, width, height, width, hook]
        row["f006_printed_dimensions_resolved"] = True
        row["dimension_derived_evidence"] = evidence.to_dict()
        row["candidate_notes"] = [
            *list(row.get("candidate_notes", [])),
            (
                "F006_PRODUCTION_DERIVED: размерите са изведени от котировките "
                f"({width:g} x {height:g}, кука {hook:g}); уравнението се "
                f"затваря с добавка {evidence.allowance:g} cm"
            ),
        ]
        resolved += 1
    return resolved


def _apply_proxy_declared_shape_suggestions(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
) -> int:
    """Owner-requested 2026-09-11: show the arm_koloni/arm_shaibi
    declared-shape evidence (``proxy_adapter.py``'s
    ``PROXY_DECLARED_SHAPE_CODE`` -- a MASTER shape independently proven
    by the designer's own declared segments PLUS real ARC-count evidence,
    never text alone) as the operator's on-screen "ПРЕДЛОЖЕНИЕ" in Review
    Center, instead of a coincidental guess from unrelated nearby geometry
    or DIMENSION entities.

    Deliberately narrow: only overwrites ``suggested_shape_code`` /
    ``suggested_segments_cm`` / ``suggested_evidence`` -- the read-only
    hint fields rendered by ``ui/review_center.py``. Never touches
    ``master_shape_code``, ``batch_status``, ``matcher_disposition`` or
    any other field that feeds AUTO_BATCH_GATE: the operator still
    reviews and confirms this position by hand, exactly as before, just
    without having to reconstruct the correct numbers themselves.

    A row that already has a ``master_shape_code`` is normally left
    alone (a real, proven geometric match always wins). Owner-confirmed
    2026-09-12, verified exhaustively against both real files: this
    default is wrong specifically when that ``master_shape_code`` itself
    came from a proven-unreliable generic-search match --
    ``FAR_ASSOCIATION`` (0 of 33 real FAR_ASSOCIATION rows in koloni+
    shaibi ever coincide with the annotation's own geometry) or
    ``GEOMETRY_OWNERSHIP_CONFLICT`` (real case: two independent columns,
    "50N8x210", both weakly matched the SAME unrelated straight line on
    the generic ``arm_koloni`` layer, which carries no per-diameter
    layer name for ``geometry_candidates.py``'s diameter check to reject
    against). In that situation the coincidental match is not actually
    more trustworthy than the arc-verified declared shape -- it is
    exactly as unconfirmed, and hiding the better hint behind it made the
    trap harder for the operator to notice. The row still requires the
    same manual confirmation either way; this only changes which number
    the operator sees while doing it."""

    applied = 0
    for row in rows:
        reasons = row.get("review_reason_codes") or []
        blockers = row.get("matcher_blocker_codes") or []
        has_unreliable_existing_match = (
            "FAR_ASSOCIATION" in reasons or "GEOMETRY_OWNERSHIP_CONFLICT" in blockers
        )
        if row.get("master_shape_code") and not has_unreliable_existing_match:
            continue
        handle = str(row.get("annotation_handle") or "")
        if not handle:
            continue
        annotation = annotations_by_handle.get(handle)
        if annotation is None:
            continue
        raw = annotation.raw or {}
        if raw.get("declared_shape_source") != PROXY_DECLARED_SHAPE_CODE:
            continue
        declared_code = raw.get("declared_shape_code")
        declared_segments = raw.get("declared_shape_segments_cm")
        if not declared_code or not declared_segments:
            continue
        row["suggested_shape_code"] = declared_code
        row["suggested_segments_cm"] = list(declared_segments)
        row["suggested_evidence"] = {
            "shape_code": declared_code,
            "segments": list(declared_segments),
            "source": raw.get("declared_shape_source"),
            "confidence": raw.get("declared_shape_confidence"),
            "bend_arc_count": raw.get("declared_shape_bend_arc_count"),
        }
        applied += 1
    return applied


#: Production segment forms this promotion knows how to build from the
#: resolver's own declared breakdown, verified numerically against both
#: real files: F001 closes directly (1 value), F003 closes directly (3
#: values), F006 closes only once unfolded (hook, w, h, w, h, hook).
#: F002 is deliberately absent -- see
#: ``auto_gate._proxy_arc_verified_evidence_is_owned``. F004/F005 and the
#: rest are absent because no real drawing has produced one through this
#: path yet, and guessing an unverified production order is exactly the
#: kind of silent error this whole dialect was built to avoid.
#: ``F002`` was held back while "zero bend arcs" was the only shape evidence:
#: it shares that signal with F001, and picking wrong had already produced one
#: real misclassification (owner-caught 2026-09-11). That ambiguity is gone --
#: the three-part shapes are now read from the annotation's own DRAWN legs
#: (``proxy_adapter._read_declared_three_leg_shape``), where F002's inclined
#: middle between two parallel legs and F001's single leg cannot be confused.
_PROXY_AUTO_PROMOTABLE_SHAPES = frozenset({"F001", "F002", "F003", "F006", "F007"})
_PROXY_AUTO_TOTAL_TOLERANCE_CM = 0.51

#: Canonical strong batch statuses (mirrors review_center_model's constants
#: and auto_gate.AUTO_EXPORT_BATCH_STATUSES; this module writes these values
#: as literals elsewhere, so they are restated here rather than imported).
_PROXY_STRONG_BATCH_STATUSES = frozenset(
    {"СИЛЕН", "СИЛЕН ПО ГЕОМЕТРИЯ", "СИЛНО СТРЕМЕ F006", "СИЛЕН КАНДИДАТ"}
)
#: Raised when the geometric matcher and the independent bend-arc evidence
#: claim different shape families. Neither side may win silently.
_PROXY_SHAPE_CONTRADICTION_CODE = "DECLARED_SHAPE_CONTRADICTS_GEOMETRY"


#: Допуск при сравняване на две разбивки, в сантиметри. Колкото и
#: MASTER-формулите: под него двете описват един и същи прът.
_SEGMENT_MATCH_TOLERANCE_CM = 0.11


def _segment_vectors_agree(
    left: Sequence[float] | None,
    right: Sequence[float] | None,
) -> bool:
    """Описват ли двата вектора един и същи прът."""

    if left is None or right is None:
        return False
    try:
        a = [float(value) for value in left]
        b = [float(value) for value in right]
    except (TypeError, ValueError):
        return False
    if len(a) != len(b) or not a:
        return False
    return all(
        math.isfinite(x) and math.isfinite(y)
        and abs(x - y) <= _SEGMENT_MATCH_TOLERANCE_CM
        for x, y in zip(a, b)
    )


def _proxy_production_segments(
    shape_code: str,
    declared_segments: Sequence[float],
) -> list[float] | None:
    """Build the MASTER production segment vector, or None if this shape's
    production form is not one this promotion is allowed to construct."""

    code = str(shape_code or "").strip().upper()
    try:
        values = [float(value) for value in declared_segments]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value <= 0 for value in values):
        return None
    if code == "F001":
        return values if len(values) == 1 else None
    if code in ("F002", "F003", "F007"):
        return values if len(values) == 3 else None
    if code == "F006":
        if len(values) != 3:
            return None
        # vertical_shape_resolver's native order is (width, height, hook);
        # MASTER production traversal is hook, A, B, A, B, hook.
        width, height, hook = values
        return [hook, width, height, width, height, hook]
    return None


def _proxy_turn_angles_deg(
    shape_code: str,
    segments: Sequence[float],
    reduction_angle_deg: float | None,
) -> tuple[float, ...] | None:
    """Ъглите на завоите за повишена proxy форма, или ``None`` ако не могат
    да бъдат доказани.

    Повечето форми в този диалект завиват само под прав ъгъл и все в една
    посока: затвореното стреме има пет такива завоя, U-прътът два.

    ``F002`` е изключението и точно то счупи един реален експорт: редукцията
    завива ВЪРХУ наклона и после обратно ОТ него, затова двата ъгъла са с
    противоположни знаци. ``excel_export._validate_f002_turn_consistency``
    изисква точно това.

    Големината НЕ идва от начертаната посока. Чертежът е схематичен и дава
    20.616 cm за участък, надписан 21 -- ъгъл оттам би дал отместване 5.095
    вместо кръглите 5, които проектантът е дал (собственик, 2026-09-13).
    Ъгълът се извежда от надписаните числа в
    ``proxy_adapter.reduction_offset_angle_deg`` и се подава тук готов; без
    него се връща ``None`` и позицията не се повишава.
    """

    code = str(shape_code or "").strip().upper()
    count = max(0, len(segments) - 1)
    if code == "F007":
        # ENGINE: „кривината е схематична" -- и всичките осем решения на
        # собственика записаха празен списък със завои.
        return ()
    if code != "F002":
        return (90.0,) * count
    if reduction_angle_deg is None:
        return None
    crank = float(reduction_angle_deg)
    if not math.isfinite(crank) or crank <= 0.0 or crank >= 90.0:
        return None
    return (crank, -crank)


_PROXY_GEOMETRY_OWNER_RE = re.compile(r"^PXG_([^_]+)_\d+_\d+$")


def _candidates_include_own_proxy_geometry(
    row: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> bool:
    """Whether the row's competing candidates include geometry owned by this
    annotation's own proxy parent.

    Fail-closed: an unknown parent is treated as "ours", so the row keeps its
    manual review rather than promoting on an unverifiable assumption.
    """

    parent = str(raw.get("parent_proxy_handle") or "").strip()
    if not parent:
        return True
    candidates = [
        str(value) for value in (row.get("candidate_handles") or []) if str(value)
    ]
    if not candidates:
        return False
    return any(
        (match := _PROXY_GEOMETRY_OWNER_RE.match(candidate)) is not None
        and match.group(1) == parent
        for candidate in candidates
    )


def _clear_superseded_segment_block(row: dict) -> None:
    """Снема забраната за експорт, вдигната от ДРУГ, вече изхвърлен източник.

    ``_block_authoritative_segment_evidence`` изтрива сегментите и вдига
    ``production_export_blocked``, когато опитът на matcher-а да ги изведе е
    негоден. Това е правилно. Но когато после декларацията на проектанта
    достави свои сегменти -- независимо преверени в ``auto_gate`` --
    забраната описва данни, които вече не съществуват.

    Реален случай (собственик, 2026-09-13, проект ``s-7``, стена Ш 3-25/600):
    „17N12x415 (общо 34)" беше разпозната вярно като F003 [30, 355, 30], но
    остана извън описа заради флаг от неуспешен опит по чужд далечен кандидат
    -- 34 бр. Ø12 x 415 см, около 125 кг, липсваха от спецификацията.

    Маха се САМО кодът на снетата забрана. Всичко останало, което редът носи
    (например ``FAR_ASSOCIATION``), остава на записа за оператора.
    """

    reason = str(row.pop("production_export_block_reason", "") or "").strip()
    row.pop("production_export_blocked", None)
    if not reason:
        return
    row["review_reason_codes"] = [
        str(value)
        for value in (row.get("review_reason_codes") or [])
        if str(value) != reason
    ]
    if str(row.get("f006_production_reason") or "") == reason:
        row.pop("f006_production_status", None)
        row.pop("f006_production_reason", None)


def _apply_proxy_declared_shape_auto_promotion(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
    shape_definitions: Sequence[ShapeDefinition],
) -> int:
    """Owner-requested 2026-09-12 ("целим програмата ни да е професионална с
    минимално инженерно вмешателство"): promote the strongest declared-shape
    rows from a suggestion to a real production result, so they stop
    queueing for a manual review that adds no safety.

    Evidence used, all of it already proven independently of this function:

    * count / diameter / length -- the designer's own structured proxy text
      (this dialect's quantities have always been taken from the text; a
      manual review never re-measured them);
    * shape FAMILY -- real structural bend-arc geometry, 165/165 on real
      data (see proxy_adapter.PROXY_DECLARED_SHAPE_CODE);
    * segments -- the designer's own declared breakdown, re-checked here to
      close the declared total exactly.

    Deliberately narrow, and deliberately NOT the final word: only HIGH
    resolver confidence, only shapes whose production form is verified
    (``_PROXY_AUTO_PROMOTABLE_SHAPES``), and never where the geometric
    matcher disagrees (FAR_ASSOCIATION / ownership conflict) or already
    proved its own shape. ``auto_gate.validate_production_segment_evidence``
    re-proves every one of these conditions from the persisted row before
    AUTO accepts it, so widening this writer alone cannot widen AUTO.
    """

    names_by_code = {
        str(shape.code).strip().upper(): shape.name for shape in shape_definitions
    }
    applied = 0
    for row in rows:
        reasons = {
            str(value).strip().upper()
            for value in (row.get("review_reason_codes") or [])
        }
        blockers = {
            str(value).strip().upper()
            for value in (row.get("matcher_blocker_codes") or [])
        }
        # MASTER-shape ambiguity is genuine uncertainty about which shape
        # applies, not a claim about a candidate, so candidate ownership
        # (checked below) cannot clear it. ``global_conflict`` despite its
        # name IS a candidate claim -- see the competing_claim set below.
        if row.get("master_conflict") or "AMBIGUOUS_MASTER" in reasons | blockers:
            continue
        handle = str(row.get("annotation_handle") or "")
        annotation = annotations_by_handle.get(handle)
        if annotation is None:
            continue
        raw = annotation.raw or {}
        # A competing geometric claim only counts when it is about THIS
        # annotation's own exploded proxy geometry. A distant or contested
        # match on an unrelated entity cannot overrule a declaration that was
        # measured entirely on our own proxy -- the owner's own rule is that a
        # label always sits next to its shape, so a far match is not ours.
        # ``auto_gate`` re-proves this from the row alone before AUTO accepts.
        competing_claim = (
            "FAR_ASSOCIATION" in reasons
            or "FAR_ASSOCIATION" in blockers
            or "GEOMETRY_OWNERSHIP_CONFLICT" in blockers
            or bool(row.get("geometry_owner_conflict"))
            or bool(row.get("global_conflict"))
        )
        if competing_claim and _candidates_include_own_proxy_geometry(row, raw):
            continue
        if raw.get("declared_shape_source") != PROXY_DECLARED_SHAPE_CODE:
            continue
        if str(raw.get("declared_shape_confidence") or "").strip().upper() != "HIGH":
            continue
        declared_code = str(raw.get("declared_shape_code") or "").strip().upper()
        matcher_code = str(row.get("master_shape_code") or "").strip().upper()
        if matcher_code and matcher_code != declared_code:
            # Two independent sources claim different families. A STRONG
            # geometric match is never overruled silently -- the row goes to
            # a human with both claims on the record, and is pulled out of
            # AUTO so a contradicted shape can never export unreviewed.
            # A weak match (manual-review confidence, typically a
            # coincidental length hit on a generic layer that carries no
            # diameter for geometry_candidates.py to reject against) loses
            # to the arc proof: a straight bar cannot have structural bends.
            # Изключение по ФИЗИКА, не по сила на съвпадението: прът с реални
            # огъващи дъги НЕ е прав прът. Реален случай (проект ``s-7``):
            # matcher-ът засичаше едно право парче от СОБСТВЕНАТА геометрия на
            # огънат прът (``PXG_<родител>_2_0``) и го обявяваше за силен F001;
            # противоречието сваляше реда на ръчен преглед и декларацията
            # оставаше неизползвана. Едно право рамо на прът с кука не е прът.
            straight_claim_refuted_by_arcs = (
                matcher_code == "F001"
                and isinstance(raw.get("declared_shape_bend_arc_count"), int)
                and not isinstance(raw.get("declared_shape_bend_arc_count"), bool)
                and int(raw.get("declared_shape_bend_arc_count") or 0) > 0
            )
            if (
                not straight_claim_refuted_by_arcs
                and str(row.get("batch_status") or "") in _PROXY_STRONG_BATCH_STATUSES
            ):
                if _PROXY_SHAPE_CONTRADICTION_CODE not in (
                    row.get("review_reason_codes") or []
                ):
                    row["review_reason_codes"] = [
                        *list(row.get("review_reason_codes") or []),
                        _PROXY_SHAPE_CONTRADICTION_CODE,
                    ]
                    row["batch_status"] = "ЗА РЪЧЕН ПРЕГЛЕД"
                    row["candidate_notes"] = [
                        *list(row.get("candidate_notes", [])),
                        (
                            f"ПРОТИВОРЕЧИЕ: геометрията сочи {matcher_code}, а "
                            f"собствената декларирана разбивка с "
                            f"{raw.get('declared_shape_bend_arc_count')} реални "
                            f"огъващи дъги сочи {declared_code}. Изисква "
                            "инженерно решение — нито едното не се приема "
                            "автоматично."
                        ),
                    ]
                continue
        elif matcher_code and row.get("production_segments_cm"):
            # Семейството съвпада -- но „съгласни за ФОРМАТА" не значи
            # „съгласни за РАЗМЕРИТЕ". Matcher-ът често вече е сложил
            # сегменти, изведени от котировки наблизо
            # (``F003_SEGMENTS_DERIVED``), докато проектантът е декларирал
            # собствена разбивка на същия прът.
            #
            # Реален случай, проект ``s-7``, 4 позиции (68N10x230, 68N10x240
            # и два пъти 34N10x240): в описа влизаше [12.5, 215, 12.5] от
            # една котировка, а декларацията казваше [20, 200, 20]. И двете
            # затварят 240 cm, тоест сборът не издава коя е вярната -- но
            # прътът се реже по едната.
            #
            # Собственик, 2026-09-13: „позицията е 20, 200, 20 няма спор щом
            # съм я дал като решение."
            #
            # Затова редът се прескача само когато двете разбивки СЪВПАДАТ.
            # Различават ли се, декларацията продължава надолу -- където
            # долните проверки ѝ искат същото, което искат от всяка друга:
            # да затваря декларираната дължина и да дава валидни MASTER
            # параметри. Не побеждава защото е декларация, а защото се
            # доказва; ако не се докаже, редът си остава с това, което вече
            # има.
            declared_breakdown = _proxy_production_segments(
                declared_code, raw.get("declared_shape_segments_cm") or ()
            )
            if declared_breakdown is None or _segment_vectors_agree(
                row.get("production_segments_cm"), declared_breakdown
            ):
                continue
        if declared_code not in _PROXY_AUTO_PROMOTABLE_SHAPES:
            continue
        arc_count = raw.get("declared_shape_bend_arc_count")
        if not isinstance(arc_count, int) or isinstance(arc_count, bool) or arc_count < 0:
            continue
        leg_directions = raw.get("declared_shape_leg_directions_deg") or ()
        if arc_count > 0:
            if declared_code == "F001":
                continue
        elif declared_code != "F001":
            # Zero ARCs no longer means straight: this dialect draws its
            # bends as sharp corners, so a three-part shape is proven by the
            # measured leg directions instead. The gate re-derives the same
            # verdict from the recorded angles.
            if three_leg_shape_from_directions(
                leg_directions, raw.get("declared_shape_segments_cm") or ()
            ) != declared_code:
                continue
        segments = _proxy_production_segments(
            declared_code, raw.get("declared_shape_segments_cm") or ()
        )
        if segments is None:
            continue
        total = row.get("production_length_cm")
        if total is None:
            total = row.get("declared_length_cm")
        try:
            total_value = float(total)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(total_value) or total_value <= 0:
            continue
        if abs(math.fsum(segments) - total_value) > _PROXY_AUTO_TOTAL_TOLERANCE_CM:
            continue

        turn_angles = _proxy_turn_angles_deg(
            declared_code, segments, raw.get("declared_shape_reduction_angle_deg")
        )
        if turn_angles is None:
            continue

        row["master_shape_code"] = declared_code
        if declared_code in names_by_code:
            row["master_shape_name"] = names_by_code[declared_code]
        row["production_segments_cm"] = list(segments)
        row["canonical_turn_angles_deg"] = list(turn_angles)
        row["production_segment_evidence"] = {
            "source": PROXY_DECLARED_SHAPE_CODE,
            "shape_code": declared_code,
            "segments": list(segments),
            "confidence": raw.get("declared_shape_confidence"),
            "bend_arc_count": arc_count,
            "declared_segments_cm": [
                float(value) for value in (raw.get("declared_shape_segments_cm") or ())
            ],
            "leg_directions_deg": [float(value) for value in leg_directions],
        }
        row["candidate_notes"] = [
            *list(row.get("candidate_notes", [])),
            (
                "ДЕКЛАРИРАНА ФОРМА, ПОТВЪРДЕНА С ДЪГИ: производствените "
                "сегменти идват от собствената разбивка на проектанта, а "
                "семейството на формата е доказано независимо от реалната "
                "дъгова геометрия"
            ),
        ]
        _clear_superseded_segment_block(row)
        applied += 1
    return applied


#: „Форма от котите до надписа" (виж ``printed_detail_dimensions``): двата
#: източника на доказателство. ``auto_gate.validate_production_segment_evidence``
#: преизвежда всеки от тях от записания ред, независимо от този писател.
PRINTED_DETAIL_SEGMENT_SOURCE = "LOCAL_DETAIL_PRINTED_DIMENSIONS"
PRINTED_SPECIFICATION_SEGMENT_SOURCE = "STIRRUP_SPECIFICATION_PRINTED_DIMENSIONS"
PRINTED_REPEATED_LABEL_SEGMENT_SOURCE = "REPEATED_LABEL_PRINTED_DIMENSIONS"

#: Стремето се огъва под прав ъгъл — и затвореното, и отвореното. Проверено
#: върху начертаните фигури в „Сграда бета\Плоча_кота 14.90": всички участъци
#: са хоризонтални или вертикални.
_PRINTED_DETAIL_TURN_ANGLES_DEG = (90.0, 90.0, 90.0, 90.0)

#: Само детайл, обявен за стреме (``стр.:``/``стремена``), носи този прочит.
_PRINTED_DETAIL_LABEL_SHAPE_CODE = "F006"


def _printed_detail_row_is_open(row: Mapping[str, Any]) -> bool:
    """Позиция, на която правилото има право да пише.

    Умишлено тясно: само пълен надпис БЕЗ геометричен кандидат и без вече
    доказана форма или участъци. Правилото допълва празното, никога не
    отменя друг източник.
    """

    if str(row.get("annotation_class") or "") != "ACTIONABLE":
        return False
    if [str(value) for value in (row.get("candidate_handles") or []) if str(value)]:
        return False
    if str(row.get("master_shape_code") or "").strip():
        return False
    if row.get("production_segments_cm") or row.get("production_segment_evidence"):
        return False
    if row.get("master_ambiguous_shape_codes") or row.get("master_conflict"):
        return False
    return (
        _positive_int(row.get("annotation_count")) is not None
        and _positive_float(row.get("diameter_mm")) is not None
        and _positive_float(row.get("declared_length_cm")) is not None
    )


def _printed_detail_dimension_records(
    dimensions: Sequence[Any],
) -> list[dict[str, Any]]:
    return [
        {"handle": item.handle, "measurement_cm": item.value_cm}
        for item in dimensions
    ]


def _install_printed_detail_shape(
    row: dict,
    shape: PrintedShape,
    *,
    source: str,
    names_by_code: Mapping[str, str],
    evidence_details: Mapping[str, Any],
    note: str,
) -> bool:
    """Записва прочетената форма и участъци заедно с доказателството."""

    row["master_shape_code"] = shape.shape_code
    if shape.shape_code in names_by_code:
        row["master_shape_name"] = names_by_code[shape.shape_code]
    row["canonical_segment_lengths_cm"] = list(shape.segments)
    row["canonical_turn_angles_deg"] = list(_PRINTED_DETAIL_TURN_ANGLES_DEG)
    if not _install_authoritative_production_segments(
        row,
        tuple(shape.segments),
        source=source,
        evidence_details=evidence_details,
    ):
        # Провалът вече е записан като блокировка; формата не остава сама,
        # защото участъците са частта, която доказва точно нея.
        row.pop("master_shape_code", None)
        row.pop("master_shape_name", None)
        row.pop("canonical_segment_lengths_cm", None)
        row.pop("canonical_turn_angles_deg", None)
        return False
    if shape.shape_code == "F006":
        row["f006_production_status"] = "F006_PRODUCTION_DERIVED"
        row["f006_production_reason"] = "PRINTED_DETAIL_DIMENSIONS"
        row["f006_printed_dimensions_resolved"] = True
        row["production_export_blocked"] = False
        row["production_export_block_reason"] = ""
    row["dimension_derived_evidence"] = {
        "shape_code": shape.shape_code,
        "segments": list(shape.printed_values_cm),
        "source": "НАДПИСАНИ КОТИ НА ДЕТАЙЛА",
        "confidence": "HIGH",
        "measured_dimensions": len(shape.printed_values_cm),
        "computed_dimensions": 0 if shape.computed_value_cm is None else 1,
    }
    # По-ранното ПРЕДЛОЖЕНИЕ по близки DIMENSION обекти вече противоречи на
    # доказан прочит. Оставено на реда, то показва на оператора друга форма и
    # други размери от тези в панела. Маха се само когато формата се различава.
    suggested = str(row.get("suggested_shape_code") or "").strip().upper()
    if suggested and suggested != shape.shape_code:
        row.pop("suggested_shape_code", None)
        row.pop("suggested_segments_cm", None)
        row.pop("suggested_evidence", None)
    row["candidate_notes"] = [
        *list(row.get("candidate_notes", [])),
        note,
    ]
    row["analysis_warnings"] = [
        *list(row.get("analysis_warnings", [])),
        note,
    ]
    return True


def _apply_printed_detail_dimension_segments(
    rows: Sequence[dict],
    *,
    entities: Sequence[DxfEntityRecord],
    annotations_by_handle: Mapping[str, DxfEntityRecord],
    policy: RecognitionPolicy,
    shape_definitions: Sequence[ShapeDefinition],
) -> int:
    """Форма и участъци от надписаните коти на локалния детайл ``стр.``.

    Собственик, 19.09.2026: „действай". Позиция с пълен надпис, без
    геометричен кандидат, чийто локален детайл е обявен за стреме и носи
    надписани размери, получава формата и участъците от тях — вместо да
    стои с празен панел. Правилото и границите му са описани в
    ``lion_rebar/dxf/printed_detail_dimensions.py``.

    Три пътя, едно четене:

    * собственият детайл на позицията (``11N8x390``, ``60N8x182``);
    * СПЕЦИФИКАЦИЯ ``стр.: N8x166`` — надпис с Ø и дължина без брой, чиито
      коти затварят нейната дължина. Тя важи за позициите със същия Ø и
      същата дължина (щурците: ``32N8x166``/``29N8x166``/``28N8x166``),
      защото това е връзката, която проектантът е НАПИСАЛ, а не близост;
    * СЪЩИЯТ НАДПИС, вече прочетен от своите коти — при пълно единодушие и
      само когато позицията също носи детайл ``стр.``, а собствените ѝ коти
      не противоречат на пренесените участъци.

    Отказът е част от правилото: бордът ``1180N8x190`` надписва 189 при
    L=190 и остава при оператора с обяснение. Позиция, чиито коти почти
    затварят, НЕ се спасява по другите два пътя — числата ѝ казват друго.
    """

    if not policy.printed_detail_dimension_rule_enabled:
        return 0
    annotations = tuple(annotations_by_handle.values())
    if not annotations:
        return 0
    index = build_local_detail_index(
        annotations,
        entities,
        policy=policy,
        include_specification_annotations=True,
        dimension_max_distance_raw=policy.printed_detail_dimension_max_distance_raw,
    )
    if not index.assignments:
        return 0
    entity_map = {str(entity.handle): entity for entity in entities}
    names_by_code = {
        str(shape.code).strip().upper(): shape.name for shape in shape_definitions
    }
    layer_aliases = policy.printed_detail_dimension_layer_aliases

    def read_shape(handle: str, total_cm: float):
        """Прочита формата на един детайл: (форма, коти) или (None, коти)."""

        assignment = index.assignment_for(handle)
        if assignment is None or assignment.shape_code != _PRINTED_DETAIL_LABEL_SHAPE_CODE:
            return None, (), None
        dimensions = printed_dimensions_for_detail(
            assignment.exclusive_dimension_handles,
            entity_map,
            layer_aliases=layer_aliases,
        )
        shape = shape_from_printed_dimensions(
            [item.value_cm for item in dimensions],
            total_cm,
            hook_min_cm=policy.printed_detail_hook_min_cm,
            hook_max_cm=policy.printed_detail_hook_max_cm,
        )
        return shape, dimensions, assignment

    # ---- СПЕЦИФИКАЦИИ: надпис с Ø и дължина без брой -------------------
    specifications: dict[tuple[int, float], dict[str, Any]] = {}
    rejected: set[tuple[int, float]] = set()
    for row in rows:
        if str(row.get("annotation_class") or "") != "MISSING_COUNT":
            continue
        diameter = _positive_float(row.get("diameter_mm"))
        length = _positive_float(row.get("declared_length_cm"))
        handle = str(row.get("annotation_handle") or "")
        if diameter is None or length is None or not handle:
            continue
        shape, dimensions, assignment = read_shape(handle, length)
        if shape is None or assignment is None:
            continue
        key = (int(round(diameter)), round(length, 3))
        if key in rejected:
            continue
        entry = {
            "handle": handle,
            "text": str(row.get("annotation_text") or ""),
            "diameter_mm": diameter,
            "length_cm": length,
            "shape": shape,
            "dimensions": dimensions,
            "assignment": assignment,
        }
        existing = specifications.get(key)
        if existing is None:
            specifications[key] = entry
        elif not _segments_equivalent(
            tuple(existing["shape"].segments), tuple(shape.segments)
        ):
            # Две спецификации за един и същ Ø и дължина, но с различни
            # размери: двусмислието е отказ, не избор по ред на файла.
            rejected.add(key)
            specifications.pop(key, None)
    # Вече доказана форма на същия Ø и дължина опровергава спецификацията.
    for row in rows:
        master = str(row.get("master_shape_code") or "").strip().upper()
        diameter = _positive_float(row.get("diameter_mm"))
        length = _positive_float(row.get("declared_length_cm"))
        if not master or diameter is None or length is None:
            continue
        key = (int(round(diameter)), round(length, 3))
        entry = specifications.get(key)
        if entry is not None and entry["shape"].shape_code != master:
            rejected.add(key)
            specifications.pop(key, None)

    applied = 0
    # ПЪРВО МИНАВАНЕ: всяка позиция със СВОИ надписани коти.
    proven_by_label: dict[str, list[dict[str, Any]]] = {}
    remaining: list[dict] = []
    for row in rows:
        if not _printed_detail_row_is_open(row):
            continue
        handle = str(row.get("annotation_handle") or "")
        declared = _positive_float(row.get("declared_length_cm"))
        if not handle or declared is None:
            continue
        shape, dimensions, assignment = read_shape(handle, declared)
        if shape is None:
            if assignment is not None and dimensions:
                miss = near_miss_note(
                    [item.value_cm for item in dimensions], declared
                )
                if miss:
                    row["candidate_notes"] = [
                        *list(row.get("candidate_notes", [])),
                        miss,
                    ]
                    row["analysis_warnings"] = [
                        *list(row.get("analysis_warnings", [])),
                        miss,
                    ]
                    continue
            remaining.append(row)
            continue
        if assignment is not None:
            note = (
                f"{PRINTED_DETAIL_SEGMENT_SOURCE}: {shape.shape_code} от "
                f"надписаните коти на детайла "
                f"({' + '.join(f'{value:g}' for value in shape.segments)} = "
                f"{shape.total_cm:g} cm при L={declared:g} cm) — "
                f"{shape.reading}; надпис {assignment.label_handle} "
                f"({assignment.label_text!r}) на "
                f"{assignment.label_distance_raw:.0f} чертожни единици"
            )
            if _install_printed_detail_shape(
                row,
                shape,
                source=PRINTED_DETAIL_SEGMENT_SOURCE,
                names_by_code=names_by_code,
                evidence_details={
                    "label_handle": assignment.label_handle,
                    "label_text": assignment.label_text,
                    "label_distance_raw": float(assignment.label_distance_raw),
                    "dimension_handles": [item.handle for item in dimensions],
                    "dimension_measurements_cm": _printed_detail_dimension_records(
                        dimensions
                    ),
                    "printed_values_cm": list(shape.printed_values_cm),
                    "computed_value_cm": shape.computed_value_cm,
                },
                note=note,
            ):
                applied += 1
                label = " ".join(str(row.get("annotation_text") or "").split()).upper()
                proven_by_label.setdefault(label, []).append(
                    {
                        "handle": handle,
                        "shape": shape,
                        "dimensions": dimensions,
                        "assignment": assignment,
                    }
                )
            continue

    # ВТОРО МИНАВАНЕ: позиции, чиито СОБСТВЕНИ коти не стигат.
    for row in remaining:
        handle = str(row.get("annotation_handle") or "")
        declared = _positive_float(row.get("declared_length_cm"))
        diameter = _positive_float(row.get("diameter_mm"))
        if not handle or declared is None or diameter is None:
            continue
        own_shape, own_dimensions, own_assignment = read_shape(handle, declared)
        if own_shape is not None:  # pragma: no cover - вече е решен по-горе
            continue
        label = " ".join(str(row.get("annotation_text") or "").split()).upper()
        siblings = proven_by_label.get(label, [])
        if own_assignment is not None and siblings:
            # Същият надпис, вече прочетен от СВОИТЕ коти на друг детайл.
            # Реален случай: три борда ``11N8x390``; при третия котата на
            # куката е на еднакво разстояние от чужд детайл и затова не му
            # принадлежи изключително. Прочитът се пренася само при пълно
            # единодушие и само ако собствените коти на позицията не му
            # противоречат.
            readings = {
                tuple(item["shape"].segments) for item in siblings
            }
            codes = {item["shape"].shape_code for item in siblings}
            if len(readings) != 1 or len(codes) != 1:
                continue
            sibling = siblings[0]
            shape = sibling["shape"]
            own_values = [item.value_cm for item in own_dimensions]
            if any(
                not any(
                    abs(value - segment) <= 0.001 for segment in shape.segments
                )
                for value in own_values
            ):
                continue
            note = (
                f"{PRINTED_REPEATED_LABEL_SEGMENT_SOURCE}: {shape.shape_code} по "
                f"вече прочетения същ надпис {sibling['handle']} — "
                f"{' + '.join(f'{value:g}' for value in shape.segments)} = "
                f"{shape.total_cm:g} cm; собственият детайл на позицията също е "
                f"стреме ({own_assignment.label_handle}, "
                f"{own_assignment.label_text!r})"
                + (
                    f", а собствените ѝ коти "
                    f"{', '.join(f'{value:g}' for value in own_values)} не му "
                    "противоречат"
                    if own_values
                    else ""
                )
            )
            if _install_printed_detail_shape(
                row,
                shape,
                source=PRINTED_REPEATED_LABEL_SEGMENT_SOURCE,
                names_by_code=names_by_code,
                evidence_details={
                    "specification_handle": sibling["handle"],
                    "specification_text": str(row.get("annotation_text") or ""),
                    "specification_diameter_mm": diameter,
                    "specification_length_cm": declared,
                    "label_handle": own_assignment.label_handle,
                    "label_text": own_assignment.label_text,
                    "label_distance_raw": float(own_assignment.label_distance_raw),
                    "sibling_label_handle": sibling["assignment"].label_handle,
                    "dimension_handles": [
                        item.handle for item in sibling["dimensions"]
                    ],
                    "dimension_measurements_cm": _printed_detail_dimension_records(
                        sibling["dimensions"]
                    ),
                    "printed_values_cm": list(shape.printed_values_cm),
                    "computed_value_cm": shape.computed_value_cm,
                    "own_printed_values_cm": own_values,
                    "own_dimension_handles": [
                        item.handle for item in own_dimensions
                    ],
                },
                note=note,
            ):
                applied += 1
            continue
        # ---- път през спецификацията ---------------------------------
        entry = specifications.get((int(round(diameter)), round(declared, 3)))
        if not entry:
            continue
        shape = entry["shape"]
        spec_dimensions = entry["dimensions"]
        spec_assignment = entry["assignment"]
        note = (
            f"{PRINTED_SPECIFICATION_SEGMENT_SOURCE}: {shape.shape_code} по "
            f"спецификацията {entry['handle']} ({entry['text']!r}) — "
            f"{' + '.join(f'{value:g}' for value in shape.segments)} = "
            f"{shape.total_cm:g} cm; същият Ø и същата дължина като позицията; "
            f"{shape.reading}; надпис {spec_assignment.label_handle} "
            f"({spec_assignment.label_text!r})"
        )
        if _install_printed_detail_shape(
            row,
            shape,
            source=PRINTED_SPECIFICATION_SEGMENT_SOURCE,
            names_by_code=names_by_code,
            evidence_details={
                "specification_handle": entry["handle"],
                "specification_text": entry["text"],
                "specification_diameter_mm": entry["diameter_mm"],
                "specification_length_cm": entry["length_cm"],
                "label_handle": spec_assignment.label_handle,
                "label_text": spec_assignment.label_text,
                "label_distance_raw": float(spec_assignment.label_distance_raw),
                "dimension_handles": [item.handle for item in spec_dimensions],
                "dimension_measurements_cm": _printed_detail_dimension_records(
                    spec_dimensions
                ),
                "printed_values_cm": list(shape.printed_values_cm),
                "computed_value_cm": shape.computed_value_cm,
            },
            note=note,
        ):
            applied += 1
    return applied


def _apply_operator_area_group_labels(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
    operator_areas: Sequence[Any] = (),
) -> int:
    """Групов етикет от рамка, начертана преди анализа.

    Собственик, 2026-09-13: „да направим рамките преди теста на чертежа, за
    да може програмата да го прочете."

    При колоните ``proxy_adapter`` сам намира затворения контур и заглавието
    му. При шайбите такива контури няма — заглавията („Ш 3-25/600") стоят, но
    нищо не ги свързва с позициите, затова описът излизаше накуп (проверено
    върху ``s-7``: 0 от 57 одобрени позиции с групов етикет). Операторската
    област е ръчният заместител на липсващия контур.

    Нарочно САМО върху празен етикет: областта попълва там, където
    автоматиката мълчи, и никога не отменя доказана група. Позициите се
    намират наново при всеки анализ, затова добавена позиция влиза в групата
    си сама.
    """

    if not operator_areas:
        return 0
    from lion_rebar.export.operator_areas import KIND_GROUP, area_for_point

    applied = 0
    for row in rows:
        if str(row.get("column_group_label") or ""):
            continue
        handle = str(row.get("annotation_handle") or "")
        if not handle:
            continue
        annotation = annotations_by_handle.get(handle)
        if annotation is None:
            continue
        anchor = _entity_insert(annotation)
        if anchor is None:
            continue
        area = area_for_point(
            operator_areas, anchor[0], anchor[1], kind=KIND_GROUP
        )
        if area is None:
            continue
        row["column_group_label"] = area.title_text
        applied += 1
    return applied

def _apply_proxy_column_group_labels(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
) -> int:
    """Owner-requested 2026-09-12: carry the koloni column-group header
    ("К23, К24 (35/35) - 2бр.") onto the review/export row.

    The label is already resolved in ``proxy_adapter.py`` -- including the
    detail-box containment rule that decides which group an annotation
    really belongs to -- but until now it lived only on the annotation
    record, so the Excel sheet could not group positions the way the
    drawing itself does (one section per column group, its own positions
    listed under it). Pure metadata: nothing here affects quantities,
    shapes or approval."""

    applied = 0
    for row in rows:
        handle = str(row.get("annotation_handle") or "")
        if not handle:
            continue
        annotation = annotations_by_handle.get(handle)
        if annotation is None:
            continue
        raw = annotation.raw or {}
        label = raw.get("column_group_label")
        if not label:
            continue
        row["column_group_label"] = str(label)
        count = raw.get("column_group_count")
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            row["column_group_count"] = count
        source_count = raw.get("column_group_source_count")
        if isinstance(source_count, int) and not isinstance(source_count, bool):
            row["column_group_source_count"] = source_count
        applied += 1
    return applied


def _apply_proxy_reference_geometry_fallback(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
) -> int:
    """Owner-requested 2026-09-11/12: surface an annotation's own attached
    (reference-only, unverified) proxy geometry as ``reference_geometry_
    handles`` so the operator has SOMETHING to look at automatically when
    there is no real geometric candidate at all (``candidate_handles``
    empty). Confirmed live: this is the EXACT set of fragments an operator
    otherwise has to click through one by one via manual geometry
    selection.

    Also applies when the ONLY candidate is FAR_ASSOCIATION-flagged
    (owner-confirmed 2026-09-12, verified across BOTH real files: 0 of 33
    FAR_ASSOCIATION rows in koloni+shaibi ever have their far candidate
    overlap the annotation's own reference geometry -- every one points at
    a completely unrelated proxy elsewhere on the sheet. The owner
    independently confirmed the same from the drawings themselves: real
    label-to-geometry pairs in this dialect are always drawn close
    together, never far apart. A FAR_ASSOCIATION candidate is therefore
    strictly less trustworthy than the annotation's own reference geometry
    when both exist, not an equally-valid alternative).

    Deliberately narrow and safe:
    - never touches ``candidate_handles`` itself (that field feeds
      ownership-conflict detection, review grouping and export -- all of
      which must keep meaning "a proven geometric match"); a FAR_ASSOCIATION
      row keeps its far candidate available via "Всички кандидати" even
      after this fallback also populates ``reference_geometry_handles`` --
      only the review_center.py quick-preview default prefers the latter;
    - only applies when the source association is
      ``PROXY_DECLARED_LENGTH_CODE`` (the "no unique geometric length
      proof" fallback already used for arm_koloni/arm_shaibi and any
      other proxy dialect that reaches it);
    - carries no length/distance claim -- the UI must render it visually
      distinct from a proven candidate (see
      ui/main_window.py::_review_preview_reference_geometry)."""

    applied = 0
    for row in rows:
        has_only_far_candidate = "FAR_ASSOCIATION" in (row.get("review_reason_codes") or [])
        if row.get("candidate_handles") and not has_only_far_candidate:
            continue
        handle = str(row.get("annotation_handle") or "")
        if not handle:
            continue
        annotation = annotations_by_handle.get(handle)
        if annotation is None:
            continue
        raw = annotation.raw or {}
        if raw.get("association_code") != PROXY_DECLARED_LENGTH_CODE:
            continue
        reference_handles = [
            str(value) for value in (raw.get("proxy_geometry_handles") or []) if str(value)
        ]
        if not reference_handles:
            continue
        row["reference_geometry_handles"] = reference_handles
        applied += 1
    return applied


def _apply_f006_printed_production_dimensions(
    rows: Sequence[dict],
    *,
    entities: Sequence[DxfEntityRecord],
    shape_definitions: Sequence[ShapeDefinition],
) -> None:
    """Replace schematic F006 length with a proven printed MASTER equation."""

    shape = next(
        (
            item
            for item in shape_definitions
            if item.active and str(item.code).strip().upper() == "F006"
        ),
        None,
    )
    if shape is None:
        return
    entity_map = {str(entity.handle): entity for entity in entities}
    for row in rows:
        if (
            row.get("annotation_class") != "ACTIONABLE"
            or str(row.get("master_shape_code") or "").strip().upper() != "F006"
        ):
            continue
        declared = _positive_float(row.get("declared_length_cm"))
        handles = [str(value) for value in row.get("candidate_handles", []) if str(value)]
        if declared is None or not handles:
            continue
        link = dict(row)
        link["geometry_handles"] = handles
        review_codes = {str(value) for value in row.get("review_reason_codes", [])}
        hooked = "HOOKED_CLOSURE" in review_codes or "HOOKED_CLOSURE" in {
            str(value) for value in row.get("matcher_blocker_codes", [])
        } or "HOOKED_CLOSURE" in str(row.get("matcher_blocker_codes") or "")
        ambiguous = bool(row.get("master_ambiguous_shape_codes")) or bool(
            row.get("master_ambiguity")
        )
        conflicted = bool(row.get("global_conflict")) or bool(
            row.get("ownership_conflict")
        )
        declared_difference = _positive_float(row.get("numeric_difference"))
        total_in_tolerance = (
            declared_difference is not None and declared_difference <= 2.1
        )

        def _production(status: str, reason: str, note: str) -> None:
            """Класифицира само production слоя; form recognition остава непокътнат."""

            row["f006_production_status"] = status
            row["f006_production_reason"] = reason
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                note,
            ]
            if status == "F006_PRODUCTION_REVIEW":
                # Формата е сигурна; производствените размери не са. Блокира
                # се само production export-ът, не разпознаването.
                row["production_export_blocked"] = True
                row["production_export_block_reason"] = reason

        # Recovery 5.3 rounded F006 candidates already carry a unique printed
        # six-segment vector derived from locally-owned width/height/hook
        # texts.  Re-running the legacy straight-line canonicalizer would see
        # ten disconnected DXF objects (4 LINE + 4 ARC + 2 keys) and falsely
        # reject the proven result.  Accept only the exact, auditable vector
        # produced by the dedicated detector; all other cases stay on the
        # historical fail-closed path below.
        if "ЗАКРЪГЛЕНО СТРЕМЕ" in str(row.get("candidate_basis") or "").upper():
            canonical = tuple(
                float(value)
                for value in row.get("canonical_segment_lengths_cm", [])
            )
            if len(canonical) == 6 and all(value > 0.0 for value in canonical):
                difference = abs(sum(canonical) - declared)
                if difference <= 2.1:
                    row["f006_production_status"] = "F006_PRODUCTION_EXACT"
                    row["f006_printed_dimensions_resolved"] = True
                    row["effective_geometry_length_cm"] = sum(canonical)
                    row["numeric_difference"] = difference
                    row["canonical_turn_angles_deg"] = [90.0, 90.0, 90.0, 90.0]
                    note = (
                        "F006_PRODUCTION_EXACT: закръглената DXF топология е "
                        "нормализирана към локално доказани производствени "
                        f"сегменти {' + '.join(f'{value:g}' for value in canonical)} "
                        f"= {sum(canonical):g} cm при L={declared:g} cm."
                    )
                    row["candidate_notes"] = [
                        *list(row.get("candidate_notes", [])),
                        note,
                    ]
                    row["analysis_warnings"] = [
                        *list(row.get("analysis_warnings", [])),
                        note,
                    ]
                    continue

        try:
            segments, turns = _segments_for_link(
                link,
                shape=shape,
                entity_map=entity_map,
                production_length_cm=declared,
            )
        except (ExcelExportError, TypeError, ValueError) as exc:
            reason = "F006_PRINTED_DIMENSIONS_MISSING"
            # Структуриран дискриминатор вместо крехко сравнение на текст:
            # само известният asymmetric-hook случай (F006AsymmetricHookError,
            # хвърлян на точно едно място в _canonicalize_for_master) може да
            # запази силната форма. Всеки друг exception е fail-closed.
            asymmetric = isinstance(exc, F006AsymmetricHookError)
            row["f006_printed_dimensions_missing"] = True
            if asymmetric:
                row["f006_asymmetric_hooks"] = True
            keep_form = (
                asymmetric
                and hooked
                and not ambiguous
                and not conflicted
                and total_in_tolerance
            )
            if keep_form:
                # Формата е доказано F006 (HOOKED_CLOSURE топология, обща
                # дължина в допуска, без двусмислие и без конфликт).
                # Отделните производствени размери обаче не могат да бъдат
                # наложени от отпечатаното уравнение. НЕ измисляме
                # симетрични стойности; опитваме да ги извлечем от реалната
                # геометрия на кандидата, с provenance.
                derived = _derive_f006_dimensions_from_candidate(
                    row, entity_map=entity_map
                )
                if derived is not None:
                    row["canonical_segment_lengths_cm"] = list(derived["segments"])
                    row["f006_production_dimensions_provenance"] = derived[
                        "provenance"
                    ]
                    _production(
                        "F006_PRODUCTION_DERIVED",
                        reason,
                        (
                            "F006_PRODUCTION_DERIVED: размерите са извлечени от "
                            "реалната DXF геометрия на кандидата "
                            f"({derived['provenance']}); формата остава силна."
                        ),
                    )
                else:
                    _production(
                        "F006_PRODUCTION_REVIEW",
                        reason,
                        (
                            "F006_PRODUCTION_REVIEW: формата е сигурно F006, но "
                            f"отделните производствени размери не са еднозначни ({exc}). "
                            "Production export за позицията е блокиран до потвърждение."
                        ),
                    )
                continue
            row["batch_status"] = "ЗА РЪЧЕН ПРЕГЛЕД"
            row["candidate_confidence"] = "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"
            row["review_reason_codes"] = [
                *list(row.get("review_reason_codes", [])),
                reason,
            ]
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                f"{reason}: отпечатаните размери на стремето не затварят заявената L ({exc}).",
            ]
            continue

        if len(segments) != 6:
            reason = "F006_PRINTED_DIMENSIONS_MISMATCH"
            row["f006_printed_dimensions_mismatch"] = True
            row["batch_status"] = "ЗА РЪЧЕН ПРЕГЛЕД"
            row["candidate_confidence"] = "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"
            row["review_reason_codes"] = [
                *list(row.get("review_reason_codes", [])),
                reason,
            ]
            continue

        hook = float(segments[0])
        width = max(float(segments[1]), float(segments[2]))
        height = min(float(segments[1]), float(segments[2]))
        canonical = (hook, width, height, width, height, hook)
        total = sum(canonical)
        difference = abs(total - declared)
        if difference > 2.1:
            reason = "F006_PRINTED_DIMENSIONS_MISMATCH"
            row["f006_printed_dimensions_mismatch"] = True
            row["batch_status"] = "ЗА РЪЧЕН ПРЕГЛЕД"
            row["candidate_confidence"] = "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"
            row["review_reason_codes"] = [
                *list(row.get("review_reason_codes", [])),
                reason,
            ]
            continue

        row["f006_production_status"] = "F006_PRODUCTION_EXACT"

        rounding_note = (
            f"; разлика {difference:g} cm е запазена като проектантско закръгляне"
            if difference > 0.11
            else ""
        )
        note = (
            "F006_PRINTED_MASTER_EQUATION: "
            f"2×({width:g}+{height:g}+{hook:g}) = {total:g} cm при заявена "
            f"L={declared:g} cm{rounding_note}. Суровата DXF L="
            f"{float(row.get('geometry_length_raw') or 0):g} cm остава само за одит."
        )
        row["canonical_segment_lengths_cm"] = list(canonical)
        row["canonical_turn_angles_deg"] = list(turns)
        row["effective_geometry_length_cm"] = total
        row["numeric_difference"] = difference
        row["f006_printed_dimensions_resolved"] = True
        row["contextual_rule_code"] = "F006_PRINTED_MASTER_EQUATION"
        row["contextual_rule_note"] = note
        row["candidate_notes"] = [*list(row.get("candidate_notes", [])), note]
        row["analysis_warnings"] = [*list(row.get("analysis_warnings", [])), note]


def _named_u_subtype_code(text: str) -> str | None:
    normalized = " ".join(str(text or "").upper().replace("–", "-").split())
    if re.search(r"\bФИБ(?:А|И|ИТЕ)?\b", normalized):
        return "F005"
    if "ОТВОРЕН ДЕТАЙЛ" in normalized or re.search(r"\bU\s*[- ]?ФОРМА\b", normalized):
        return "F003"
    return None


def _u_context_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    lengths = tuple(
        round(float(value), 3)
        for value in row.get("canonical_segment_lengths_cm", [])
    )
    if lengths:
        return ("SEGMENTS", *lengths)
    declared = _positive_float(row.get("declared_length_cm"))
    segment_count = _positive_int(row.get("segment_count"))
    topology = str(row.get("canonical_signature") or "").strip()
    if declared is None or segment_count is None or not topology:
        return ()
    return ("REPORT", round(declared, 3), segment_count, topology)


def _apply_beam_engineering_shape_law(
    rows: Sequence[dict],
    *,
    annotations_by_handle: Mapping[str, DxfEntityRecord],
    entities: Sequence[DxfEntityRecord],
    shape_definitions: Sequence[ShapeDefinition],
    policy: RecognitionPolicy,
) -> None:
    """Keep staircase families out of proven beam drawing frames.

    The designer often draws beam hooks at a schematic acute angle.  In a
    frame classified as BEAMS, the approved engineering interpretation is a
    90-degree F004 for one end and F003 for two ends.  The rule is applied only
    when the printed production segments exactly reproduce the annotation L;
    otherwise the row is blocked instead of being relabelled.
    """

    if not policy.beam_n8_middle_rule_enabled:
        return
    frames = detect_drawing_frames(entities)
    if not frames:
        return
    frame_map = {frame.index: frame for frame in frames}
    shape_map = {
        str(shape.code).strip().upper(): shape
        for shape in shape_definitions
        if shape.active
    }

    for row in rows:
        if row.get("annotation_class") != "ACTIONABLE":
            continue
        annotation = annotations_by_handle.get(str(row.get("annotation_handle") or ""))
        if annotation is None:
            continue
        anchor = _point(annotation.geometry.get("visual_anchor")) or _point(
            annotation.geometry.get("insert")
        )
        if anchor is None:
            continue
        frame_index = assign_point_to_frame(anchor[0], anchor[1], frames)
        frame = frame_map.get(int(frame_index)) if frame_index is not None else None
        if frame is None:
            continue
        row["frame_index"] = frame.index
        row["frame_title"] = frame.combined_title
        row["frame_category_code"] = frame.category_code
        if frame.category_code != "BEAMS":
            continue

        source_code = str(row.get("master_shape_code") or "").strip().upper()
        if source_code not in {f"F{index:03d}" for index in range(10, 20)}:
            continue
        lengths = tuple(
            float(value) for value in row.get("canonical_segment_lengths_cm", [])
        )
        declared = _positive_float(row.get("declared_length_cm"))
        target = "F004" if len(lengths) == 2 else "F003" if len(lengths) == 3 else ""
        target_shape = shape_map.get(target)
        exact = (
            declared is not None
            and bool(lengths)
            and abs(sum(lengths) - declared)
            <= policy.exact_length_tolerance_for_master(declared, target)
        )
        if target_shape is None or not exact:
            reason = "BEAM_STAIR_SHAPE_FORBIDDEN"
            row["batch_status"] = "ЗА РЪЧЕН ПРЕГЛЕД"
            row["candidate_confidence"] = "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"
            row["review_reason_codes"] = [
                *list(row.get("review_reason_codes", [])),
                reason,
            ]
            row["candidate_notes"] = [
                *list(row.get("candidate_notes", [])),
                (
                    f"{source_code} е забранена стълбищна форма в рамка Греди, "
                    "но отпечатаните сегменти не доказват еднозначно F003/F004."
                ),
            ]
            continue

        rule_code = "BEAM_ENGINEERING_90_F004" if target == "F004" else "BEAM_ENGINEERING_90_F003"
        note = (
            f"{rule_code}: рамката е Греди и {source_code} е схематично "
            f"изчертаване; {len(lengths)} отпечатани сегмента с общо "
            f"{sum(lengths):g} cm се нормализират към {target} под 90°."
        )
        row["beam_original_master_shape_code"] = source_code
        row["beam_engineering_shape_resolved"] = True
        row["master_shape_code"] = target
        row["master_shape_name"] = target_shape.name
        row["master_ambiguous_shape_codes"] = []
        row["master_conflict"] = False
        row["canonical_turn_angles_deg"] = [90.0] * max(0, len(lengths) - 1)
        row["canonical_signature"] = (
            f"BEAM_90|{target}|" + ",".join(f"{value:.3f}" for value in lengths)
        )
        row["contextual_rule_code"] = rule_code
        row["contextual_rule_note"] = note
        row["review_reason_codes"] = [
            str(value)
            for value in row.get("review_reason_codes", [])
            if str(value).strip().upper()
            not in {"AMBIGUOUS_MASTER", "MASTER_AMBIGUITY"}
        ]
        row["candidate_notes"] = [*list(row.get("candidate_notes", [])), note]
        row["analysis_warnings"] = [*list(row.get("analysis_warnings", [])), note]


def _matcher_decision_counts(rows: Sequence[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        disposition = str(row.get("matcher_disposition") or "UNCLASSIFIED")
        counts[disposition] = counts.get(disposition, 0) + 1
    return dict(sorted(counts.items()))


def _apply_review_groups(rows: Sequence[dict]) -> None:
    """Attach deterministic, conservative review grouping metadata.

    This function only groups report rows.  It never copies an approval or a
    manual decision to another row.  That keeps the first iteration safe while
    allowing the Review Center to present one representative per equivalent
    group in a later workflow.
    """

    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = build_review_group_key(row)
        row["review_group_key"] = key
        row["review_group_basis"] = review_group_components(row)
        groups.setdefault(key, []).append(row)

    for key, members in groups.items():
        ordered = sorted(
            members,
            key=lambda row: (
                str(row.get("annotation_handle") or ""),
                tuple(
                    sorted(
                        str(value)
                        for value in row.get("candidate_handles", [])
                        if str(value)
                    )
                ),
            ),
        )
        representative_handle = str(
            ordered[0].get("annotation_handle") or ""
        )
        member_handles = [
            str(row.get("annotation_handle") or "") for row in ordered
        ]
        for index, row in enumerate(ordered, start=1):
            row["review_group_key"] = key
            row["review_group_size"] = len(ordered)
            row["review_group_representative"] = index == 1
            row["review_group_representative_handle"] = representative_handle
            row["review_group_member_handles"] = member_handles
            row["review_group_member_index"] = index


def _review_group_counts(rows: Sequence[dict]) -> dict[str, int]:
    actionable = [
        row for row in rows if row.get("annotation_class") == "ACTIONABLE"
    ]
    keys = {
        str(row.get("review_group_key") or build_review_group_key(row))
        for row in actionable
    }
    sizes: dict[str, int] = {}
    for row in actionable:
        key = str(row.get("review_group_key") or build_review_group_key(row))
        sizes[key] = sizes.get(key, 0) + 1
    return {
        "actionable_groups": len(keys),
        "multi_position_groups": sum(size > 1 for size in sizes.values()),
        "positions_saved": max(0, len(actionable) - len(keys)),
        "largest_group": max(sizes.values(), default=0),
    }


def _review_group_metadata(rows: Sequence[dict]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("annotation_class") != "ACTIONABLE":
            continue
        key = str(row.get("review_group_key") or build_review_group_key(row))
        groups.setdefault(key, []).append(row)

    result: list[dict[str, Any]] = []
    for key in sorted(groups):
        members = sorted(
            groups[key],
            key=lambda row: str(row.get("annotation_handle") or ""),
        )
        representative = next(
            (
                row
                for row in members
                if bool(row.get("review_group_representative"))
            ),
            members[0],
        )
        result.append(
            {
                "review_group_key": key,
                "size": len(members),
                "representative_handle": str(
                    representative.get("annotation_handle") or ""
                ),
                "member_handles": [
                    str(row.get("annotation_handle") or "") for row in members
                ],
                "basis": review_group_components(representative),
            }
        )
    return result


def _annotation_class(parsed: ParsedRebarText) -> str:
    if parsed.count is None:
        return "MISSING_COUNT"
    if parsed.diameter_mm is None:
        return "MISSING_DIAMETER"
    if parsed.length_cm is None:
        return "UNRESOLVED_LENGTH" if parsed.length_expression else "MISSING_LENGTH"
    return "ACTIONABLE"


def _data_quality_flags(parsed: ParsedRebarText) -> list[str]:
    flags: list[str] = []
    if parsed.count is None:
        flags.append("ЛИПСВА БРОЙ")
    if parsed.diameter_mm is None:
        flags.append("ЛИПСВА ДИАМЕТЪР")
    if parsed.length_cm is None:
        if parsed.length_expression:
            flags.append("ДЪЛЖИНАТА СЪДЪРЖА ПРОМЕНЛИВА")
        else:
            flags.append("ЛИПСВА ЧИСЛОВА ДЪЛЖИНА")
    elif parsed.length_cm > 2000:
        flags.append("НЕОБИЧАЙНО ГОЛЯМА ДЪЛЖИНА — ПРОВЕРИ ПРОЕКТАНТСКА ГРЕШКА")
    flags.extend(str(value) for value in parsed.warnings if str(value) not in flags)
    return flags


def _quantity_review_required(parsed: ParsedRebarText) -> bool:
    """Return whether an unusually large, but syntactically valid, count needs review.

    Counts in the thousands are legitimate for chairs, slab-edge hairpins and
    punching-shear C-bars.  They must therefore never be rewritten or labelled
    as a parser error solely because of magnitude.  They remain excluded from
    unattended bulk approval until a person confirms the engineering context.
    """

    return parsed.count is not None and parsed.count > 1000


def _data_quality_notes(parsed: ParsedRebarText) -> list[str]:
    notes: list[str] = []
    if _quantity_review_required(parsed):
        notes.append(
            "ГОЛЯМ БРОЙ — синтактично валиден. Потвърди дали позицията е масова "
            "арматура (столчета, фиби или C-ове), преди окончателна спецификация."
        )
    return notes


def _context_row(
    annotation: DxfEntityRecord,
    parsed: ParsedRebarText,
    policy: RecognitionPolicy,
    annotation_class: str,
) -> dict:
    if annotation_class == "MISSING_COUNT":
        batch_status = "НЕПЪЛЕН НАДПИС"
        analysis_status = "Липсва брой; надписът не е самостоятелна позиция за спецификация."
    elif annotation_class == "UNRESOLVED_LENGTH":
        batch_status = "НЕИЗЧИСЛИМА ДЪЛЖИНА"
        analysis_status = "Дължината съдържа променлива и изисква размер от чертежа."
    else:
        batch_status = "КОНТЕКСТЕН НАДПИС"
        analysis_status = "Контекстен армировъчен текст без пълна числова дължина."
    return {
        "annotation_handle": annotation.handle,
        "annotation_text": annotation.text or "",
        "diameter_mm": parsed.diameter_mm,
        "declared_length_cm": parsed.length_cm,
        "batch_status": batch_status,
        "analysis_status": analysis_status,
        "approved_in_project": False,
        "candidate_handles": [],
        "candidate_layer": None,
        "segment_count": 0,
        "geometry_length_raw": None,
        "effective_geometry_length_cm": None,
        "raw_numeric_difference": None,
        "numeric_difference": None,
        "distance_raw": None,
        "layer_diameter_mm": None,
        "diameter_layer_match": False,
        "candidate_confidence": None,
        "candidate_basis": None,
        "candidate_score": None,
        "candidate_score_gap": None,
        "candidate_distance_gap_raw": None,
        "global_conflict": False,
        "master_conflict": False,
        "candidate_notes": list(parsed.warnings),
        "canonical_signature": None,
        "master_shape_code": None,
        "master_shape_name": None,
        "master_shape_mirrored": False,
        "master_ambiguous_shape_codes": [],
        "master_reference_profile_id": None,
        "uniform_scale_factor": None,
        "review_reason_codes": [],
        "contextual_rule_code": None,
        "contextual_rule_note": None,
        "canonical_segment_lengths_cm": [],
        "canonical_turn_angles_deg": [],
        "analysis_warnings": list(parsed.warnings),
        "rejection_counts": {},
        "recognition_policy": policy.to_dict(),
        "recognition_laws": list(policy.laws_bg()),
        "alternative_candidates": [],
    }


def _apply_global_geometry_conflicts(rows: Sequence[dict]) -> None:
    # ownership_reference_only is set only for geometry identified through a
    # shared legend/position-key reference (resolve_position_key_annotation)
    # or a proxy-block reference. Many distinct annotations legitimately
    # pointing at the same legend geometry is the whole point of a legend —
    # running the double-ownership check on them would only produce false
    # conflicts. Owner-confirmed 2026-09-09; see
    # docs/KNOWN_LIMITATIONS_v0.1.0_BG.md.
    candidate_rows: list[tuple[dict, frozenset[str]]] = []
    for row in rows:
        if (
            row.get("annotation_class") == "ACTIONABLE"
            and row.get("batch_status") != "ОДОБРЕНА ВРЪЗКА"
            and not bool(row.get("ownership_reference_only"))
        ):
            # Keep the report schema total even for synthetic/test rows and
            # for callers that do not pass through _base_row().
            row["global_conflict"] = False
            row["geometry_owner_conflict"] = False
            handles = frozenset(
                str(value) for value in row.get("candidate_handles", []) if str(value)
            )
            if handles:
                candidate_rows.append((row, handles))

    conflicts: dict[int, tuple[dict, set[str], set[int]]] = {}
    for left_index, (left_row, left_handles) in enumerate(candidate_rows):
        for right_row, right_handles in candidate_rows[left_index + 1 :]:
            overlap = left_handles & right_handles
            if not overlap:
                continue
            left_key = id(left_row)
            right_key = id(right_row)
            conflicts.setdefault(left_key, (left_row, set(), set()))[1].update(overlap)
            conflicts.setdefault(right_key, (right_row, set(), set()))[1].update(overlap)
            conflicts[left_key][2].add(right_key)
            conflicts[right_key][2].add(left_key)

    conflict_rows = {key: value[0] for key, value in conflicts.items()}
    for row, handles, linked_keys in conflicts.values():
        linked_rows = [conflict_rows[key] for key in linked_keys if key in conflict_rows]
        unique_annotations = {
            str(value.get("annotation_handle")) for value in (row, *linked_rows)
        }
        texts = sorted(
            {str(value.get("annotation_text") or "") for value in (row, *linked_rows)}
        )
        note = (
            "ГЛОБАЛЕН КОНФЛИКТ: припокриваща се DXF геометрия "
            f"({', '.join(sorted(handles))}) участва в първия кандидат на "
            f"{len(unique_annotations)} надписа: "
            + "; ".join(texts)
        )
        row["global_conflict"] = True
        row["geometry_owner_conflict"] = True
        row["batch_status"] = "ЗА РЪЧЕН ПРЕГЛЕД"
        row["candidate_confidence"] = "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА — КОНФЛИКТ"
        notes = list(row.get("candidate_notes", []))
        if note not in notes:
            notes.append(note)
        row["candidate_notes"] = notes
        warnings = list(row.get("analysis_warnings", []))
        if note not in warnings:
            warnings.append(note)
        row["analysis_warnings"] = warnings


def _resolve_global_geometry_ownership(
    rows: Sequence[dict],
    policy: RecognitionPolicy,
) -> None:
    """Reject an obviously foreign top candidate before reporting conflicts.

    A nearby annotation may see a partial chain that already belongs to another
    annotation.  Distance alone is not enough to resolve that situation.  When
    two top candidates share DXF handles and one annotation has a strictly
    better *raw* length agreement band, the better row owns the geometry and
    the weaker row is returned as ``НЯМА КАНДИДАТ``.  Equal bands remain an
    explicit conflict for human review.

    The raw DXF difference is deliberately used here.  A schematic MASTER scale
    may make the matcher difference zero, but it must never hide that the same
    geometry agrees far better with a neighbouring production label.
    """

    candidate_rows: list[tuple[dict, frozenset[str]]] = []
    for row in rows:
        if row.get("annotation_class") != "ACTIONABLE":
            continue
        if bool(row.get("ownership_reference_only")):
            continue
        handles = frozenset(
            str(value) for value in row.get("candidate_handles", []) if str(value)
        )
        if handles:
            candidate_rows.append((row, handles))

    rejected: dict[int, list[tuple[dict, set[str]]]] = {}
    for left_index, (left_row, left_handles) in enumerate(candidate_rows):
        for right_row, right_handles in candidate_rows[left_index + 1 :]:
            overlap = left_handles & right_handles
            if not overlap:
                continue
            left_band = _ownership_length_band(left_row, policy)
            right_band = _ownership_length_band(right_row, policy)
            if left_band == right_band:
                resolved = _same_band_ownership_winner(left_row, right_row, policy)
                if resolved is None:
                    continue
                winner, loser = resolved
                loser["ownership_tiebreak_resolved"] = True
            elif left_band < right_band:
                winner, loser = left_row, right_row
            else:
                winner, loser = right_row, left_row
            rejected.setdefault(id(loser), []).append((winner, set(overlap)))

    rejected_ids = set(rejected)
    occupied_handles: set[str] = {
        handle
        for row, handles in candidate_rows
        if id(row) not in rejected_ids
        for handle in handles
    }
    rejected_rows = sorted(
        (row for row, _handles in candidate_rows if id(row) in rejected_ids),
        key=lambda row: (
            _ownership_length_band(row, policy),
            _raw_length_difference(row) if _raw_length_difference(row) is not None else math.inf,
            _sortable_number(row.get("distance_raw")),
            _sortable_number(row.get("candidate_score")),
            str(row.get("annotation_handle") or ""),
        ),
    )

    for row in rejected_rows:
        blockers = rejected.get(id(row), [])
        blocker_rows = [value[0] for value in blockers]
        overlap = sorted({handle for _winner, values in blockers for handle in values})
        winner_labels = sorted(
            {
                str(value.get("annotation_text") or value.get("annotation_handle") or "—")
                for value in blocker_rows
            }
        )
        rejected_candidate = _candidate_snapshot_from_row(row)
        alternative = _first_free_alternative(row, occupied_handles)
        if alternative is not None:
            _promote_ownership_alternative(
                row,
                alternative,
                policy=policy,
                rejected_candidate=rejected_candidate,
                overlap=overlap,
                winner_labels=winner_labels,
            )
            occupied_handles.update(
                str(value) for value in row.get("candidate_handles", []) if str(value)
            )
            continue
        _clear_foreign_geometry_candidate(
            row,
            rejected_candidate=rejected_candidate,
            overlap=overlap,
            winner_labels=winner_labels,
        )


def _same_band_ownership_winner(
    left_row: dict[str, Any],
    right_row: dict[str, Any],
    policy: RecognitionPolicy,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return a winner only when three independent margins agree.

    Equal tolerance bands are normally left as visible conflicts.  The sole
    exception is an exact-band pair where length fit, annotation distance and
    the complete candidate score all favour the same row by configured minimum
    margins.  MASTER ambiguity or an already approved link blocks this
    tiebreaker because those cases require a different kind of evidence.
    """

    if any(
        bool(row.get("master_conflict"))
        or bool(row.get("master_ambiguous_shape_codes"))
        or row.get("batch_status") == "ОДОБРЕНА ВРЪЗКА"
        or bool(row.get("approved_in_project"))
        for row in (left_row, right_row)
    ):
        return None
    if _ownership_length_band(left_row, policy) != 0:
        return None

    left_difference = _raw_length_difference(left_row)
    right_difference = _raw_length_difference(right_row)
    if left_difference is None or right_difference is None:
        return None
    difference_gap = abs(left_difference - right_difference)
    if difference_gap < policy.ownership_tiebreak_min_length_advantage_cm:
        return None
    if left_difference < right_difference:
        winner, loser = left_row, right_row
    elif right_difference < left_difference:
        winner, loser = right_row, left_row
    else:
        return None

    winner_distance = _sortable_number(winner.get("distance_raw"))
    loser_distance = _sortable_number(loser.get("distance_raw"))
    winner_score = _sortable_number(winner.get("candidate_score"))
    loser_score = _sortable_number(loser.get("candidate_score"))
    if not all(
        math.isfinite(value)
        for value in (winner_distance, loser_distance, winner_score, loser_score)
    ):
        return None
    if (
        loser_distance - winner_distance
        < policy.ownership_tiebreak_min_distance_advantage_raw
    ):
        return None
    if loser_score - winner_score < policy.ownership_tiebreak_min_score_advantage:
        return None
    return winner, loser


def _first_free_alternative(
    row: Mapping[str, Any],
    occupied_handles: set[str],
) -> dict[str, Any] | None:
    for raw in row.get("alternative_candidates", []):
        if not isinstance(raw, Mapping):
            continue
        handles = {
            str(value) for value in raw.get("handles", []) if str(value)
        }
        if not handles or handles & occupied_handles:
            continue
        return dict(raw)
    return None


def _candidate_snapshot_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "handles": list(row.get("candidate_handles", [])),
        "layer": row.get("candidate_layer"),
        "segment_count": row.get("segment_count"),
        "geometry_length_raw": row.get("geometry_length_raw"),
        "effective_geometry_length_cm": row.get("effective_geometry_length_cm"),
        "raw_numeric_difference": _raw_length_difference(row),
        "numeric_difference": row.get("numeric_difference"),
        "distance_raw": row.get("distance_raw"),
        "centroid_distance_raw": row.get("centroid_distance_raw"),
        "layer_diameter_mm": row.get("layer_diameter_mm"),
        "diameter_layer_match": row.get("diameter_layer_match"),
        "score": row.get("candidate_score"),
        "score_gap": row.get("candidate_score_gap"),
        "distance_gap_raw": row.get("candidate_distance_gap_raw"),
        "confidence": row.get("candidate_confidence"),
        "basis": row.get("candidate_basis"),
        "notes": list(row.get("candidate_notes", [])),
        "canonical_signature": row.get("canonical_signature"),
        "master_shape_code": row.get("master_shape_code"),
        "master_shape_name": row.get("master_shape_name"),
        "master_shape_mirrored": row.get("master_shape_mirrored"),
        "master_shape_reversed": row.get("master_shape_reversed"),
        "master_shape_score": row.get("master_shape_score"),
        "master_ambiguous_shape_codes": list(
            row.get("master_ambiguous_shape_codes", [])
        ),
        "master_reference_profile_id": row.get("master_reference_profile_id"),
        "uniform_scale_factor": row.get("uniform_scale_factor"),
        "dimension_evidence_applied": bool(row.get("dimension_evidence_applied")),
        "dimension_master_resolved": bool(row.get("dimension_master_resolved")),
        "dimension_original_ambiguous_shape_codes": list(
            row.get("dimension_original_ambiguous_shape_codes", [])
        ),
        "dimension_text_handles": list(row.get("dimension_text_handles", [])),
        "dimension_resolution_strategy": row.get("dimension_resolution_strategy"),
        "dimension_total_difference_cm": row.get("dimension_total_difference_cm"),
        "review_reason_codes": list(row.get("review_reason_codes", [])),
    }


def _promote_ownership_alternative(
    row: dict[str, Any],
    alternative: Mapping[str, Any],
    *,
    policy: RecognitionPolicy,
    rejected_candidate: Mapping[str, Any],
    overlap: Sequence[str],
    winner_labels: Sequence[str],
) -> None:
    handles = [str(value) for value in alternative.get("handles", []) if str(value)]
    original_confidence = str(alternative.get("confidence") or "")
    risk_only = "РИСКОВ" in original_confidence or original_confidence.startswith("СЛАБ")
    verified = _verified_ownership_alternative(row, alternative, policy)
    if verified:
        note = (
            "ПРОВЕРЕНО ГЛОБАЛНО ПРЕНАСОЧВАНЕ: първият кандидат беше "
            f"отхвърлен като чужда геометрия ({', '.join(overlap)}), защото "
            "принадлежи по-точно на "
            + "; ".join(winner_labels)
            + f". Свободният резервен кандидат №{alternative.get('rank', '—')} "
            "е потвърден от точни A–E, обща L, Ø от слоя и еднозначен "
            "MASTER; резултатът е силен, но не е автоматично одобрение."
        )
    else:
        note = (
            "ГЛОБАЛНО ПРЕНАСОЧВАНЕ: първият кандидат беше отхвърлен като чужда "
            f"геометрия ({', '.join(overlap)}), защото принадлежи по-точно на "
            + "; ".join(winner_labels)
            + f". Избран е свободният резервен кандидат №{alternative.get('rank', '—')} "
            "само за ръчна проверка; това не е автоматично одобрение."
        )
    reasons = [str(value) for value in alternative.get("review_reason_codes", [])]
    recovery_reason = (
        "GLOBAL_ALTERNATIVE_RECOVERY_VERIFIED"
        if verified
        else "GLOBAL_ALTERNATIVE_RECOVERY"
    )
    if recovery_reason not in reasons:
        reasons.append(recovery_reason)
    notes = [str(value) for value in alternative.get("notes", [])]
    if note not in notes:
        notes.append(note)
    row.update(
        {
            "batch_status": (
                "РИСК"
                if risk_only
                else (
                    "СИЛЕН"
                    if verified and bool(alternative.get("diameter_layer_match"))
                    else (
                        "СИЛЕН ПО ГЕОМЕТРИЯ"
                        if verified
                        else "ЗА РЪЧЕН ПРЕГЛЕД"
                    )
                )
            ),
            "analysis_status": "СВОБОДЕН РЕЗЕРВЕН КАНДИДАТ СЛЕД ГЛОБАЛНО РАЗПРЕДЕЛЕНИЕ",
            "candidate_handles": handles,
            "candidate_layer": alternative.get("layer"),
            "segment_count": int(alternative.get("segment_count") or 0),
            "geometry_length_raw": alternative.get("geometry_length_raw"),
            "effective_geometry_length_cm": alternative.get("effective_geometry_length_cm"),
            "raw_numeric_difference": alternative.get("raw_numeric_difference"),
            "numeric_difference": alternative.get("numeric_difference"),
            "distance_raw": alternative.get("distance_raw"),
            "centroid_distance_raw": alternative.get("centroid_distance_raw"),
            "layer_diameter_mm": alternative.get("layer_diameter_mm"),
            "diameter_layer_match": bool(alternative.get("diameter_layer_match")),
            "candidate_confidence": (
                original_confidence
                if risk_only or verified
                else "РЕЗЕРВЕН КАНДИДАТ СЛЕД ГЛОБАЛНО РАЗПРЕДЕЛЕНИЕ — РЪЧНА ПРОВЕРКА"
            ),
            "candidate_original_confidence": original_confidence,
            "candidate_basis": alternative.get("basis"),
            "candidate_score": alternative.get("score"),
            "candidate_score_gap": alternative.get("score_gap"),
            "candidate_distance_gap_raw": alternative.get("distance_gap_raw"),
            "global_conflict": False,
            "geometry_owner_conflict": False,
            "master_conflict": bool(alternative.get("master_ambiguous_shape_codes")),
            "candidate_notes": notes,
            "canonical_signature": alternative.get("canonical_signature"),
            "master_shape_code": alternative.get("master_shape_code"),
            "master_shape_name": alternative.get("master_shape_name"),
            "master_shape_mirrored": bool(alternative.get("master_shape_mirrored")),
            "master_shape_reversed": bool(alternative.get("master_shape_reversed")),
            "master_shape_score": alternative.get("master_shape_score"),
            "master_ambiguous_shape_codes": list(
                alternative.get("master_ambiguous_shape_codes", [])
            ),
            "master_reference_profile_id": alternative.get("master_reference_profile_id"),
            "uniform_scale_factor": alternative.get("uniform_scale_factor"),
            "dimension_evidence_applied": bool(
                alternative.get("dimension_evidence_applied")
            ),
            "dimension_master_resolved": bool(
                alternative.get("dimension_master_resolved")
            ),
            "dimension_original_ambiguous_shape_codes": list(
                alternative.get("dimension_original_ambiguous_shape_codes", [])
            ),
            "dimension_text_handles": list(
                alternative.get("dimension_text_handles", [])
            ),
            "dimension_resolution_strategy": alternative.get(
                "dimension_resolution_strategy"
            ),
            "dimension_total_difference_cm": alternative.get(
                "dimension_total_difference_cm"
            ),
            "mirror_requires_review": bool(alternative.get("mirror_requires_review")),
            "orientation_invariant": bool(alternative.get("orientation_invariant", True)),
            "review_reason_codes": reasons,
            "contextual_rule_code": alternative.get("contextual_rule_code"),
            "contextual_rule_note": alternative.get("contextual_rule_note"),
            "near_gap_healed": bool(alternative.get("near_gap_healed")),
            "near_gap_bridge_gap_raw": alternative.get("near_gap_bridge_gap_raw"),
            "near_gap_bridge_handles": list(alternative.get("near_gap_bridge_handles", [])),
            "near_gap_bridge_points": list(alternative.get("near_gap_bridge_points", [])),
            "canonical_segment_lengths_cm": list(
                alternative.get("canonical_segment_lengths_cm", [])
            ),
            "canonical_turn_angles_deg": list(
                alternative.get("canonical_turn_angles_deg", [])
            ),
            "ownership_rejected": not verified,
            "ownership_reassigned": True,
            "ownership_recovery_verified": verified,
            "ownership_reassignment_rank": alternative.get("rank"),
            "ownership_rejected_candidate": dict(rejected_candidate),
            "ownership_winner_annotations": list(winner_labels),
            "alternative_candidates": [
                dict(value)
                for value in row.get("alternative_candidates", [])
                if isinstance(value, Mapping)
                and list(value.get("handles", [])) != list(alternative.get("handles", []))
            ],
        }
    )
    warnings = list(row.get("analysis_warnings", []))
    if note not in warnings:
        warnings.append(note)
    row["analysis_warnings"] = warnings


def _verified_ownership_alternative(
    row: Mapping[str, Any],
    alternative: Mapping[str, Any],
    policy: RecognitionPolicy,
) -> bool:
    """Prove that a free replacement candidate is strong on its own merits."""

    if not policy.verified_alternative_recovery_enabled:
        return False
    if not bool(alternative.get("dimension_evidence_applied")):
        return False
    if not bool(alternative.get("diameter_layer_match")):
        return False
    if alternative.get("master_ambiguous_shape_codes"):
        return False
    if not str(alternative.get("master_shape_code") or "").strip():
        return False
    if not alternative.get("canonical_segment_lengths_cm"):
        return False
    if any(
        code in {
            "AMBIGUOUS_MASTER",
            "FAR_ASSOCIATION",
            "HOOKED_CLOSURE",
            "MIRROR_REVIEW",
            "NEAR_GAP_HEALED",
            "SCHEMATIC_SCALE",
            "UNRESOLVED_CURVE_GEOMETRY",
        }
        for code in (
            str(value) for value in alternative.get("review_reason_codes", [])
        )
    ):
        return False

    try:
        declared = max(float(row.get("declared_length_cm") or 0.0), 1.0)
        difference = abs(float(alternative.get("numeric_difference")))
        dimension_difference = abs(
            float(alternative.get("dimension_total_difference_cm"))
        )
        distance = float(alternative.get("distance_raw"))
        score_gap = float(alternative.get("score_gap"))
    except (TypeError, ValueError):
        return False
    if difference > policy.exact_length_tolerance_for_master(
        declared,
        str(alternative.get("master_shape_code") or ""),
    ):
        return False
    if dimension_difference > float(
        policy.verified_alternative_dimension_tolerance_cm
    ):
        return False
    if distance > policy.strong_distance_limit(declared):
        return False
    return score_gap >= float(policy.strong_score_gap)


def _clear_foreign_geometry_candidate(
    row: dict[str, Any],
    *,
    rejected_candidate: Mapping[str, Any],
    overlap: Sequence[str],
    winner_labels: Sequence[str],
) -> None:
    note = (
        "ОТХВЪРЛЕНА ЧУЖДА ГЕОМЕТРИЯ: припокриващите се DXF обекти "
        f"({', '.join(overlap)}) имат по-точно сурово съвпадение с надпис "
        + "; ".join(winner_labels)
        + ". Няма свободен резервен кандидат; текущият надпис остава без кандидат."
    )
    existing_notes = list(row.get("candidate_notes", []))
    row.update(
        {
            "batch_status": "НЯМА КАНДИДАТ",
            "analysis_status": "ЧУЖДА ГЕОМЕТРИЯ ОТХВЪРЛЕНА ГЛОБАЛНО",
            "candidate_handles": [],
            "candidate_layer": None,
            "segment_count": 0,
            "geometry_length_raw": None,
            "effective_geometry_length_cm": None,
            "raw_numeric_difference": None,
            "numeric_difference": None,
            "distance_raw": None,
            "layer_diameter_mm": None,
            "diameter_layer_match": False,
            "candidate_confidence": "ОТХВЪРЛЕНА ЧУЖДА ГЕОМЕТРИЯ",
            "candidate_basis": None,
            "candidate_score": None,
            "candidate_score_gap": None,
            "candidate_distance_gap_raw": None,
            "global_conflict": False,
            "geometry_owner_conflict": False,
            "master_conflict": False,
            "candidate_notes": [*existing_notes, note],
            "canonical_signature": None,
            "master_shape_code": None,
            "master_shape_name": None,
            "master_shape_mirrored": False,
            "master_ambiguous_shape_codes": [],
            "master_reference_profile_id": None,
            "uniform_scale_factor": None,
            "dimension_evidence_applied": False,
            "dimension_master_resolved": False,
            "dimension_original_ambiguous_shape_codes": [],
            "dimension_text_handles": [],
            "dimension_resolution_strategy": None,
            "dimension_total_difference_cm": None,
            "review_reason_codes": ["FOREIGN_GEOMETRY_REJECTED"],
            "ownership_rejected": True,
            "ownership_reassigned": False,
            "ownership_rejected_candidate": dict(rejected_candidate),
            "ownership_winner_annotations": list(winner_labels),
        }
    )
    warnings = list(row.get("analysis_warnings", []))
    if note not in warnings:
        warnings.append(note)
    row["analysis_warnings"] = warnings


def _ownership_length_band(row: Mapping[str, Any], policy: RecognitionPolicy) -> int:
    if row.get("batch_status") == "ОДОБРЕНА ВРЪЗКА" or row.get("approved_in_project"):
        return -1
    declared = _positive_float(row.get("declared_length_cm"))
    difference = _raw_length_difference(row)
    if declared is None or difference is None:
        return 4
    if difference <= policy.exact_length_tolerance_for_master(
        declared,
        str(row.get("master_shape_code") or ""),
    ):
        return 0
    if difference <= policy.review_length_tolerance(declared):
        return 1
    if difference <= policy.hard_length_difference_limit(declared):
        return 2
    return 3


def _raw_length_difference(row: Mapping[str, Any]) -> float | None:
    value = row.get("raw_numeric_difference")
    try:
        if value is not None:
            return abs(float(value))
    except (TypeError, ValueError):
        pass
    declared = _positive_float(row.get("declared_length_cm"))
    geometry = _positive_float(row.get("geometry_length_raw"))
    if declared is None or geometry is None:
        return None
    return abs(geometry - declared)


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or not number.is_integer():
        return None
    return int(number)


def _sortable_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.inf


def _approved_row(
    annotation: DxfEntityRecord,
    approved: dict,
    policy: RecognitionPolicy,
) -> dict:
    handles = [str(value) for value in approved.get("geometry_handles", [])]
    return {
        "annotation_handle": annotation.handle,
        "annotation_text": annotation.text or approved.get("annotation_text", ""),
        "diameter_mm": approved.get("diameter_mm"),
        "declared_length_cm": approved.get("declared_length_cm"),
        "batch_status": "ОДОБРЕНА ВРЪЗКА",
        "candidate_score_gap": None,
        "candidate_distance_gap_raw": None,
        "global_conflict": False,
        "master_conflict": False,
        "analysis_status": "ПРОПУСНАТО — ВЕЧЕ Е ОДОБРЕНО В ПРОЕКТА",
        "approved_in_project": True,
        "candidate_handles": handles,
        "candidate_layer": approved.get("geometry_layer"),
        "segment_count": int(approved.get("segment_count", len(handles)) or 0),
        "geometry_length_raw": approved.get("geometry_length_raw"),
        "effective_geometry_length_cm": approved.get(
            "production_length_cm", approved.get("geometry_length_raw")
        ),
        "production_length_cm": approved.get(
            "production_length_cm", approved.get("declared_length_cm")
        ),
        "production_length_source": approved.get("production_length_source"),
        "production_segment_lengths_cm": list(
            approved.get("production_segment_lengths_cm", [])
        ),
        "raw_numeric_difference": approved.get("numeric_difference"),
        "numeric_difference": approved.get(
            "geometry_vs_production_difference_cm", approved.get("numeric_difference")
        ),
        "distance_raw": approved.get("distance_raw"),
        "layer_diameter_mm": None,
        "diameter_layer_match": bool(approved.get("diameter_layer_match", False)),
        "candidate_confidence": "ОДОБРЕНО РЪЧНО",
        "candidate_basis": approved.get("candidate_basis"),
        "candidate_score": None,
        "candidate_notes": list(approved.get("candidate_notes", [])),
        "canonical_signature": (approved.get("manual_assignment") or {}).get(
            "canonical_signature"
        ),
        "master_shape_code": approved.get("master_shape_code"),
        "master_shape_name": approved.get("master_shape_name"),
        "master_shape_mirrored": False,
        "master_ambiguous_shape_codes": [],
        "master_reference_profile_id": None,
        "uniform_scale_factor": None,
        "review_reason_codes": [],
        "contextual_rule_code": None,
        "contextual_rule_note": None,
        "canonical_segment_lengths_cm": [],
        "canonical_turn_angles_deg": [],
        "analysis_warnings": ["Връзката не е преизчислявана, защото вече е одобрена ръчно."],
        "rejection_counts": {},
        "recognition_policy": policy.to_dict(),
        "recognition_laws": list(policy.laws_bg()),
        "alternative_candidates": [],
    }


def _error_row(
    annotation: DxfEntityRecord,
    error_message: str,
    policy: RecognitionPolicy,
) -> dict:
    return {
        "annotation_handle": annotation.handle,
        "annotation_text": annotation.text or "",
        "annotation_type": annotation.entity_type,
        "diameter_mm": None,
        "declared_length_cm": None,
        "batch_status": "ГРЕШКА",
        "candidate_score_gap": None,
        "candidate_distance_gap_raw": None,
        "global_conflict": False,
        "master_conflict": False,
        "analysis_status": error_message,
        "approved_in_project": False,
        "candidate_handles": [],
        "candidate_layer": None,
        "segment_count": 0,
        "geometry_length_raw": None,
        "effective_geometry_length_cm": None,
        "raw_numeric_difference": None,
        "numeric_difference": None,
        "distance_raw": None,
        "layer_diameter_mm": None,
        "diameter_layer_match": False,
        "candidate_confidence": None,
        "candidate_basis": None,
        "candidate_score": None,
        "candidate_notes": [error_message],
        "canonical_signature": None,
        "master_shape_code": None,
        "master_shape_name": None,
        "master_shape_mirrored": False,
        "master_ambiguous_shape_codes": [],
        "master_reference_profile_id": None,
        "uniform_scale_factor": None,
        "review_reason_codes": [],
        "contextual_rule_code": None,
        "contextual_rule_note": None,
        "canonical_segment_lengths_cm": [],
        "canonical_turn_angles_deg": [],
        "analysis_warnings": [error_message],
        "rejection_counts": {},
        "recognition_policy": policy.to_dict(),
        "recognition_laws": list(policy.laws_bg()),
        "alternative_candidates": [],
    }


def _write_checkpoint(
    report_dir: Path,
    *,
    project_name: str,
    generated_at: datetime,
    total_annotations: int,
    rows: Sequence[dict],
    cancelled: bool,
    errors: Sequence[str],
    final: bool = False,
    data_identity: Mapping[str, Any] | None = None,
) -> None:
    path = report_dir / ("progress_final.json" if final else "progress.json")
    payload = {
        "schema": "lion-rebar-batch-progress/v1",
        "project_name": project_name,
        IDENTITY_KEY: dict(data_identity or {}),
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "processed_annotations": len(rows),
        "total_annotations": total_annotations,
        "cancelled": cancelled,
        "errors": list(errors),
        "rows": [
            {key: value for key, value in row.items() if key != "svg_preview"}
            for row in rows
        ],
    }
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _write_category_csvs(report_dir: Path, base_name: str, rows: Sequence[dict]) -> None:
    categories = {
        "APPROVED": {"ОДОБРЕНА ВРЪЗКА"},
        "STRONG": {"СИЛЕН"},
        "GEOMETRY_STRONG": {"СИЛЕН ПО ГЕОМЕТРИЯ"},
        "F006_STRONG": {"СИЛНО СТРЕМЕ F006"},
        "MANUAL_REVIEW": {"ЗА РЪЧЕН ПРЕГЛЕД"},
        "RISK": {"РИСК", "ГРЕШКА"},
        "NO_CANDIDATE": {"НЯМА КАНДИДАТ"},
        "CONTEXT": {"КОНТЕКСТЕН НАДПИС", "НЕПЪЛЕН НАДПИС", "НЕИЗЧИСЛИМА ДЪЛЖИНА"},
    }
    for suffix, statuses in categories.items():
        selected = [row for row in rows if row.get("batch_status") in statuses]
        _write_csv(report_dir / f"{base_name}_{suffix}.csv", selected)


#: Колко градуса се прощават, за да се смята текст или прът за по оста.
_AXIS_TOLERANCE_DEG = 2.0
#: Двата кандидата трябва да са взаимозаменяеми по дължина, за да ги разменим.
_AXIS_SWAP_LENGTH_TOLERANCE_CM = 1.0


def _axis_direction(points) -> tuple[float, float] | None:
    """Посоката на права от две точки, или ``None`` за чупена/затворена."""

    if len(points) != 2:
        return None
    (x0, y0), (x1, y1) = points[0], points[1]
    dx, dy = float(x1) - float(x0), float(y1) - float(y0)
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    return (dx / length, dy / length)


def _is_parallel(left, right, *, tolerance_deg: float = _AXIS_TOLERANCE_DEG) -> bool:
    cosine = abs(left[0] * right[0] + left[1] * right[1])
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) <= tolerance_deg


def _prefer_annotation_axis_candidate(
    annotation: DxfEntityRecord,
    analysis: GeometryCandidateAnalysis,
    context: GeometryCandidateContext,
) -> GeometryCandidateAnalysis:
    """Надпис по оста описва прът в СВОЯТА посока, не напряко.

    Собственик, 2026-09-19, върху „Плоча кота 14.90“: „тази позиция искам да
    я провериш, мисля че я дублира“.

    Не беше дубликат: `13N12x240` стои два пъти — веднъж хоризонтално и
    веднъж вертикално — защото описва мрежа 240 × 240 с по 13 пръта във
    всяка посока. Двата пръта се пресичат и са еднакво дълги.

    Но в 4 от 12-те такива надписа matcher-ът беше вързал КРЪСТОСАНО:
    хоризонталният надпис сочеше вертикалния прът и обратно. Побеждаваше
    разстоянието — при пресичащи се пръти то не е граница. Количествата не се
    менят (двата пръта са еднакви), но маркерът в DXF и проследимостта сочат
    желязото в другата посока — тиха грешка.

    Правилото разменя САМО взаимозаменяеми кандидати: надпис по оста, водещ
    кандидат една права НАПРЯКО, и точно един друг кандидат — също една права,
    успореден на надписа, с еднаква дължина и същата MASTER форма. Няма ли
    точно един такъв, редът остава както е бил.
    """

    candidates = tuple(analysis.candidates)
    if len(candidates) < 2:
        return analysis
    rotation = annotation.geometry.get("rotation")
    try:
        rotation = float(rotation)
    except (TypeError, ValueError):
        return analysis
    if abs((rotation % 90.0)) > _AXIS_TOLERANCE_DEG and abs(
        (rotation % 90.0) - 90.0
    ) > _AXIS_TOLERANCE_DEG:
        return analysis
    радиани = math.radians(rotation)
    посока_на_надписа = (math.cos(радиани), math.sin(радиани))

    def права(candidate):
        if len(candidate.handles) != 1:
            return None
        primitive = context.primitive_by_handle.get(str(candidate.handles[0]))
        if primitive is None or primitive.closed:
            return None
        return _axis_direction(primitive.points)

    водеща = права(candidates[0])
    if водеща is None or _is_parallel(водеща, посока_на_надписа):
        return analysis

    успоредни = [
        index
        for index, candidate in enumerate(candidates[1:], start=1)
        if (посока := права(candidate)) is not None
        and _is_parallel(посока, посока_на_надписа)
        and str(candidate.master_shape_code or "")
        == str(candidates[0].master_shape_code or "")
        and abs(
            float(candidate.geometry_length_raw)
            - float(candidates[0].geometry_length_raw)
        )
        <= _AXIS_SWAP_LENGTH_TOLERANCE_CM
    ]
    if len(успоредни) != 1:
        return analysis

    избран = успоредни[0]
    пренаредени = (
        candidates[избран],
        *candidates[:избран],
        *candidates[избран + 1 :],
    )
    return replace(analysis, candidates=пренаредени)


def _analysis_row(analysis: GeometryCandidateAnalysis, approved: dict | None) -> dict:
    top = analysis.candidates[0] if analysis.candidates else None
    approved_handles = tuple(
        sorted(str(value) for value in (approved or {}).get("geometry_handles", []))
    )
    top_handles = tuple(sorted(top.handles)) if top else ()
    origin = approval_origin(approved)
    # A geometry match against a previous AUTO record is evidence of stable
    # revalidation, not a manual approval.  Only explicit USER/OWNER links may
    # convert the current row to APPROVED_IN_PROJECT.
    approved_match = bool(
        origin == "MANUAL"
        and approved_handles
        and approved_handles == top_handles
    )

    if approved_match:
        batch_status = "ОДОБРЕНА ВРЪЗКА"
    elif top is None:
        batch_status = "НЯМА КАНДИДАТ"
    elif top.confidence == "СИЛЕН КАНДИДАТ — НЕОДОБРЕН":
        batch_status = "СИЛЕН"
    elif top.confidence == "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА":
        batch_status = "СИЛЕН ПО ГЕОМЕТРИЯ"
    elif top.confidence == F006_STRONG_CONFIDENCE:
        batch_status = "СИЛНО СТРЕМЕ F006"
    elif top.confidence == F006_HOOKED_FAMILY_CONFIDENCE:
        batch_status = "СИЛНО СТРЕМЕ F006"
    elif "РЪЧНА ПРОВЕРКА" in top.confidence:
        batch_status = "ЗА РЪЧЕН ПРЕГЛЕД"
    else:
        batch_status = "РИСК"

    return {
        "annotation_handle": analysis.annotation_handle,
        "annotation_text": analysis.label,
        "diameter_mm": analysis.diameter_mm,
        "declared_length_cm": analysis.declared_length_cm,
        "batch_status": batch_status,
        "analysis_status": analysis.status,
        "approved_in_project": approved_match,
        "approval_origin": origin,
        "previous_auto_approval_present": origin == "AUTO",
        "previous_auto_geometry_match": bool(
            origin == "AUTO"
            and approved_handles
            and approved_handles == top_handles
        ),
        "candidate_handles": list(top.handles) if top else [],
        "candidate_layer": top.layer if top else None,
        "segment_count": top.segment_count if top else 0,
        "geometry_length_raw": top.geometry_length_raw if top else None,
        "effective_geometry_length_cm": top.effective_geometry_length_cm if top else None,
        "raw_numeric_difference": top.raw_numeric_difference if top else None,
        "numeric_difference": top.numeric_difference if top else None,
        "distance_raw": top.distance_raw if top else None,
        "centroid_distance_raw": top.centroid_distance_raw if top else None,
        "layer_diameter_mm": top.layer_diameter_mm if top else None,
        "diameter_layer_match": top.diameter_layer_match if top else False,
        "candidate_confidence": top.confidence if top else None,
        "candidate_basis": top.basis if top else None,
        "candidate_score": top.score if top else None,
        "candidate_score_gap": top.score_gap if top else None,
        "candidate_distance_gap_raw": top.distance_gap_raw if top else None,
        "global_conflict": False,
        "master_conflict": bool(top and top.master_ambiguous_shape_codes),
        "candidate_notes": list(top.notes) if top else [],
        "canonical_signature": top.canonical_signature if top else None,
        "master_shape_code": top.master_shape_code if top else None,
        "master_shape_name": top.master_shape_name if top else None,
        "master_shape_mirrored": top.master_shape_mirrored if top else False,
        "master_shape_reversed": top.master_shape_reversed if top else False,
        "master_shape_score": top.master_shape_score if top else None,
        "mirror_requires_review": top.mirror_requires_review if top else False,
        "orientation_invariant": top.orientation_invariant if top else False,
        "master_ambiguous_shape_codes": (
            list(top.master_ambiguous_shape_codes) if top else []
        ),
        "master_reference_profile_id": (
            top.master_reference_profile_id if top else None
        ),
        "uniform_scale_factor": top.uniform_scale_factor if top else None,
        "dimension_evidence_applied": top.dimension_evidence_applied if top else False,
        "dimension_master_resolved": top.dimension_master_resolved if top else False,
        "dimension_original_ambiguous_shape_codes": (
            list(top.dimension_original_ambiguous_shape_codes) if top else []
        ),
        "dimension_text_handles": list(top.dimension_text_handles) if top else [],
        "dimension_resolution_strategy": (
            top.dimension_resolution_strategy if top else None
        ),
        "dimension_total_difference_cm": (
            top.dimension_total_difference_cm if top else None
        ),
        "local_f006_printed_dimension_handles": (
            list(top.local_f006_printed_dimension_handles) if top else []
        ),
        "review_reason_codes": list(top.review_reason_codes) if top else [],
        "contextual_rule_code": top.contextual_rule_code if top else None,
        "contextual_rule_note": top.contextual_rule_note if top else None,
        "near_gap_healed": top.near_gap_healed if top else False,
        "near_gap_bridge_gap_raw": top.near_gap_bridge_gap_raw if top else None,
        "near_gap_bridge_handles": list(top.near_gap_bridge_handles) if top else [],
        "near_gap_bridge_points": (
            [list(point) for point in top.near_gap_bridge_points] if top else []
        ),
        "geometry_representation": top.geometry_representation if top else None,
        "geometry_layer_role": top.geometry_layer_role if top else None,
        "source_curve_count": top.source_curve_count if top else 0,
        "normalized_bend_count": top.normalized_bend_count if top else 0,
        "unresolved_curve_count": top.unresolved_curve_count if top else 0,
        "source_arc_radii_raw": list(top.source_arc_radii_raw) if top else [],
        "hooked_closure": top.hooked_closure if top else False,
        "hooked_closure_gap_raw": top.hooked_closure_gap_raw if top else None,
        "canonical_segment_lengths_cm": list(top.canonical_segment_lengths_cm) if top else [],
        "canonical_turn_angles_deg": list(top.canonical_turn_angles_deg) if top else [],
        "analysis_warnings": list(analysis.warnings),
        "rejection_counts": dict(analysis.rejection_counts),
        "recognition_policy": dict(analysis.policy),
        "recognition_laws": [warning for warning in analysis.warnings if warning.startswith("Закон ")],
        "alternative_candidates": [candidate.to_dict() for candidate in analysis.candidates[1:]],
    }


def _write_disagreement_csv(path: Path, report: Mapping[str, Any]) -> None:
    """Находките на „Разногласия“ — по един ред на находка.

    Отделен файл, а не колона в общия CSV: една позиция може да се обади по
    повече от една проверка, а общият отчет е един ред на позиция.
    """

    fieldnames = [
        "check",
        "kind",
        "annotation_handle",
        "annotation_text",
        "batch_status",
        "explanation",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for finding in report.get("findings") or ():
            writer.writerow({key: finding.get(key, "") for key in fieldnames})


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "annotation_handle",
        "annotation_text",
        "annotation_type",
        "annotation_class",
        "annotation_count",
        "diameter_mm",
        "declared_length_cm",
        "data_quality_flags",
        "data_quality_notes",
        "quantity_review_required",
        "batch_status",
        "approved_in_project",
        "candidate_handles",
        "candidate_layer",
        "segment_count",
        "geometry_length_raw",
        "effective_geometry_length_cm",
        "raw_numeric_difference",
        "numeric_difference",
        "distance_raw",
        "centroid_distance_raw",
        "layer_diameter_mm",
        "diameter_layer_match",
        "candidate_confidence",
        "candidate_basis",
        "candidate_score",
        "candidate_score_gap",
        "candidate_distance_gap_raw",
        "candidate_original_confidence",
        "proxy_parent_provenance",
        "proxy_association_code",
        "proxy_annotation_parent_handle",
        "proxy_geometry_parent_handle",
        "proxy_geometry_handles",
        "proxy_position_key",
        "proxy_spacing_cm",
        "proxy_scale_factor",
        "proxy_exact_total_cm",
        "proxy_predecessor_distance",
        "proxy_modelspace_distance",
        "global_conflict",
        "geometry_owner_conflict",
        "ownership_reassigned",
        "ownership_reassignment_rank",
        "master_conflict",
        "candidate_notes",
        "canonical_signature",
        "master_shape_code",
        "master_shape_name",
        "master_shape_mirrored",
        "master_shape_reversed",
        "master_shape_score",
        "master_ambiguous_shape_codes",
        "master_reference_profile_id",
        "mirror_requires_review",
        "orientation_invariant",
        "uniform_scale_factor",
        "dimension_evidence_applied",
        "dimension_master_resolved",
        "dimension_original_ambiguous_shape_codes",
        "dimension_text_handles",
        "dimension_resolution_strategy",
        "dimension_total_difference_cm",
        "review_reason_codes",
        "contextual_rule_code",
        "named_u_subtype_context_resolved",
        "named_u_subtype_context_handles",
        "ownership_reference_only",
        "position_key_resolved",
        "position_key_manual_override",
        "position_key_resolution_code",
        "position_key_code",
        "position_key_table_circle_handle",
        "position_key_table_distance_raw",
        "position_key_legend_circle_handle",
        "position_key_legend_spec_handle",
        "position_key_legend_spec_text",
        "position_key_occurrence_count",
        "position_key_legend_geometry_handles",
        "position_key_legend_geometry_distance_raw",
        "position_key_original_batch_status",
        "position_key_original_rejection_counts",
        "position_key_original_candidate",
        "position_key_legend_reference",
        "stirrup_length_group_resolved",
        "stirrup_length_group_resolution_code",
        "stirrup_length_group_representative_handle",
        "stirrup_length_group_representative_distance_raw",
        "stirrup_length_group_member_handles",
        "stirrup_length_group_member_distances_raw",
        "stirrup_length_group_exact_arc_lengths_cm",
        "stirrup_length_group_declared_count",
        "stirrup_length_group_original_batch_status",
        "stirrup_length_group_original_rejection_counts",
        "stirrup_length_group_reference",
        "near_gap_healed",
        "near_gap_bridge_gap_raw",
        "near_gap_bridge_handles",
        "near_gap_bridge_points",
        "canonical_segment_lengths_cm",
        "canonical_turn_angles_deg",
        "matcher_disposition",
        "matcher_evidence_band",
        "form_classification_confidence",
        "geometry_ownership_confidence",
        "form_classification_evidence",
        "geometry_ownership_evidence",
        "matcher_blocker_codes",
        "matcher_decision_summary",
        "review_group_key",
        "review_group_size",
        "review_group_representative",
        "review_group_representative_handle",
        "review_group_member_handles",
        "review_group_member_index",
        "review_group_basis",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["candidate_handles"] = ", ".join(row["candidate_handles"])
            flat["candidate_notes"] = "; ".join(row["candidate_notes"])
            flat["proxy_geometry_handles"] = ", ".join(
                row.get("proxy_geometry_handles", [])
            )
            flat["master_ambiguous_shape_codes"] = ", ".join(
                row.get("master_ambiguous_shape_codes", [])
            )
            flat["review_reason_codes"] = ", ".join(row.get("review_reason_codes", []))
            flat["named_u_subtype_context_handles"] = ", ".join(
                row.get("named_u_subtype_context_handles", [])
            )
            flat["position_key_legend_geometry_handles"] = ", ".join(
                row.get("position_key_legend_geometry_handles", [])
            )
            flat["position_key_original_rejection_counts"] = json.dumps(
                row.get("position_key_original_rejection_counts", {}),
                ensure_ascii=False,
                sort_keys=True,
            )
            flat["position_key_original_candidate"] = json.dumps(
                row.get("position_key_original_candidate"),
                ensure_ascii=False,
                sort_keys=True,
            )
            flat["stirrup_length_group_member_handles"] = ", ".join(
                row.get("stirrup_length_group_member_handles", [])
            )
            flat["stirrup_length_group_member_distances_raw"] = ", ".join(
                str(value)
                for value in row.get(
                    "stirrup_length_group_member_distances_raw", []
                )
            )
            flat["stirrup_length_group_exact_arc_lengths_cm"] = ", ".join(
                str(value)
                for value in row.get(
                    "stirrup_length_group_exact_arc_lengths_cm", []
                )
            )
            flat["stirrup_length_group_original_rejection_counts"] = json.dumps(
                row.get("stirrup_length_group_original_rejection_counts", {}),
                ensure_ascii=False,
                sort_keys=True,
            )
            flat["matcher_blocker_codes"] = ", ".join(
                row.get("matcher_blocker_codes", [])
            )
            flat["form_classification_evidence"] = json.dumps(
                row.get("form_classification_evidence", []),
                ensure_ascii=False,
                sort_keys=True,
            )
            flat["geometry_ownership_evidence"] = json.dumps(
                row.get("geometry_ownership_evidence", []),
                ensure_ascii=False,
                sort_keys=True,
            )
            flat["review_group_member_handles"] = ", ".join(
                row.get("review_group_member_handles", [])
            )
            flat["review_group_basis"] = json.dumps(
                row.get("review_group_basis", {}),
                ensure_ascii=False,
                sort_keys=True,
            )
            flat["near_gap_bridge_handles"] = ", ".join(
                row.get("near_gap_bridge_handles", [])
            )
            flat["near_gap_bridge_points"] = "; ".join(
                f"{point[0]},{point[1]}"
                for point in row.get("near_gap_bridge_points", [])
                if isinstance(point, (list, tuple)) and len(point) >= 2
            )
            flat["data_quality_flags"] = "; ".join(row.get("data_quality_flags", []))
            flat["data_quality_notes"] = "; ".join(row.get("data_quality_notes", []))
            flat["canonical_segment_lengths_cm"] = ", ".join(str(v) for v in row.get("canonical_segment_lengths_cm", []))
            flat["canonical_turn_angles_deg"] = ", ".join(str(v) for v in row.get("canonical_turn_angles_deg", []))
            writer.writerow({field: flat.get(field) for field in fieldnames})


def _render_html(
    project_name: str,
    generated_at: datetime,
    rows: list[dict],
    counts: dict,
    *,
    total_annotations: int,
    cancelled: bool,
) -> str:
    cards = []
    group_counts = _review_group_counts(rows)
    laws = rows[0].get("recognition_laws", []) if rows else []
    laws_html = "".join(f"<li>{html.escape(str(law))}</li>" for law in laws)
    for index, row in enumerate(rows, start=1):
        status_class = {
            "ОДОБРЕНА ВРЪЗКА": "approved",
            "СИЛЕН": "strong",
            "СИЛЕН ПО ГЕОМЕТРИЯ": "geometry-strong",
            "СИЛНО СТРЕМЕ F006": "f006-strong",
            "ЗА РЪЧЕН ПРЕГЛЕД": "review",
            "РИСК": "risk",
            "НЯМА КАНДИДАТ": "none",
            "КОНТЕКСТЕН НАДПИС": "context",
            "НЕПЪЛЕН НАДПИС": "context",
            "НЕИЗЧИСЛИМА ДЪЛЖИНА": "context",
            "ГРЕШКА": "risk",
        }.get(row["batch_status"], "none")
        handles = ", ".join(row["candidate_handles"]) or "—"
        notes = "; ".join(row["candidate_notes"]) or "—"
        quality_flags = "; ".join(row.get("data_quality_flags", [])) or "—"
        quality_notes = "; ".join(row.get("data_quality_notes", [])) or "—"
        matcher_blockers = ", ".join(row.get("matcher_blocker_codes", [])) or "—"
        matcher_decision = row.get("matcher_decision")
        matcher_transforms = (
            matcher_decision.get("transforms", {})
            if isinstance(matcher_decision, dict)
            else {}
        )
        transform_explanation = (
            matcher_transforms.get("explanation")
            if isinstance(matcher_transforms, dict)
            else None
        )
        cards.append(
            f"""
            <section class="card {status_class}">
              <div class="card-head">
                <h2>{index}. {html.escape(str(row['annotation_text']))}</h2>
                <span class="badge">{html.escape(row['batch_status'])}</span>
              </div>
              <div class="grid">
                <div><b>Надпис handle:</b> {html.escape(str(row['annotation_handle']))}</div>
                <div><b>Тип надпис:</b> {html.escape(str(row.get('annotation_type') or '—'))}</div>
                <div><b>Клас:</b> {html.escape(str(row.get('annotation_class') or '—'))}</div>
                <div><b>Брой:</b> {_fmt(row.get('annotation_count'))}</div>
                <div><b>Кандидат:</b> {html.escape(handles)}</div>
                <div><b>Слой:</b> {html.escape(str(row['candidate_layer'] or '—'))}</div>
                <div><b>Сегменти:</b> {row['segment_count']}</div>
                <div><b>Сурова DXF L:</b> {_fmt(row['geometry_length_raw'])}</div>
                <div><b>Проектна L след правила:</b> {_fmt(row.get('effective_geometry_length_cm'))}</div>
                <div><b>Зададено L:</b> {_fmt(row['declared_length_cm'])}</div>
                <div><b>Сурова Δ:</b> {_fmt(row.get('raw_numeric_difference'))}</div>
                <div><b>Δ след правила:</b> {_fmt(row['numeric_difference'])}</div>
                <div><b>Разстояние:</b> {_fmt(row['distance_raw'])}</div>
                <div><b>Преднина по score:</b> {_fmt(row.get('candidate_score_gap'))}</div>
                <div><b>Преднина по дистанция:</b> {_fmt(row.get('candidate_distance_gap_raw'))}</div>
                <div><b>MASTER двусмислие:</b> {'ДА' if row.get('master_conflict') else 'НЕ'}</div>
                <div><b>DXF ownership конфликт:</b> {'ДА' if row.get('global_conflict') or row.get('geometry_owner_conflict') else 'НЕ'}</div>
                <div><b>Одобрено в проекта:</b> {'ДА' if row['approved_in_project'] else 'НЕ'}</div>
                <div><b>MASTER форма:</b> {html.escape(' / '.join(row.get('master_ambiguous_shape_codes', [])) or str(row['master_shape_code'] or '—'))} {html.escape(str(row['master_shape_name'] or ''))}</div>
                <div><b>Проектно правило:</b> {html.escape(str(row.get('contextual_rule_code') or '—'))}</div>
                <div><b>Ориентационен подпис:</b> {html.escape(str(row['canonical_signature'] or '—'))}</div>
                <div><b>Matcher решение:</b> {html.escape(str(row.get('matcher_disposition') or '—'))}</div>
                <div><b>Evidence band:</b> {html.escape(str(row.get('matcher_evidence_band') or '—'))}</div>
                <div><b>Увереност за MASTER формата:</b> {html.escape(str(row.get('form_classification_confidence') or '—'))}</div>
                <div><b>Увереност за DXF собствеността:</b> {html.escape(str(row.get('geometry_ownership_confidence') or '—'))}</div>
                <div><b>Група за преглед:</b> ×{int(row.get('review_group_size') or 1)}</div>
                <div><b>Представител:</b> {html.escape(str(row.get('review_group_representative_handle') or '—'))}</div>
              </div>
              <div class="preview">{row['svg_preview']}</div>
              <p><b>Блокиращи проверки на данните:</b> {html.escape(quality_flags)}</p>
              <p><b>Matcher blockers:</b> {html.escape(matcher_blockers)}</p>
              <p><b>Matcher обяснение:</b> {html.escape(str(row.get('matcher_decision_summary') or '—'))}</p>
              <p><b>Нормализирани трансформации:</b> {html.escape(str(transform_explanation or '—'))}</p>
              <p><b>Информационни бележки:</b> {html.escape(quality_notes)}</p>
              <p><b>Контрол:</b> {html.escape(notes)}</p>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<title>LION REBAR пакетен анализ</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f3f4f6; color: #111827; }}
header {{ background: #111827; color: white; padding: 20px 28px; }}
header h1 {{ margin: 0 0 6px; font-size: 24px; }}
.summary {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }}
.summary span {{ background: #374151; padding: 6px 10px; border-radius: 6px; }}
main {{ padding: 20px; max-width: 1400px; margin: auto; }}
.card {{ background: white; border-left: 8px solid #6b7280; border-radius: 8px; padding: 16px; margin-bottom: 18px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
.card.approved {{ border-color: #15803d; background:#f0fdf4; }}
.card.strong {{ border-color: #16a34a; }} .card.geometry-strong {{ border-color:#0f766e; background:#f0fdfa; }}
.card.f006-strong {{ border-color:#7c3aed; background:#faf5ff; }}
.card.review {{ border-color: #d97706; }} .card.context {{ border-color:#94a3b8; background:#f8fafc; }}
.card.risk {{ border-color: #dc2626; }} .card.none {{ border-color: #6b7280; }}
.card-head {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
.card-head h2 {{ margin:0; font-size:20px; }}
.badge {{ font-weight:700; background:#e5e7eb; padding:5px 9px; border-radius:999px; white-space:nowrap; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:8px 16px; margin:14px 0; }}
.preview {{ border:1px solid #d1d5db; background:white; overflow:auto; }}
svg {{ display:block; width:100%; min-height:260px; }}
p {{ margin-bottom:0; }}
details.laws {{ background:#ffffff; border:1px solid #d1d5db; border-radius:8px; padding:12px 16px; margin-bottom:18px; }}
details.laws summary {{ font-weight:700; cursor:pointer; }}
</style>
</head>
<body>
<header>
<h1>LION REBAR — пакетен геометричен анализ</h1>
<div>Проект: {html.escape(project_name)} | Генериран: {html.escape(generated_at.isoformat(timespec='seconds'))}</div>
<div class="summary">
<span>Обработени надписи: {len(rows)} / {total_annotations}</span><span>Реални позиции: {counts['actionable']}</span>
<span>С кандидат: {counts['candidate']}</span><span>Одобрени: {counts['approved']}</span><span>Силни: {counts['strong']}</span>
<span>Силни по геометрия: {counts['geometry_strong']}</span><span>Стремена F006: {counts['f006_strong']}</span><span>За преглед: {counts['review']}</span>
<span>Риск: {counts['risk']}</span><span>Без кандидат: {counts['none']}</span>
<span>Контекст/непълни: {counts['context']}</span>
<span>F003/F005 по текст: {counts['named_u_subtype_context_resolved']}</span>
<span>MASTER двусмислия: {counts['master_ambiguity']}</span>
<span>DXF ownership конфликти: {counts['ownership_conflict']}</span>
<span>Общо конфликтни позиции: {counts['conflict']}</span>
<span>Групи за преглед: {group_counts['actionable_groups']}</span>
<span>Спестени повторения: {group_counts['positions_saved']}</span>
<span>Грешки: {counts.get('error', 0)}</span>
<span>Статус: {'ПРЕКРАТЕН — ЧАСТИЧЕН ОТЧЕТ' if cancelled else 'ЗАВЪРШЕН'}</span>
</div>
</header>
<main><details class="laws" open><summary>Активни закони за разпознаване</summary><ol>{laws_html}</ol></details>{''.join(cards)}</main>
</body></html>"""


def _svg_preview(
    annotation: DxfEntityRecord,
    analysis: GeometryCandidateAnalysis | None,
    *,
    context: GeometryCandidateContext,
    selected_handles: Sequence[str] = (),
) -> str:
    if not selected_handles and analysis is not None and analysis.candidates:
        selected_handles = analysis.candidates[0].handles
    selected_handles = {str(handle) for handle in selected_handles}
    anchor = _point(annotation.geometry.get("visual_anchor")) or _point(
        annotation.geometry.get("insert")
    ) or (0.0, 0.0)

    selected_segments = [
        segment
        for handle in selected_handles
        for segment in context.segments_by_handle.get(handle, ())
    ]
    bounds_points = [(anchor[0], anchor[1])]
    for segment in selected_segments:
        bounds_points.extend([(segment[0], segment[1]), (segment[2], segment[3])])
    if len(bounds_points) == 1:
        nearest = context.nearby_segments(anchor, radius=150.0, limit=8)
        for _, segment in nearest:
            bounds_points.extend([(segment[0], segment[1]), (segment[2], segment[3])])

    min_x = min(point[0] for point in bounds_points)
    max_x = max(point[0] for point in bounds_points)
    min_y = min(point[1] for point in bounds_points)
    max_y = max(point[1] for point in bounds_points)
    span = max(max_x - min_x, max_y - min_y, 40.0)
    margin = span * 0.35
    min_x -= margin
    max_x += margin
    min_y -= margin
    max_y += margin

    nearby = context.segments_in_rect(min_x, min_y, max_x, max_y)
    width, height = 1000.0, 360.0
    scale = min(width / max(max_x - min_x, 1.0), height / max(max_y - min_y, 1.0))

    def sx(x: float) -> float:
        return (x - min_x) * scale

    def sy(y: float) -> float:
        return height - (y - min_y) * scale

    lines = []
    for handle, segment in nearby:
        selected = handle in selected_handles
        stroke = "#dc2626" if selected else "#cbd5e1"
        stroke_width = 6 if selected else 2
        lines.append(
            f'<line x1="{sx(segment[0]):.2f}" y1="{sy(segment[1]):.2f}" '
            f'x2="{sx(segment[2]):.2f}" y2="{sy(segment[3]):.2f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}" stroke-linecap="round" />'
        )
    label = html.escape(annotation.text or "")
    return (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="100%" height="100%" fill="white" />'
        + "".join(lines)
        + f'<circle cx="{sx(anchor[0]):.2f}" cy="{sy(anchor[1]):.2f}" r="8" fill="#2563eb" />'
        + f'<text x="{sx(anchor[0]) + 12:.2f}" y="{sy(anchor[1]) - 10:.2f}" '
        'font-family="Segoe UI, Arial" font-size="22" fill="#1d4ed8">'
        + label
        + "</text></svg>"
    )


def _entity_segments(entity: DxfEntityRecord) -> tuple[tuple[float, float, float, float], ...]:
    if entity.entity_type == "LINE":
        start = _point(entity.geometry.get("start"))
        end = _point(entity.geometry.get("end"))
        if start is None or end is None:
            return ()
        return ((start[0], start[1], end[0], end[1]),)
    if entity.entity_type in {"LWPOLYLINE", "POLYLINE"}:
        raw_points = entity.geometry.get("points")
        if not isinstance(raw_points, list):
            return ()
        points = [point for value in raw_points if (point := _point(value)) is not None]
        if bool(entity.geometry.get("closed")) and len(points) > 2 and points[0] != points[-1]:
            points.append(points[0])
        return tuple((a[0], a[1], b[0], b[1]) for a, b in pairwise(points))
    return ()


def _segment_intersects_rect(
    segment: tuple[float, float, float, float],
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> bool:
    x1, y1, x2, y2 = segment
    return not (max(x1, x2) < min_x or min(x1, x2) > max_x or max(y1, y2) < min_y or min(y1, y2) > max_y)


def _distance_point_to_segment(
    point: tuple[float, float],
    segment: tuple[float, float, float, float],
) -> float:
    px, py = point
    x1, y1, x2, y2 = segment
    dx, dy = x2 - x1, y2 - y1
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denominator))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _point(value) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_." else "_" for char in value.strip())
    return cleaned.strip("._") or "LION_REBAR_PROJECT"


def _fmt(value) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")

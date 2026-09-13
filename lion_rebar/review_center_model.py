from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from lion_rebar.dxf.geometry_candidates import (
    GeometryCandidate,
    GeometryCandidateAnalysis,
)
from lion_rebar.dxf.matcher_evidence import (
    build_review_group_key,
    review_group_components,
)
from lion_rebar.project.non_production import (
    is_valid_non_production_decision,
)

REPORT_SCHEMAS = {
    "lion-rebar-geometry-batch-report/v3",
}
STRONG_STATUS = "СИЛЕН"
GEOMETRY_STRONG_STATUS = "СИЛЕН ПО ГЕОМЕТРИЯ"
F006_STRONG_STATUS = "СИЛНО СТРЕМЕ F006"
MANUAL_STATUS = "ЗА РЪЧЕН ПРЕГЛЕД"
RISK_STATUS = "РИСК"
NO_CANDIDATE_STATUS = "НЯМА КАНДИДАТ"
APPROVED_STATUS = "ОДОБРЕНА ВРЪЗКА"
ACTIONABLE_CLASS = "ACTIONABLE"
MISSING_MASTER_STATUS = "ЛИПСВА MASTER ФОРМА"
POSITIVE_REVIEW_DECISIONS = frozenset(
    {
        "APPROVED",
        "APPROVED_BULK_AFTER_SAMPLE",
        "APPROVED_BULK_AFTER_GEOMETRY_SAMPLE",
        "APPROVED_BULK_AFTER_F006_SAMPLE",
        "APPROVED_BULK_FINAL_USER_CONFIRMATION",
        "MANUAL_ASSIGNED",
        "ENGINEER_RESOLVED",
    }
)
# These decisions are diagnostic records, not production resolutions. Legacy
# codes remain readable but no longer make a row disappear from the work queue.
DIAGNOSTIC_REVIEW_DECISIONS = frozenset(
    {"PROBLEM_RECORDED", "FLAGGED", "NO_VALID_CANDIDATE", "MISSING_MASTER"}
)
EXPLICIT_EXCLUSION_DECISIONS = frozenset({"EXCLUDED_BY_ENGINEER", "NON_PRODUCTION_POSITION"})
FINAL_REVIEW_DECISIONS = POSITIVE_REVIEW_DECISIONS | EXPLICIT_EXCLUSION_DECISIONS
REJECTED_REVIEW_DECISIONS = DIAGNOSTIC_REVIEW_DECISIONS | EXPLICIT_EXCLUSION_DECISIONS

# Locked MASTER profile used by the currently approved F009 definition.
# The report may contain legacy F009 labels produced by topology-only matching.
_FIXED_MASTER_SEGMENT_PROFILES: dict[str, tuple[float, ...]] = {
    "F009": (8.0, 79.0, 15.0, 79.0, 8.0),
}


def is_position_key_legend_reference(value: Mapping) -> bool:
    """Return whether geometry is a shared schematic legend reference.

    Legend geometry is evidence for multiple table annotations; it is not
    locally owned by any one annotation and must not enter uniqueness repair.
    """

    if bool(value.get("position_key_legend_reference")):
        return True
    reasons = {
        str(code).strip().upper()
        for code in (value.get("review_reason_codes") or ())
        if str(code).strip()
    }
    if "POSITION_KEY_LEGEND_REFERENCE" in reasons:
        return True
    if "POSITION_KEY_LEGEND_REFERENCE" in str(value.get("candidate_basis") or "").upper():
        return True
    return any(
        "POSITION_KEY_LEGEND_REFERENCE" in str(note).upper()
        or "легендна референция" in str(note).lower()
        for note in (value.get("candidate_notes") or ())
    )


@dataclass(frozen=True)
class ReviewReport:
    path: Path
    project_name: str
    generated_at: str
    rows: tuple[dict, ...]
    summary: dict
    data_identity: dict[str, Any]
    review_groups: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ControlSampleProgress:
    sample_handles: tuple[str, ...]
    approved: int
    pending: int
    problem_handles: tuple[str, ...]
    bulk_remaining: int

    @property
    def total(self) -> int:
        return len(self.sample_handles)

    @property
    def passed(self) -> bool:
        return (
            self.total > 0
            and self.approved == self.total
            and not self.problem_handles
        )


@dataclass(frozen=True)
class FinalConfirmationProgress:
    actionable: int
    approved_or_resolved: int
    safe_remaining: int
    engineering_remaining: int
    safe_handles: tuple[str, ...]
    engineering_handles: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.safe_remaining == 0 and self.engineering_remaining == 0

    @property
    def ready_for_final_confirmation(self) -> bool:
        return self.safe_remaining > 0 and self.engineering_remaining == 0


class ReviewReportError(RuntimeError):
    pass


def load_review_report(path: Path | str) -> ReviewReport:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewReportError(f"Отчетът не може да бъде прочетен: {report_path}\n{exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewReportError("Отчетът няма валидна JSON структура.")
    schema = str(payload.get("schema") or "")
    if schema not in REPORT_SCHEMAS:
        raise ReviewReportError(
            f"Неподдържана схема на отчет: {schema or 'липсва'}. Очаква се v3."
        )
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ReviewReportError("В отчета липсва списък rows.")
    normalized: list[dict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["annotation_handle"] = str(row.get("annotation_handle") or "")
        row["annotation_text"] = str(row.get("annotation_text") or "")
        row["batch_status"] = str(row.get("batch_status") or "")
        row["annotation_class"] = str(row.get("annotation_class") or "")
        row["candidate_handles"] = [
            str(value) for value in row.get("candidate_handles", []) if str(value)
        ]
        row["alternative_candidates"] = [
            dict(value)
            for value in row.get("alternative_candidates", [])
            if isinstance(value, dict)
        ]
        row["data_quality_flags"] = [
            str(value) for value in row.get("data_quality_flags", []) if str(value)
        ]
        row["data_quality_notes"] = [
            str(value) for value in row.get("data_quality_notes", []) if str(value)
        ]
        row["quantity_review_required"] = bool(row.get("quantity_review_required", False))
        row["candidate_notes"] = [
            str(value) for value in row.get("candidate_notes", []) if str(value)
        ]
        row["master_ambiguous_shape_codes"] = [
            str(value)
            for value in row.get("master_ambiguous_shape_codes", [])
            if str(value)
        ]
        row["review_reason_codes"] = [
            str(value) for value in row.get("review_reason_codes", []) if str(value)
        ]
        row["matcher_disposition"] = str(
            row.get("matcher_disposition") or "UNCLASSIFIED"
        )
        row["matcher_evidence_band"] = str(
            row.get("matcher_evidence_band") or "UNKNOWN"
        )
        matcher_decision = (
            row.get("matcher_decision")
            if isinstance(row.get("matcher_decision"), Mapping)
            else {}
        )
        row["form_classification_confidence"] = str(
            row.get("form_classification_confidence")
            or matcher_decision.get("form_classification_confidence")
            or row["matcher_evidence_band"]
        )
        row["geometry_ownership_confidence"] = str(
            row.get("geometry_ownership_confidence")
            or matcher_decision.get("geometry_ownership_confidence")
            or (
                "REVIEW"
                if bool(row.get("global_conflict"))
                else row["matcher_evidence_band"]
            )
        )
        row["form_classification_evidence"] = _evidence_rows(
            row.get("form_classification_evidence")
            or matcher_decision.get("form_classification_evidence")
        )
        row["geometry_ownership_evidence"] = _evidence_rows(
            row.get("geometry_ownership_evidence")
            or matcher_decision.get("geometry_ownership_evidence")
        )
        row["matcher_blocker_codes"] = [
            str(value) for value in row.get("matcher_blocker_codes", []) if str(value)
        ]
        row["matcher_decision_summary"] = str(
            row.get("matcher_decision_summary") or ""
        )
        row["master_conflict"] = bool(row.get("master_conflict", False))
        row["geometry_owner_conflict"] = bool(
            row.get("geometry_owner_conflict", row.get("global_conflict", False))
        )
        row["analysis_warnings"] = [
            str(value) for value in row.get("analysis_warnings", []) if str(value)
        ]
        normalized.append(row)
    _normalize_review_groups(normalized)
    group_metadata = _review_group_metadata(normalized)
    summary = dict(payload.get("summary") or {})
    master_ambiguities = sum(
        bool(row.get("master_conflict"))
        or bool(row.get("master_ambiguous_shape_codes"))
        for row in normalized
    )
    ownership_conflicts = sum(
        bool(row.get("global_conflict"))
        or bool(row.get("geometry_owner_conflict"))
        for row in normalized
    )
    total_conflicts = sum(
        bool(row.get("master_conflict"))
        or bool(row.get("master_ambiguous_shape_codes"))
        or bool(row.get("global_conflict"))
        or bool(row.get("geometry_owner_conflict"))
        for row in normalized
    )
    actionable = [
        row for row in normalized if row.get("annotation_class") == ACTIONABLE_CLASS
    ]
    actionable_groups = {
        str(row.get("review_group_key") or "") for row in actionable
    }
    summary.setdefault("master_ambiguities", master_ambiguities)
    summary.setdefault("ownership_conflicts", ownership_conflicts)
    summary.setdefault("total_conflicts", total_conflicts)
    # Keep the old, misleading name readable for callers of historical reports.
    summary.setdefault("global_conflicts", total_conflicts)
    summary.setdefault("review_groups", len(actionable_groups))
    summary.setdefault(
        "grouped_positions_saved",
        max(0, len(actionable) - len(actionable_groups)),
    )
    return ReviewReport(
        path=report_path,
        project_name=str(payload.get("project_name") or ""),
        generated_at=str(payload.get("generated_at") or ""),
        rows=tuple(normalized),
        summary=summary,
        data_identity=(
            dict(payload.get("data_identity") or {})
            if isinstance(payload.get("data_identity"), Mapping)
            else {}
        ),
        review_groups=tuple(group_metadata),
    )


def _evidence_rows(value: Any) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _normalize_review_groups(rows: Sequence[dict]) -> None:
    """Rebuild deterministic group metadata for new and historical reports."""

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


def _review_group_metadata(rows: Sequence[Mapping]) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        if (
            row.get("annotation_class") != ACTIONABLE_CLASS
            or not bool(row.get("review_group_representative"))
        ):
            continue
        result.append(
            {
                "review_group_key": str(row.get("review_group_key") or ""),
                "size": _safe_int(row.get("review_group_size"), 1),
                "representative_handle": str(
                    row.get("review_group_representative_handle") or ""
                ),
                "member_handles": [
                    str(value)
                    for value in row.get("review_group_member_handles", [])
                    if str(value)
                ],
                "basis": dict(row.get("review_group_basis") or {}),
            }
        )
    result.sort(key=lambda item: item["review_group_key"])
    return result


def review_group_representatives(rows: Sequence[Mapping]) -> list[dict]:
    """Return one deterministic representative per conservative review group.

    This is a presentation helper only.  A decision on the representative is
    not silently propagated to the other group members.
    """

    normalized = [dict(row) for row in rows]
    _normalize_review_groups(normalized)
    return [
        row for row in normalized if bool(row.get("review_group_representative"))
    ]


def actionable_rows(rows: Iterable[Mapping]) -> list[dict]:
    return [dict(row) for row in rows if str(row.get("annotation_class")) == ACTIONABLE_CLASS]


def build_stratified_sample(rows: Sequence[Mapping], *, size: int = 30) -> tuple[str, ...]:
    """Build a deterministic, shape/layer-stratified sample of strong candidates.

    The sample is intentionally limited to candidates with confirmed diameter from
    the DXF layer. Geometry-only candidates remain in a separate review queue and
    are never unlocked for bulk approval by this sample.
    """

    eligible = [dict(row) for row in rows if bulk_eligible(row)]
    if not eligible or size <= 0:
        return ()
    size = min(int(size), len(eligible))

    groups: dict[tuple[str, str, str, int], list[dict]] = {}
    for row in eligible:
        key = (
            str(row.get("batch_status") or ""),
            str(row.get("master_shape_code") or "NO_MASTER"),
            str(row.get("candidate_layer") or "NO_LAYER"),
            _safe_int(row.get("segment_count"), 0),
        )
        groups.setdefault(key, []).append(row)
    for values in groups.values():
        values.sort(key=_stable_row_key)

    # Rare groups first: they are easier to miss in a purely proportional sample.
    ordered_groups = sorted(groups.items(), key=lambda item: (len(item[1]), item[0]))
    selected: list[str] = []
    used: set[str] = set()

    for _key, values in ordered_groups:
        handle = _row_handle(values[0])
        if handle and handle not in used:
            selected.append(handle)
            used.add(handle)
            if len(selected) >= size:
                return tuple(selected)

    round_index = 1
    while len(selected) < size:
        added = False
        for _key, values in ordered_groups:
            if round_index >= len(values):
                continue
            handle = _row_handle(values[round_index])
            if handle and handle not in used:
                selected.append(handle)
                used.add(handle)
                added = True
                if len(selected) >= size:
                    return tuple(selected)
        if not added:
            break
        round_index += 1
    return tuple(selected)


def build_control_sample(
    rows: Sequence[Mapping],
    *,
    eligibility: Callable[[Mapping], bool],
    size: int,
    preferred_handles: Sequence[str] = (),
) -> tuple[str, ...]:
    """Build a deterministic control sample by MASTER form and segment count.

    ``preferred_handles`` is used only when a previous manual approval already
    exists. This preserves review progress during an update without allowing a
    row outside the strict eligibility gate into the sample.
    """

    eligible = [dict(row) for row in rows if eligibility(row)]
    if not eligible or size <= 0:
        return ()
    size = min(int(size), len(eligible))
    row_by_handle = {
        _row_handle(row): row for row in eligible if _row_handle(row)
    }
    groups: dict[tuple[str, int], list[dict]] = {}
    for row in eligible:
        key = (
            str(row.get("master_shape_code") or "NO_MASTER"),
            _safe_int(row.get("segment_count"), 0),
        )
        groups.setdefault(key, []).append(row)
    for values in groups.values():
        values.sort(key=_stable_row_key)

    selected: list[str] = []
    used: set[str] = set()

    for raw_handle in preferred_handles:
        handle = str(raw_handle)
        if handle in row_by_handle and handle not in used:
            selected.append(handle)
            used.add(handle)
            if len(selected) >= size:
                return tuple(selected)

    # Cover rare MASTER/segment combinations before adding repeated examples.
    ordered_groups = sorted(groups.items(), key=lambda item: (len(item[1]), item[0]))
    for _key, values in ordered_groups:
        handle = next(
            (_row_handle(row) for row in values if _row_handle(row) not in used),
            "",
        )
        if handle:
            selected.append(handle)
            used.add(handle)
            if len(selected) >= size:
                return tuple(selected)

    while len(selected) < size:
        added = False
        for _key, values in ordered_groups:
            handle = next(
                (_row_handle(row) for row in values if _row_handle(row) not in used),
                "",
            )
            if not handle:
                continue
            selected.append(handle)
            used.add(handle)
            added = True
            if len(selected) >= size:
                return tuple(selected)
        if not added:
            break
    return tuple(selected)


def control_sample_progress(
    rows: Sequence[Mapping],
    *,
    sample_handles: Sequence[str],
    eligibility: Callable[[Mapping], bool],
    approved_handles: Iterable[str] = (),
    decisions: Mapping | None = None,
) -> ControlSampleProgress:
    """Return unambiguous progress for one explicit bulk-approval sample."""

    approved_set = {str(value) for value in approved_handles}
    decisions = decisions if isinstance(decisions, Mapping) else {}
    eligible_rows = [dict(row) for row in rows if eligibility(row)]
    row_by_handle = {
        _row_handle(row): row for row in eligible_rows if _row_handle(row)
    }
    valid_sample = tuple(
        str(handle)
        for handle in sample_handles
        if str(handle) in row_by_handle
    )
    problem_handles = tuple(
        handle
        for handle in valid_sample
        if review_decision_code(decisions, handle) in REJECTED_REVIEW_DECISIONS
    )
    problem_set = set(problem_handles)
    approved = sum(
        handle in approved_set
        and handle not in problem_set
        and review_decision_code(decisions, handle) == "APPROVED"
        for handle in valid_sample
    )
    pending = max(0, len(valid_sample) - approved - len(problem_handles))
    sample_set = set(valid_sample)
    bulk_remaining = sum(
        _row_handle(row) not in sample_set
        and review_decision_code(decisions, _row_handle(row))
        not in DIAGNOSTIC_REVIEW_DECISIONS
        and not is_review_row_resolved(
            row,
            decision_code=review_decision_code(decisions, _row_handle(row)),
            decision_payload=(
                decisions.get(_row_handle(row), {})
                if isinstance(decisions.get(_row_handle(row), {}), Mapping)
                else {}
            ),
            approved_handles=approved_set,
        )
        for row in eligible_rows
    )
    return ControlSampleProgress(
        sample_handles=valid_sample,
        approved=approved,
        pending=pending,
        problem_handles=problem_handles,
        bulk_remaining=bulk_remaining,
    )


def bulk_eligible(row: Mapping) -> bool:
    """Strict gate for explicit bulk approval after a passed control sample."""

    if str(row.get("annotation_class")) != ACTIONABLE_CLASS:
        return False
    if str(row.get("batch_status")) != STRONG_STATUS:
        return False
    if str(row.get("matcher_disposition") or "") not in {
        "CONTROL",
        "AUTO_ELIGIBLE",
        "UNCLASSIFIED",
        "",
    }:
        return False
    if bool(row.get("global_conflict")):
        return False
    if bool(row.get("master_conflict")) or row.get("master_ambiguous_shape_codes"):
        return False
    if not bool(row.get("diameter_layer_match")):
        return False
    if not row.get("candidate_handles"):
        return False
    if row.get("data_quality_flags"):
        return False
    if not row.get("master_shape_code"):
        return False
    if not master_candidate_is_valid(row):
        return False

    count = _safe_int(row.get("annotation_count"), 0)
    declared = _safe_float(row.get("declared_length_cm"))
    difference = _safe_float(row.get("numeric_difference"))
    if count < 1 or count > 1000 or declared is None or declared <= 0 or declared > 2000:
        return False
    return difference is not None and abs(difference) <= max(1.0, declared * 0.005)


def geometry_bulk_eligible(row: Mapping) -> bool:
    """Gate for geometry-strong rows after a separate 10-position visual sample.

    These rows do not have a diameter confirmation from the DXF layer. The
    diameter is taken from the parsed annotation, so they are never mixed with
    the stricter confirmed-diameter bulk action.
    """

    if str(row.get("annotation_class")) != ACTIONABLE_CLASS:
        return False
    if str(row.get("batch_status")) != GEOMETRY_STRONG_STATUS:
        return False
    if str(row.get("matcher_disposition") or "") not in {
        "CONTROL",
        "AUTO_ELIGIBLE",
        "UNCLASSIFIED",
        "",
    }:
        return False
    if bool(row.get("global_conflict")):
        return False
    if bool(row.get("master_conflict")) or row.get("master_ambiguous_shape_codes"):
        return False
    if bool(row.get("diameter_layer_match")):
        # Confirmed-diameter rows belong to the stricter STRONG queue.
        return False
    if not row.get("candidate_handles"):
        return False
    if row.get("data_quality_flags"):
        return False
    if not row.get("master_shape_code"):
        return False
    if not master_candidate_is_valid(row):
        return False

    diameter = _safe_float(row.get("diameter_mm"))
    count = _safe_int(row.get("annotation_count"), 0)
    declared = _safe_float(row.get("declared_length_cm"))
    difference = _safe_float(row.get("numeric_difference"))
    if diameter is None or diameter <= 0 or diameter > 50:
        return False
    if count < 1 or count > 1000 or declared is None or declared <= 0 or declared > 2000:
        return False
    return difference is not None and abs(difference) <= max(1.0, declared * 0.005)



def f006_bulk_eligible(row: Mapping) -> bool:
    """Gate for auto-detected composite F006 stirrups after a five-row sample.

    The annotation x-value is not treated as unfolded length for this topology.
    Approval is based on the strict six-LINE geometry rule and a visual sample.
    """

    if str(row.get("annotation_class")) != ACTIONABLE_CLASS:
        return False
    if str(row.get("batch_status")) != F006_STRONG_STATUS:
        return False
    if str(row.get("matcher_disposition") or "") not in {
        "CONTROL",
        "AUTO_ELIGIBLE",
        "UNCLASSIFIED",
        "",
    }:
        return False
    if bool(row.get("global_conflict")):
        return False
    if bool(row.get("master_conflict")) or row.get("master_ambiguous_shape_codes"):
        return False
    if str(row.get("master_shape_code") or "").upper() != "F006":
        return False
    if str(row.get("contextual_rule_code") or "") != "F006_COMPOSITE_STIRRUP":
        return False
    handles = [str(value) for value in row.get("candidate_handles", []) if str(value)]
    if len(handles) != 6 or len(set(handles)) != 6:
        return False
    if _safe_int(row.get("segment_count"), 0) != 6:
        return False
    if row.get("data_quality_flags"):
        return False
    count = _safe_int(row.get("annotation_count"), 0)
    diameter = _safe_float(row.get("diameter_mm"))
    geometry_length = _safe_float(row.get("effective_geometry_length_cm"))
    if count < 1 or count > 1000:
        return False
    if diameter is None or diameter <= 0 or diameter > 50:
        return False
    return geometry_length is not None and 0 < geometry_length <= 2000

def final_confirmation_eligible(row: Mapping) -> bool:
    """Return whether an unresolved strong row may be explicitly confirmed in bulk.

    This is an owner action, not AUTO approval. It never accepts conflicts,
    missing/ambiguous MASTER forms, data-quality blockers or production-review rows.
    """

    if str(row.get("annotation_class") or "") != ACTIONABLE_CLASS:
        return False
    if str(row.get("batch_status") or "") not in {
        STRONG_STATUS,
        GEOMETRY_STRONG_STATUS,
        F006_STRONG_STATUS,
    }:
        return False
    if str(row.get("matcher_disposition") or "") not in {
        "CONTROL", "AUTO_ELIGIBLE", "UNCLASSIFIED", ""
    }:
        return False
    if bool(row.get("global_conflict")) or bool(row.get("geometry_owner_conflict")):
        return False
    if bool(row.get("master_conflict")) or row.get("master_ambiguous_shape_codes"):
        return False
    if is_missing_master_row(row) or not master_candidate_is_valid(row):
        return False
    if not row.get("candidate_handles") or not row.get("master_shape_code"):
        return False
    if row.get("data_quality_flags") or bool(row.get("quantity_review_required")):
        return False
    if str(row.get("f006_production_status") or "") == "F006_PRODUCTION_REVIEW":
        return False
    if bool(row.get("production_export_blocked")):
        return False
    count = _safe_int(row.get("annotation_count"), 0)
    diameter = _safe_float(row.get("diameter_mm"))
    declared = _safe_float(row.get("declared_length_cm"))
    if count < 1 or count > 1_000_000:
        return False
    if diameter is None or diameter <= 0 or diameter > 100:
        return False
    if declared is None or declared <= 0 or declared > 20_000:
        return False
    if str(row.get("batch_status") or "") == F006_STRONG_STATUS:
        return str(row.get("master_shape_code") or "").upper() == "F006"
    difference = _safe_float(row.get("numeric_difference"))
    return difference is not None and abs(difference) <= max(1.0, declared * 0.005)


def final_confirmation_progress(
    rows: Sequence[Mapping],
    *,
    decisions: Mapping | None = None,
    approved_handles: Iterable[str] = (),
) -> FinalConfirmationProgress:
    decisions = decisions if isinstance(decisions, Mapping) else {}
    approved_set = {str(value) for value in approved_handles}
    actionable = [
        dict(row) for row in rows
        if str(row.get("annotation_class") or "") == ACTIONABLE_CLASS
    ]
    safe: list[str] = []
    engineering: list[str] = []
    resolved_count = 0
    for row in actionable:
        handle = _row_handle(row)
        decision = review_decision_code(decisions, handle)
        if is_review_row_resolved(
            row,
            decision_code=decision,
            decision_payload=(
                decisions.get(handle, {})
                if isinstance(decisions.get(handle, {}), Mapping)
                else {}
            ),
            approved_handles=approved_set,
        ):
            resolved_count += 1
        elif decision in DIAGNOSTIC_REVIEW_DECISIONS:
            # A recorded problem is never swept into the final bulk confirmation,
            # even when the raw matcher row would otherwise pass the safe gate.
            engineering.append(handle)
        elif final_confirmation_eligible(row):
            safe.append(handle)
        else:
            engineering.append(handle)
    return FinalConfirmationProgress(
        actionable=len(actionable),
        approved_or_resolved=resolved_count,
        safe_remaining=len(safe),
        engineering_remaining=len(engineering),
        safe_handles=tuple(safe),
        engineering_handles=tuple(engineering),
    )


def queue_rows(
    rows: Sequence[Mapping],
    *,
    queue_code: str,
    sample_handles: Sequence[str] = (),
) -> list[dict]:
    actionable = [dict(row) for row in rows if str(row.get("annotation_class")) == ACTIONABLE_CLASS]
    sample_set = {str(value) for value in sample_handles}
    if queue_code == "SAMPLE":
        result = [row for row in actionable if _row_handle(row) in sample_set]
        order = {handle: index for index, handle in enumerate(sample_handles)}
        result.sort(key=lambda row: order.get(_row_handle(row), 10**9))
        return result
    if queue_code == "AI_EXCEPTIONS":
        return sorted(
            [
                row
                for row in actionable
                if str(row.get("matcher_disposition") or "")
                in {"MANUAL", "UNKNOWN"}
            ],
            key=_review_priority_key,
        )
    if queue_code == "AI_CONTROL":
        return sorted(
            [
                row
                for row in actionable
                if str(row.get("matcher_disposition") or "") == "CONTROL"
            ],
            key=_review_priority_key,
        )
    if queue_code == "MISSING_MASTER":
        return sorted(
            [row for row in actionable if is_missing_master_row(row)],
            key=_review_priority_key,
        )

    # A row with no valid MASTER match belongs only to the dedicated queue.
    supported = [row for row in actionable if not is_missing_master_row(row)]
    if queue_code == "F006_STRONG":
        return sorted(
            [row for row in supported if str(row.get("batch_status")) == F006_STRONG_STATUS],
            key=_review_priority_key,
        )
    if queue_code == "CONFLICT":
        return sorted(
            [
                row
                for row in supported
                if bool(row.get("global_conflict")) or bool(row.get("master_conflict"))
            ],
            key=_review_priority_key,
        )
    if queue_code == "MANUAL":
        return sorted(
            [
                row
                for row in supported
                if str(row.get("batch_status")) == MANUAL_STATUS
                and not bool(row.get("global_conflict"))
                and not bool(row.get("master_conflict"))
            ],
            key=_review_priority_key,
        )
    if queue_code == "RISK":
        return sorted(
            [row for row in supported if str(row.get("batch_status")) == RISK_STATUS],
            key=_review_priority_key,
        )
    if queue_code == "NONE":
        return sorted(
            [row for row in supported if str(row.get("batch_status")) == NO_CANDIDATE_STATUS],
            key=_review_priority_key,
        )
    if queue_code == "GEOMETRY_STRONG":
        return sorted(
            [row for row in supported if str(row.get("batch_status")) == GEOMETRY_STRONG_STATUS],
            key=_review_priority_key,
        )
    if queue_code == "STRONG":
        return sorted(
            [row for row in supported if str(row.get("batch_status")) == STRONG_STATUS],
            key=_review_priority_key,
        )
    if queue_code == "APPROVED":
        return sorted(
            [row for row in actionable if str(row.get("batch_status")) == APPROVED_STATUS],
            key=_review_priority_key,
        )
    if queue_code == "ALL":
        return sorted(actionable, key=_review_priority_key)
    return []


def review_decision_code(decisions: Mapping, annotation_handle: str) -> str:
    """Return one normalized Review Center decision code for a report row."""

    raw = decisions.get(str(annotation_handle), {}) if isinstance(decisions, Mapping) else {}
    return str(raw.get("decision") or "") if isinstance(raw, Mapping) else ""


def is_review_row_approved(
    row: Mapping,
    *,
    decision_code: str = "",
    approved_handles: Iterable[str] = (),
) -> bool:
    """Return whether a row has a positive final decision.

    A later problem decision always wins over an older approval link.  This
    mirrors the project-integrity rule and prevents rejected rows from leaking
    back into the approved history queue.
    """

    decision = str(decision_code or "")
    if decision in REJECTED_REVIEW_DECISIONS:
        return False
    if decision in POSITIVE_REVIEW_DECISIONS or decision.startswith("APPROVED"):
        return True
    handle = _row_handle(row)
    approved_set = {str(value) for value in approved_handles}
    return handle in approved_set or str(row.get("batch_status")) == APPROVED_STATUS


def is_review_row_resolved(
    row: Mapping,
    *,
    decision_code: str = "",
    decision_payload: Mapping | None = None,
    approved_handles: Iterable[str] = (),
) -> bool:
    """Return whether the row no longer needs an operator decision."""

    if str(row.get("annotation_class")) != ACTIONABLE_CLASS:
        return True
    decision = str(decision_code or "")
    if decision in DIAGNOSTIC_REVIEW_DECISIONS:
        return False
    if decision == "NON_PRODUCTION_POSITION":
        return is_valid_non_production_decision(decision_payload)
    if decision in FINAL_REVIEW_DECISIONS:
        return True
    return is_review_row_approved(
        row,
        decision_code=decision_code,
        approved_handles=approved_handles,
    )


#: Фаза 2 (09.09.2026): консолидирани изгледи в Review Center dropdown-а.
#: "PROBLEMS" обединява всичко, което изисква истинска инженерна преценка;
#: "STRONG_REVIEW" обединява автоматично силните категории за бърз преглед
#: преди bulk-approve. Всеки от старите кодове по-долу остава изцяло
#: функционален и тестван самостоятелно — сливането е реализирано като
#: обединение на резултатите им, не като нова филтърна логика, за да не се
#: дублира риск от разминаване.
_PROBLEM_QUEUE_CODES: tuple[str, ...] = (
    "AI_EXCEPTIONS", "MISSING_MASTER", "CONFLICT", "MANUAL", "RISK", "NONE",
)
_STRONG_REVIEW_QUEUE_CODES: tuple[str, ...] = (
    "AI_CONTROL", "GEOMETRY_STRONG", "STRONG", "F006_STRONG",
)


def review_queue_rows(
    rows: Sequence[Mapping],
    *,
    queue_code: str,
    sample_handles: Sequence[str] = (),
    geometry_sample_handles: Sequence[str] = (),
    f006_sample_handles: Sequence[str] = (),
    decisions: Mapping | None = None,
    approved_handles: Iterable[str] = (),
) -> list[dict]:
    """Build one live Review Center queue.

    Work queues contain only unresolved rows.  ``APPROVED``, ``REJECTED`` and
    ``ALL`` are history queues and intentionally retain completed decisions.
    This single function is used for both the table and every displayed count,
    so queue badges cannot drift after an approval or manual correction.
    """

    decisions = decisions if isinstance(decisions, Mapping) else {}
    approved_set = {str(value) for value in approved_handles}
    actionable = [
        dict(row)
        for row in rows
        if str(row.get("annotation_class")) == ACTIONABLE_CLASS
    ]

    def decision_for(row: Mapping) -> str:
        return review_decision_code(decisions, _row_handle(row))

    def decision_payload_for(row: Mapping) -> Mapping:
        value = decisions.get(_row_handle(row), {})
        return value if isinstance(value, Mapping) else {}

    def resolved(row: Mapping) -> bool:
        return is_review_row_resolved(
            row,
            decision_code=decision_for(row),
            decision_payload=decision_payload_for(row),
            approved_handles=approved_set,
        )

    if queue_code in {"PROBLEMS", "STRONG_REVIEW"}:
        sub_codes = (
            _PROBLEM_QUEUE_CODES
            if queue_code == "PROBLEMS"
            else _STRONG_REVIEW_QUEUE_CODES
        )
        merged: dict[str, dict] = {}
        for sub_code in sub_codes:
            for row in review_queue_rows(
                rows,
                queue_code=sub_code,
                sample_handles=sample_handles,
                geometry_sample_handles=geometry_sample_handles,
                f006_sample_handles=f006_sample_handles,
                decisions=decisions,
                approved_handles=approved_handles,
            ):
                merged[_row_handle(row)] = row
        return sorted(merged.values(), key=_review_priority_key)

    if queue_code in {"GEOMETRY_SAMPLE", "F006_SAMPLE"}:
        selected_handles = (
            geometry_sample_handles
            if queue_code == "GEOMETRY_SAMPLE"
            else f006_sample_handles
        )
        order = {
            str(handle): index
            for index, handle in enumerate(selected_handles)
            if str(handle)
        }
        selected = [
            row for row in actionable if _row_handle(row) in order
        ]
        # Pending controls appear first. Approved/problem rows remain visible
        # at the bottom as an audit of the fixed sample.
        selected.sort(
            key=lambda row: (
                1 if resolved(row) else 0,
                order.get(_row_handle(row), 10**9),
            )
        )
        return selected

    if queue_code == "APPROVED":
        return sorted(
            [
                row
                for row in actionable
                if is_review_row_approved(
                    row,
                    decision_code=decision_for(row),
                    approved_handles=approved_set,
                )
            ],
            key=_review_priority_key,
        )
    if queue_code == "REJECTED":
        return sorted(
            [
                row
                for row in actionable
                if decision_for(row) in REJECTED_REVIEW_DECISIONS
            ],
            key=_review_priority_key,
        )
    if queue_code == "ALL":
        return queue_rows(
            rows,
            queue_code="ALL",
            sample_handles=sample_handles,
        )
    if queue_code == "GROUPS":
        unresolved = [row for row in actionable if not resolved(row)]
        by_group: dict[str, list[dict]] = {}
        for row in unresolved:
            key = str(row.get("review_group_key") or build_review_group_key(row))
            by_group.setdefault(key, []).append(row)
        representatives: list[dict] = []
        for key, members in by_group.items():
            ordered = sorted(members, key=_review_priority_key)
            fixed = next(
                (
                    row
                    for row in ordered
                    if bool(row.get("review_group_representative"))
                ),
                None,
            )
            representative = dict(fixed or ordered[0])
            representative["review_group_key"] = key
            representative["review_group_unresolved_count"] = len(members)
            representative["review_group_display_representative"] = True
            representative["review_group_display_representative_handle"] = (
                _row_handle(representative)
            )
            representatives.append(representative)
        representatives.sort(
            key=lambda row: (
                _work_queue_priority(row),
                _review_priority_key(row),
            )
        )
        return representatives
    if queue_code == "WORK":
        combined_samples = tuple(sample_handles) + tuple(geometry_sample_handles) + tuple(
            f006_sample_handles
        )
        sample_order = {
            str(handle): index
            for index, handle in enumerate(combined_samples)
            if str(handle)
        }
        unresolved = [row for row in actionable if not resolved(row)]
        unresolved.sort(
            key=lambda row: (
                0 if _row_handle(row) in sample_order else 1,
                sample_order.get(_row_handle(row), 10**9),
                _work_queue_priority(row),
                _review_priority_key(row),
            )
        )
        return unresolved

    if queue_code == "MANUAL":
        # A diagnostic problem remains an active engineering task even when
        # its original batch status was NO_CANDIDATE/RISK/etc.  Keep it in the
        # manual work queue until a complete ENGINEER_RESOLVED link is saved.
        candidates = [
            row
            for row in actionable
            if (
                str(row.get("batch_status")) == MANUAL_STATUS
                or decision_for(row) in DIAGNOSTIC_REVIEW_DECISIONS
            )
            and not bool(row.get("global_conflict"))
            and not bool(row.get("master_conflict"))
        ]
        return sorted(
            [row for row in candidates if not resolved(row)],
            key=_review_priority_key,
        )

    return [
        row
        for row in queue_rows(
            rows,
            queue_code=queue_code,
            sample_handles=sample_handles,
        )
        if not resolved(row)
    ]


def build_review_evidence_note(
    row: Mapping,
    *,
    reason_label: str | None = None,
) -> str:
    """Build a concise, reproducible technical note from report evidence."""

    parts: list[str] = []
    annotation = str(row.get("annotation_text") or "").strip()
    if annotation:
        parts.append(f"Надпис {annotation}")

    handles = [str(value) for value in row.get("candidate_handles", []) if str(value)]
    if handles:
        parts.append(f"кандидат {', '.join(handles)}")
    else:
        parts.append("без геометричен кандидат")

    shape_codes = [
        str(value)
        for value in row.get("master_ambiguous_shape_codes", [])
        if str(value)
    ]
    shape = " / ".join(shape_codes) or str(row.get("master_shape_code") or "").strip()
    if shape:
        parts.append(f"MASTER {shape}")

    segment_count = _safe_int(row.get("segment_count"), 0)
    if segment_count > 0:
        parts.append(f"{segment_count} сегм.")
    layer = str(row.get("candidate_layer") or "").strip()
    if layer:
        parts.append(f"слой {layer}")

    geometry = _safe_float(
        row.get("geometry_length_raw")
        if row.get("geometry_length_raw") is not None
        else row.get("effective_geometry_length_cm")
    )
    declared = _safe_float(row.get("declared_length_cm"))
    difference = _safe_float(
        row.get("raw_numeric_difference")
        if row.get("raw_numeric_difference") is not None
        else row.get("numeric_difference")
    )
    if geometry is not None:
        parts.append(f"DXF L={_evidence_number(geometry)} cm")
    if declared is not None:
        parts.append(f"зададена L={_evidence_number(declared)} cm")
    if difference is not None:
        parts.append(f"Δ={_evidence_number(difference, signed=True)} cm")

    flags = [
        str(value)
        for value in row.get("data_quality_flags", [])
        if str(value)
    ]
    if flags:
        parts.append(f"контрол: {', '.join(flags[:2])}")
    if reason_label:
        parts.append(f"причина: {str(reason_label).strip()}")
    return "; ".join(parts).rstrip("; ") + "."


def master_candidate_is_valid(row: Mapping) -> bool:
    """Return True only when the reported MASTER match is production-usable.

    v0.1.18 reports can contain legacy F009 matches created from topology alone.
    F009 is a locked absolute profile (8+79+15+79+8=189 cm), therefore both
    the total and the segment ratios must agree with that approved profile.
    """

    code = str(row.get("master_shape_code") or "").strip().upper()
    if not code:
        return False
    profile = _FIXED_MASTER_SEGMENT_PROFILES.get(code)
    if profile is None:
        return True

    geometry_length = _safe_float(row.get("geometry_length_raw"))
    if geometry_length is None:
        return False
    expected_total = sum(profile)
    if abs(geometry_length - expected_total) > max(2.0, expected_total * 0.01):
        return False

    ratios = _ratios_from_signature(str(row.get("canonical_signature") or ""))
    if len(ratios) != len(profile):
        return False
    expected = tuple(value / expected_total for value in profile)
    ratio_error = min(
        max(abs(left - right) for left, right in zip(ratios, expected)),
        max(abs(left - right) for left, right in zip(tuple(reversed(ratios)), expected)),
    )
    return ratio_error <= 0.03


def is_missing_master_row(row: Mapping) -> bool:
    if str(row.get("annotation_class")) != ACTIONABLE_CLASS:
        return False
    if str(row.get("batch_status")) == APPROVED_STATUS:
        return False
    if not row.get("candidate_handles"):
        return False
    return not master_candidate_is_valid(row)


def missing_master_reason(row: Mapping) -> str:
    code = str(row.get("master_shape_code") or "").strip().upper()
    if not code:
        return "Кандидатът няма съвпадение с активна MASTER форма."
    if code == "F009":
        return (
            "Геометрията е означена като F009 от стария topology-only matcher, "
            "но не съвпада с заключения профил 8+79+15+79+8=189 cm."
        )
    return f"Кандидатът сочи неподдържана MASTER форма {code}."


def _ratios_from_signature(signature: str) -> tuple[float, ...]:
    marker = "|r="
    if marker not in signature:
        return ()
    raw = signature.split(marker, 1)[1].split("|", 1)[0]
    output: list[float] = []
    for part in raw.split(","):
        value = _safe_float(part)
        if value is None:
            return ()
        output.append(value)
    return tuple(output)


def geometry_candidate_from_report_row(row: Mapping) -> GeometryCandidate | None:
    handles = tuple(str(value) for value in row.get("candidate_handles", []) if str(value))
    if not handles:
        return None
    declared = _safe_float(row.get("declared_length_cm"))
    geometry_length = _safe_float(row.get("geometry_length_raw"))
    difference = _safe_float(row.get("numeric_difference"))
    distance = _safe_float(row.get("distance_raw"))
    if declared is None or geometry_length is None or difference is None or distance is None:
        return None
    return GeometryCandidate(
        rank=1,
        handles=handles,
        layer=str(row.get("candidate_layer") or ""),
        segment_count=_safe_int(row.get("segment_count"), len(handles)),
        geometry_length_raw=geometry_length,
        declared_length_cm=declared,
        numeric_difference=difference,
        distance_raw=distance,
        layer_diameter_mm=_safe_float(row.get("layer_diameter_mm")),
        diameter_layer_match=bool(row.get("diameter_layer_match")),
        score=_safe_float(row.get("candidate_score")) or 0.0,
        score_gap=_safe_float(row.get("candidate_score_gap")),
        distance_gap_raw=_safe_float(row.get("candidate_distance_gap_raw")),
        confidence=str(row.get("candidate_confidence") or row.get("batch_status") or ""),
        basis=str(row.get("candidate_basis") or "ПАКЕТЕН ОТЧЕТ"),
        notes=tuple(str(value) for value in row.get("candidate_notes", []) if str(value)),
        canonical_signature=_optional_text(row.get("canonical_signature")),
        master_shape_code=_optional_text(row.get("master_shape_code")),
        master_shape_name=_optional_text(row.get("master_shape_name")),
        master_shape_mirrored=bool(row.get("master_shape_mirrored")),
        master_reference_profile_id=_optional_text(row.get("master_reference_profile_id")),
        master_ambiguous_shape_codes=tuple(
            str(value)
            for value in row.get("master_ambiguous_shape_codes", [])
            if str(value)
        ),
        uniform_scale_factor=_safe_float(row.get("uniform_scale_factor")),
        mirror_requires_review=bool(row.get("mirror_requires_review")),
        review_reason_codes=tuple(
            str(value) for value in row.get("review_reason_codes", []) if str(value)
        ),
        effective_geometry_length_cm=_safe_float(row.get("effective_geometry_length_cm")),
        raw_numeric_difference=_safe_float(row.get("raw_numeric_difference")),
        contextual_rule_code=_optional_text(row.get("contextual_rule_code")),
        contextual_rule_note=_optional_text(row.get("contextual_rule_note")),
        canonical_segment_lengths_cm=tuple(
            value
            for raw in row.get("canonical_segment_lengths_cm", [])
            if (value := _safe_float(raw)) is not None
        ),
        canonical_turn_angles_deg=tuple(
            value
            for raw in row.get("canonical_turn_angles_deg", [])
            if (value := _safe_float(raw)) is not None
        ),
    )


def geometry_analysis_from_report_row(row: Mapping) -> GeometryCandidateAnalysis:
    candidates: list[GeometryCandidate] = []
    top = geometry_candidate_from_report_row(row)
    if top is not None:
        candidates.append(top)
    for index, raw in enumerate(row.get("alternative_candidates", []), start=2):
        if not isinstance(raw, Mapping):
            continue
        candidate = _candidate_from_mapping(raw, rank=index)
        if candidate is not None:
            candidates.append(candidate)
    return GeometryCandidateAnalysis(
        annotation_handle=_row_handle(row),
        label=str(row.get("annotation_text") or ""),
        diameter_mm=_safe_float(row.get("diameter_mm")),
        declared_length_cm=_safe_float(row.get("declared_length_cm")),
        candidates=tuple(candidates),
        status=str(row.get("analysis_status") or row.get("batch_status") or ""),
        warnings=tuple(str(value) for value in row.get("analysis_warnings", []) if str(value)),
        rejection_counts={
            str(key): _safe_int(value, 0)
            for key, value in dict(row.get("rejection_counts") or {}).items()
        },
        policy=dict(row.get("recognition_policy") or {}),
    )


def report_fingerprint(report: ReviewReport) -> str:
    payload = f"{report.project_name}|{report.generated_at}|{len(report.rows)}|{report.path.name}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_from_mapping(raw: Mapping, *, rank: int) -> GeometryCandidate | None:
    handles = tuple(str(value) for value in raw.get("handles", []) if str(value))
    declared = _safe_float(raw.get("declared_length_cm"))
    geometry = _safe_float(raw.get("geometry_length_raw"))
    difference = _safe_float(raw.get("numeric_difference"))
    distance = _safe_float(raw.get("distance_raw"))
    if not handles or declared is None or geometry is None or difference is None or distance is None:
        return None
    return GeometryCandidate(
        rank=_safe_int(raw.get("rank"), rank),
        handles=handles,
        layer=str(raw.get("layer") or ""),
        segment_count=_safe_int(raw.get("segment_count"), len(handles)),
        geometry_length_raw=geometry,
        declared_length_cm=declared,
        numeric_difference=difference,
        distance_raw=distance,
        layer_diameter_mm=_safe_float(raw.get("layer_diameter_mm")),
        diameter_layer_match=bool(raw.get("diameter_layer_match")),
        score=_safe_float(raw.get("score")) or 0.0,
        score_gap=_safe_float(raw.get("score_gap")),
        distance_gap_raw=_safe_float(raw.get("distance_gap_raw")),
        confidence=str(raw.get("confidence") or "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"),
        basis=str(raw.get("basis") or "ПАКЕТЕН ОТЧЕТ"),
        notes=tuple(str(value) for value in raw.get("notes", []) if str(value)),
        centroid_distance_raw=_safe_float(raw.get("centroid_distance_raw")),
        canonical_signature=_optional_text(raw.get("canonical_signature")),
        orientation_invariant=bool(raw.get("orientation_invariant", True)),
        reversed_direction_equivalent=bool(raw.get("reversed_direction_equivalent", True)),
        master_shape_code=_optional_text(raw.get("master_shape_code")),
        master_shape_name=_optional_text(raw.get("master_shape_name")),
        master_shape_revision=_safe_int_or_none(raw.get("master_shape_revision")),
        master_shape_mirrored=bool(raw.get("master_shape_mirrored")),
        master_shape_reversed=bool(raw.get("master_shape_reversed")),
        master_shape_score=_safe_float(raw.get("master_shape_score")),
        master_reference_profile_id=_optional_text(raw.get("master_reference_profile_id")),
        master_ambiguous_shape_codes=tuple(
            str(value)
            for value in raw.get("master_ambiguous_shape_codes", [])
            if str(value)
        ),
        uniform_scale_factor=_safe_float(raw.get("uniform_scale_factor")),
        mirror_requires_review=bool(raw.get("mirror_requires_review")),
        review_reason_codes=tuple(
            str(value) for value in raw.get("review_reason_codes", []) if str(value)
        ),
        effective_geometry_length_cm=_safe_float(raw.get("effective_geometry_length_cm")),
        raw_numeric_difference=_safe_float(raw.get("raw_numeric_difference")),
        contextual_rule_code=_optional_text(raw.get("contextual_rule_code")),
        contextual_rule_note=_optional_text(raw.get("contextual_rule_note")),
        canonical_segment_lengths_cm=tuple(
            value
            for item in raw.get("canonical_segment_lengths_cm", [])
            if (value := _safe_float(item)) is not None
        ),
        canonical_turn_angles_deg=tuple(
            value
            for item in raw.get("canonical_turn_angles_deg", [])
            if (value := _safe_float(item)) is not None
        ),
    )


def _review_priority_key(row: Mapping) -> tuple:
    return (
        0 if bool(row.get("global_conflict")) or bool(row.get("master_conflict")) else 1,
        0 if row.get("data_quality_flags") else 1,
        abs(_safe_float(row.get("numeric_difference")) or 10**9),
        _safe_float(row.get("distance_raw")) or 10**9,
        _stable_row_key(row),
    )


def _work_queue_priority(row: Mapping) -> int:
    disposition = str(row.get("matcher_disposition") or "")
    if disposition == "UNKNOWN":
        return 0
    if disposition == "MANUAL":
        return 1
    if is_missing_master_row(row):
        return 2
    if bool(row.get("global_conflict")) or bool(row.get("master_conflict")):
        return 3
    status = str(row.get("batch_status") or "")
    return {
        NO_CANDIDATE_STATUS: 4,
        RISK_STATUS: 5,
        MANUAL_STATUS: 6,
        GEOMETRY_STRONG_STATUS: 7,
        F006_STRONG_STATUS: 8,
        STRONG_STATUS: 9,
    }.get(status, 10)


def _stable_row_key(row: Mapping) -> str:
    raw = f"{_row_handle(row)}|{row.get('annotation_text', '')}|{row.get('candidate_handles', [])}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_handle(row: Mapping) -> str:
    return str(row.get("annotation_handle") or "")


def _safe_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _evidence_number(value: float, *, signed: bool = False) -> str:
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        text = str(round(number))
    else:
        text = f"{number:.3f}".rstrip("0").rstrip(".")
    if signed and number > 0:
        return f"+{text}"
    return text

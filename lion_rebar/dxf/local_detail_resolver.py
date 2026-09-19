from __future__ import annotations

"""Conservative local detail association for beam reinforcement callouts.

Some beam drawings split one engineering detail across several nearby DXF
texts: a semantic prefix (``стр.:`` or ``фиби.:``), an ``ARM_N`` block with
count/diameter/length, and one or more numeric dimension texts around the
sketch.  Global proximity alone can let a neighbouring callout borrow the
other detail's geometry or dimensions.

This module builds a small, auditable local ownership index before geometric
matching.  It never creates a shape, length, or approval.  It only records:

* which explicit semantic prefix belongs to which full annotation; and
* which nearby numeric texts are locally associated with that annotation.

Ambiguous associations remain unowned and therefore cannot silently become
exclusive evidence.
"""

import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.text_parser import parse_rebar_text

Point2D = tuple[float, float]
Rect = tuple[float, float, float, float]

_NUMERIC_RE = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*(?:CM)?\s*", re.IGNORECASE)


@dataclass(frozen=True)
class LocalDetailAssignment:
    annotation_handle: str
    shape_code: str
    label_handle: str
    label_text: str
    label_distance_raw: float
    dimension_handles: tuple[str, ...] = ()
    exclusive_dimension_handles: tuple[str, ...] = ()

    @property
    def rule_code(self) -> str:
        if self.shape_code == "F007":
            return "LOCAL_DETAIL_LABEL_F007"
        if self.shape_code == "F005":
            return "LOCAL_DETAIL_LABEL_F005"
        if self.shape_code == "F006":
            return "LOCAL_DETAIL_LABEL_F006"
        return "LOCAL_DETAIL_LABEL"

    @property
    def note(self) -> str:
        return (
            f"{self.rule_code}: локалният надпис {self.label_handle} "
            f"({self.label_text!r}) е еднозначно свързан с "
            f"{self.annotation_handle} на разстояние "
            f"{self.label_distance_raw:.3f} чертожни единици."
        )


@dataclass(frozen=True)
class LocalDetailIndex:
    assignments: Mapping[str, LocalDetailAssignment]
    exclusive_dimension_owner: Mapping[str, str]

    def assignment_for(self, annotation_handle: str) -> LocalDetailAssignment | None:
        return self.assignments.get(str(annotation_handle))

    def excluded_dimension_handles_for(self, annotation_handle: str) -> tuple[str, ...]:
        owner = str(annotation_handle)
        return tuple(
            sorted(
                handle
                for handle, assigned_owner in self.exclusive_dimension_owner.items()
                if assigned_owner != owner
            )
        )


@dataclass(frozen=True)
class _SemanticLabel:
    record: DxfEntityRecord
    shape_code: str
    rect: Rect


@dataclass(frozen=True)
class _ActionableAnnotation:
    record: DxfEntityRecord
    rect: Rect


@dataclass(frozen=True)
class _NumericText:
    record: DxfEntityRecord
    rect: Rect


def build_local_detail_index(
    annotations: Sequence[DxfEntityRecord],
    entities: Sequence[DxfEntityRecord],
    *,
    policy: RecognitionPolicy,
    include_specification_annotations: bool = False,
    dimension_max_distance_raw: float | None = None,
) -> LocalDetailIndex:
    """Build the local ownership index for semantic detail labels.

    ``include_specification_annotations`` also admits a rebar text that
    declares Ø and length WITHOUT a count (``стр.: N8x166``).  Such a text is
    never a position of its own -- it is the designer's specification for the
    positions listed elsewhere -- so the established matching path keeps the
    default and only the printed-dimension rule opts in.

    ``dimension_max_distance_raw`` overrides the numeric-text search radius.
    The default belongs to the matching path; a caller that reads the printed
    sizes of the DRAWN figure needs a radius that covers the figure itself
    (see ``RecognitionPolicy.printed_detail_dimension_max_distance_raw``).
    Ownership still decides: nearest owner with an explicit margin.
    """

    if not policy.local_detail_context_rule_enabled:
        return LocalDetailIndex(assignments={}, exclusive_dimension_owner={})

    actionables = tuple(
        _ActionableAnnotation(record=record, rect=_entity_rect(record))
        for record in annotations
        if _is_actionable(record)
        or (include_specification_annotations and _is_specification(record))
    )
    labels = tuple(
        _SemanticLabel(record=record, shape_code=shape_code, rect=_entity_rect(record))
        for record in entities
        if record.entity_type.upper() in {"TEXT", "MTEXT"}
        and (shape_code := local_detail_shape_code(record.text or "")) is not None
    )
    if not actionables or not labels:
        return LocalDetailIndex(assignments={}, exclusive_dimension_owner={})

    # A label and annotation are paired one-to-one.  Global greedy ordering is
    # deterministic and prevents one prefix from relabelling two nearby blocks.
    candidate_pairs: list[tuple[float, str, str, _ActionableAnnotation, _SemanticLabel]] = []
    maximum_label_distance = max(0.0, float(policy.local_detail_label_max_distance_raw))
    for annotation in actionables:
        for label in labels:
            score = _label_annotation_score(label.rect, annotation.rect)
            if score > maximum_label_distance:
                continue
            candidate_pairs.append(
                (
                    score,
                    str(annotation.record.handle),
                    str(label.record.handle),
                    annotation,
                    label,
                )
            )
    candidate_pairs.sort(key=lambda item: (item[0], item[1], item[2]))

    assigned_annotations: set[str] = set()
    assigned_labels: set[str] = set()
    base_assignments: dict[str, tuple[_ActionableAnnotation, _SemanticLabel, float]] = {}
    for score, annotation_handle, label_handle, annotation, label in candidate_pairs:
        if annotation_handle in assigned_annotations or label_handle in assigned_labels:
            continue
        # If two different semantic labels are essentially tied for the same
        # annotation, do not choose one by file order.
        competing = [
            pair
            for pair in candidate_pairs
            if pair[1] == annotation_handle
            and pair[4].shape_code != label.shape_code
            and pair[0] - score <= float(policy.local_detail_label_ambiguity_margin_raw)
        ]
        if competing:
            continue
        assigned_annotations.add(annotation_handle)
        assigned_labels.add(label_handle)
        base_assignments[annotation_handle] = (annotation, label, score)

    if not base_assignments:
        return LocalDetailIndex(assignments={}, exclusive_dimension_owner={})

    numeric_rows = tuple(
        _NumericText(record=record, rect=_entity_rect(record))
        for record in entities
        if record.entity_type.upper() in {"TEXT", "MTEXT", "DIMENSION"}
        and _NUMERIC_RE.fullmatch(str(record.text or ""))
    )
    region_by_annotation = {
        handle: _rect_union(annotation.rect, label.rect)
        for handle, (annotation, label, _score) in base_assignments.items()
    }
    nearest_owner: dict[str, str] = {}
    exclusive_owner: dict[str, str] = {}
    maximum_dimension_distance = max(
        0.0,
        float(
            policy.local_detail_dimension_max_distance_raw
            if dimension_max_distance_raw is None
            else dimension_max_distance_raw
        ),
    )
    minimum_margin = max(
        0.0, float(policy.local_detail_dimension_min_margin_raw)
    )
    for numeric in numeric_rows:
        ranked = sorted(
            (
                (_rect_distance(numeric.rect, region), annotation_handle)
                for annotation_handle, region in region_by_annotation.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
        if not ranked or ranked[0][0] > maximum_dimension_distance:
            continue
        best_distance, best_owner = ranked[0]
        nearest_owner[str(numeric.record.handle)] = best_owner
        if len(ranked) == 1 or ranked[1][0] - best_distance >= minimum_margin:
            exclusive_owner[str(numeric.record.handle)] = best_owner

    dimensions_by_owner: dict[str, list[str]] = {handle: [] for handle in base_assignments}
    exclusive_by_owner: dict[str, list[str]] = {handle: [] for handle in base_assignments}
    for handle, owner in nearest_owner.items():
        dimensions_by_owner.setdefault(owner, []).append(handle)
    for handle, owner in exclusive_owner.items():
        exclusive_by_owner.setdefault(owner, []).append(handle)

    assignments: dict[str, LocalDetailAssignment] = {}
    for handle, (_annotation, label, score) in base_assignments.items():
        assignments[handle] = LocalDetailAssignment(
            annotation_handle=handle,
            shape_code=label.shape_code,
            label_handle=str(label.record.handle),
            label_text=str(label.record.text or ""),
            label_distance_raw=float(score),
            dimension_handles=tuple(sorted(dimensions_by_owner.get(handle, ()))),
            exclusive_dimension_handles=tuple(
                sorted(exclusive_by_owner.get(handle, ()))
            ),
        )
    return LocalDetailIndex(
        assignments=assignments,
        exclusive_dimension_owner=exclusive_owner,
    )


def local_detail_shape_code(text: str) -> str | None:
    normalized = _normalize_text(text)
    # Explicit engineering captions for S/C-shaped punching bars. A bare
    # S (for example the spacing text s=20) is deliberately not enough:
    # it would relabel unrelated rows by proximity.
    if re.search(r"\bЕСОВЕ\b", normalized):
        return "F007"
    if (
        re.search(r"(?:^|\s)[SС](?:\s|$)", normalized)
        and re.search(
            r"\b(?:ПРОДЪЛЖАВАН|ПРОДЪНВАН)(?:Е|ЕТО)\b",
            normalized,
        )
    ):
        return "F007"
    if re.search(r"\bФИБ(?:А|И|ИТЕ)?\b", normalized):
        return "F005"
    if (
        re.search(r"\bСТР\s*\.?\s*:?\b", normalized)
        or re.search(r"\bСТРЕМ(?:Е|ЕНА|ЕНАТА)?\b", normalized)
        or re.search(r"\bБИГЛ(?:А|И)\b", normalized)
    ):
        return "F006"
    return None


def _normalize_text(text: str) -> str:
    value = str(text or "").upper()
    value = value.replace("%%U", " ").replace("\\L", " ").replace("\\l", " ")
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"[{}]", " ", value)
    value = re.sub(r"[^0-9A-ZА-ЯЁØФИСТРЕМНБГЛ.:-]+", " ", value)
    return " ".join(value.split())


def _is_actionable(record: DxfEntityRecord) -> bool:
    parsed = parse_rebar_text(record.text or "")
    return (
        parsed.count is not None
        and parsed.diameter_mm is not None
        and parsed.length_cm is not None
    )


def _is_specification(record: DxfEntityRecord) -> bool:
    """Ø и дължина БЕЗ брой: спецификация на детайла, не позиция.

    Реален случай: ``стр.: N8x166`` над таблицата „Щурцове 1 до 9" описва
    стремето, а бройките стоят в редовете на таблицата (``32N8x166`` …).
    """

    parsed = parse_rebar_text(record.text or "")
    return (
        parsed.count is None
        and parsed.diameter_mm is not None
        and parsed.length_cm is not None
    )


def _entity_rect(record: DxfEntityRecord) -> Rect:
    geometry = record.geometry or {}
    raw_bbox = geometry.get("visual_bbox")
    if isinstance(raw_bbox, (list, tuple)) and raw_bbox:
        points = [
            (float(point[0]), float(point[1]))
            for point in raw_bbox
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        if points:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            return min(xs), min(ys), max(xs), max(ys)
    for key in ("visual_anchor", "insert", "text_midpoint", "defpoint"):
        point = geometry.get(key)
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = float(point[0]), float(point[1])
            return x, y, x, y
    return (0.0, 0.0, 0.0, 0.0)


def _rect_union(left: Rect, right: Rect) -> Rect:
    return (
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    )


def _rect_distance(left: Rect, right: Rect) -> float:
    dx = max(left[0] - right[2], right[0] - left[2], 0.0)
    dy = max(left[1] - right[3], right[1] - left[3], 0.0)
    return math.hypot(dx, dy)


def _rect_center(rect: Rect) -> Point2D:
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def _label_annotation_score(label: Rect, annotation: Rect) -> float:
    base = _rect_distance(label, annotation)
    label_center = _rect_center(label)
    annotation_center = _rect_center(annotation)
    vertical_offset = abs(label_center[1] - annotation_center[1])
    # Local prefixes are normally on the same text baseline.  A modest
    # vertical penalty prevents a word in the row above from winning solely
    # because its bbox corner touches the annotation bbox.
    return base + vertical_offset * 0.20

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.utils.files import safe_filename

CANDIDATE_SCHEMA = "lion-rebar-project-shape-candidate/v1"
_MAX_FORM_NUMBER = 999


def suggest_next_shape_code(
    shapes: Iterable[ShapeDefinition | Mapping[str, Any] | str],
    *,
    reserved_codes: Iterable[str] = (),
    preferred_code: str | None = None,
) -> str | None:
    """Return the next collision-free F-code without modifying the Engine.

    The suggestion follows the same numbering rule as Engine Studio: an empty
    custom library starts at F010.  Existing project candidates are supplied as
    ``reserved_codes`` so a batch of new forms does not receive one repeated
    proposal.
    """

    occupied = {
        normalized
        for value in (*tuple(_shape_code(item) for item in shapes), *tuple(reserved_codes))
        if (normalized := _normalize_form_code(value)) is not None
    }
    preferred = _normalize_form_code(preferred_code)
    if preferred is not None and preferred not in occupied:
        return preferred

    numbers = [int(code[1:]) for code in occupied]
    candidate = max(max(numbers, default=9) + 1, 10)
    while candidate <= _MAX_FORM_NUMBER:
        code = f"F{candidate:03d}"
        if code not in occupied:
            return code
        candidate += 1
    return None


def build_project_shape_candidate(
    payload: Mapping[str, Any],
    *,
    existing_shapes: Iterable[ShapeDefinition | Mapping[str, Any] | str] = (),
    reserved_codes: Iterable[str] = (),
    preferred_code: str | None = None,
    project_name: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a reviewable project candidate and, only when safe, one draft form.

    The returned JSON is deliberately non-authoritative.  It contains the exact
    selected DXF evidence and a normalized duplicate-search signature.  A
    ``forms`` list is emitted only for a physically connected, unbranched,
    owner-confirmed line chain whose segment order can be reconstructed from the
    exact points.  Importing that list in Engine Studio still creates a DRAFT;
    this function never approves, publishes or changes an Engine.
    """

    geometry = _mapping(payload.get("geometry"))
    dimensions = _mapping(payload.get("dimensions"))
    annotation = _mapping(payload.get("annotation"))
    shape_name = str(payload.get("shape_name") or "").strip()
    timestamp = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
    suggested_code = suggest_next_shape_code(
        existing_shapes,
        reserved_codes=reserved_codes,
        preferred_code=preferred_code,
    )

    points = _points(geometry.get("points"))
    closed = bool(geometry.get("closed"))
    exact_lengths, exact_turns = _geometry_profile(points, closed=closed)
    declared_segment_count = _positive_int(geometry.get("segment_count"))
    normalized_points = _canonical_normalized_points(points, closed=closed)
    normalized_topology = _normalized_topology(
        geometry,
        exact_lengths=exact_lengths,
        exact_turns=exact_turns,
        normalized_points=normalized_points,
    )

    blockers = _import_blockers(
        geometry,
        annotation=annotation,
        dimensions=dimensions,
        shape_name=shape_name,
        suggested_code=suggested_code,
        points=points,
        exact_lengths=exact_lengths,
        exact_turns=exact_turns,
        declared_segment_count=declared_segment_count,
    )
    import_ready = not blockers
    candidate_id = _candidate_id(
        project_name=project_name,
        annotation=annotation,
        geometry=geometry,
        normalized_topology=normalized_topology,
    )

    production_length = _positive_float(dimensions.get("production_length_cm"))
    production_segments = _positive_float_sequence(
        dimensions.get("production_segment_lengths_cm")
    )
    exact_geometry = {
        "handles": _string_list(geometry.get("handles")),
        "entity_types": _string_list(geometry.get("entity_types")),
        "layers": _string_list(geometry.get("layers")),
        "ordered_points": [[x, y] for x, y in points],
        "segment_lengths_cm": list(exact_lengths),
        "signed_turn_angles_deg": list(exact_turns),
        "measured_total_length_cm": _positive_float(geometry.get("total_length_cm")),
        "closed": closed,
        "connected": bool(geometry.get("connected")),
        "physical_connected": bool(geometry.get("physical_connected", True)),
        "branched": bool(geometry.get("branched")),
        "component_count": _positive_int(geometry.get("component_count")) or 1,
        "inferred_composite": bool(geometry.get("inferred_composite")),
        "composite_kind": geometry.get("composite_kind"),
        "near_gap_healed": bool(geometry.get("near_gap_healed")),
        "near_gap_bridge_gap_raw": geometry.get("near_gap_bridge_gap_raw"),
        "near_gap_bridge_handles": _string_list(
            geometry.get("near_gap_bridge_handles")
        ),
        "contains_curves": bool(geometry.get("contains_curves")),
        "curve_segment_count": _positive_int(
            geometry.get("curve_segment_count")
        )
        or 0,
    }
    evidence = {
        "source": "OWNER_SELECTED_DXF_GEOMETRY",
        "owner_confirmation_required_by_ui": True,
        "training_eligible": bool(geometry.get("training_eligible", True)),
        "save_training_example_requested": bool(payload.get("save_training_example")),
        "reason": str(payload.get("reason") or ""),
        "note": str(payload.get("note") or ""),
        "warnings": _string_list(geometry.get("warnings")),
        "import_blocker_codes": blockers,
    }
    candidate = {
        "candidate_id": candidate_id,
        "status": "PROJECT_CANDIDATE_REQUIRES_ENGINE_APPROVAL",
        "never_publish_automatically": True,
        "project_name": project_name,
        "created_at": timestamp,
        "suggested_shape_code": suggested_code,
        "suggested_shape_name": shape_name,
        "annotation": dict(annotation),
        "production": {
            "length_cm": production_length,
            "length_source": str(dimensions.get("source") or ""),
            "segment_lengths_cm": list(production_segments),
            "declared_annotation_length_cm": _positive_float(
                annotation.get("declared_length_cm")
            ),
        },
        "exact_dxf_geometry": exact_geometry,
        "normalized_topology": normalized_topology,
        "evidence": evidence,
        "engine_studio_import": {
            "ready": import_ready,
            "creates_state": "DRAFT",
            "requires_human_review": True,
            "blocker_codes": blockers,
            "instruction_bg": (
                "Импортирай този JSON в отворена чернова на Engine Studio. "
                "Формата остава DRAFT и трябва да бъде проверена, тествана и "
                "одобрена ръчно."
                if import_ready
                else
                "Кандидатът е запазен за диагностика, но не е безопасен за "
                "директен импорт. Провери геометрията и избери точните обекти."
            ),
        },
    }

    forms = (
        [
            _engine_studio_form(
                code=suggested_code or "",
                name=shape_name,
                candidate_id=candidate_id,
                closed=closed,
                segment_lengths=exact_lengths,
                turn_angles=exact_turns,
                canonical_signature=str(
                    geometry.get("canonical_signature") or ""
                ),
                production_length=production_length,
                annotation=annotation,
            )
        ]
        if import_ready
        else []
    )
    return {
        "schema": CANDIDATE_SCHEMA,
        "candidate": candidate,
        # Engine Studio's existing JSON importer reads this root list.  Keeping
        # it empty for unsafe evidence makes accidental import a no-op.
        "forms": forms,
    }


def candidate_pack_filename(pack: Mapping[str, Any]) -> str:
    candidate = _mapping(pack.get("candidate"))
    annotation = _mapping(candidate.get("annotation"))
    handle = safe_filename(str(annotation.get("handle") or ""), "NO_HANDLE")
    return f"SHAPE_CANDIDATE_{handle}.json"


def write_project_shape_candidate(
    working_dir: Path,
    pack: Mapping[str, Any],
) -> Path:
    """Atomically write one current candidate artifact per annotation."""

    destination_dir = Path(working_dir) / "shape_candidates"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / candidate_pack_filename(pack)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(dict(pack), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def _engine_studio_form(
    *,
    code: str,
    name: str,
    candidate_id: str,
    closed: bool,
    segment_lengths: Sequence[float],
    turn_angles: Sequence[float],
    canonical_signature: str,
    production_length: float | None,
    annotation: Mapping[str, Any],
) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    for index, length in enumerate(segment_lengths):
        turn = turn_angles[index] if index < len(turn_angles) else None
        if turn is None or abs(turn) <= 1e-8:
            angle = None
            direction = None
        else:
            angle = round(abs(turn), 6)
            direction = "LEFT" if turn > 0 else "RIGHT"
        segments.append(
            {
                "lengthCode": f"L{index + 1}",
                # Prototype dimensions stay in raw evidence and the recognition
                # profile.  No fixed production dimension is invented.
                "prototypeLengthCm": round(float(length), 6),
                "parametric": True,
                "angle": angle,
                "turn": direction,
            }
        )

    profile_id = f"PROJECT-{candidate_id}"
    return {
        "proposedCode": code,
        "name": name,
        "category": "PROJECT_CANDIDATE",
        "state": "DRAFT",
        "closed": bool(closed),
        "symmetric": False,
        # Mirroring is a controlled Engine rule.  The candidate does not infer
        # permission merely because duplicate-search normalization uses mirrors.
        "mirrorAllowed": False,
        "initialDirection": "RIGHT",
        "segments": segments,
        "recognition": {
            "canonical_signature_observed": canonical_signature,
            "reference_segment_profiles": [
                {
                    "profile_id": profile_id,
                    "segment_lengths_cm": [
                        round(float(value), 6) for value in segment_lengths
                    ],
                    "source": "OWNER_CONFIRMED_PROJECT_CANDIDATE",
                }
            ],
        },
        "candidateEvidence": {
            "candidate_id": candidate_id,
            "annotation_handle": str(annotation.get("handle") or ""),
            "annotation_text": str(annotation.get("text") or ""),
            "production_length_cm": production_length,
            "never_publish_automatically": True,
        },
        "notes": (
            "Създадено от проект като контролирана DRAFT чернова. "
            "Провери сегментите, правилото за огледалност и тестовете преди одобрение."
        ),
    }


def _import_blockers(
    geometry: Mapping[str, Any],
    *,
    annotation: Mapping[str, Any],
    dimensions: Mapping[str, Any],
    shape_name: str,
    suggested_code: str | None,
    points: Sequence[tuple[float, float]],
    exact_lengths: Sequence[float],
    exact_turns: Sequence[float],
    declared_segment_count: int | None,
) -> list[str]:
    blockers: list[str] = []
    if not str(annotation.get("handle") or ""):
        blockers.append("MISSING_ANNOTATION_HANDLE")
    if not _string_list(geometry.get("handles")):
        blockers.append("MISSING_GEOMETRY_HANDLES")
    if _positive_float(dimensions.get("production_length_cm")) is None:
        blockers.append("MISSING_PRODUCTION_LENGTH")
    if not shape_name:
        blockers.append("MISSING_SHAPE_NAME")
    if suggested_code is None:
        blockers.append("NO_AVAILABLE_F_CODE")
    if not bool(geometry.get("connected")):
        blockers.append("DISCONNECTED_GEOMETRY")
    if not bool(geometry.get("physical_connected", True)):
        blockers.append("PHYSICALLY_DISCONNECTED_GEOMETRY")
    if bool(geometry.get("branched")):
        blockers.append("BRANCHED_GEOMETRY")
    if bool(geometry.get("inferred_composite")):
        blockers.append("COMPOSITE_GEOMETRY_REQUIRES_ENGINEERING")
    if bool(geometry.get("contains_curves")) or (
        _positive_int(geometry.get("curve_segment_count")) or 0
    ):
        blockers.append("CURVED_GEOMETRY_REQUIRES_ENGINEERING")
    if bool(geometry.get("near_gap_healed")):
        blockers.append("HEALED_GAP_IS_NOT_TRAINING_TRUTH")
    if not bool(geometry.get("training_eligible", True)):
        blockers.append("GEOMETRY_NOT_TRAINING_ELIGIBLE")
    if len(points) < 2:
        blockers.append("MISSING_EXACT_ORDERED_POINTS")
    if not exact_lengths:
        blockers.append("NO_MEASURABLE_SEGMENTS")
    if declared_segment_count is None or declared_segment_count != len(exact_lengths):
        blockers.append("SEGMENT_COUNT_MISMATCH")
    expected_turns = len(exact_lengths) if bool(geometry.get("closed")) else max(
        0, len(exact_lengths) - 1
    )
    if len(exact_turns) != expected_turns:
        blockers.append("TURN_COUNT_MISMATCH")
    return blockers


def _normalized_topology(
    geometry: Mapping[str, Any],
    *,
    exact_lengths: Sequence[float],
    exact_turns: Sequence[float],
    normalized_points: Sequence[tuple[float, float]],
) -> dict[str, Any]:
    total = sum(exact_lengths)
    ratios = (
        [round(value / total, 8) for value in exact_lengths]
        if total > 1e-12
        else []
    )
    topology_only_signature = _topology_only_signature(
        segment_count=len(exact_lengths),
        turns=exact_turns,
        closed=bool(geometry.get("closed")),
    )
    coordinate_signature = (
        f"{'C' if geometry.get('closed') else 'O'}|p="
        + ";".join(f"{x:.8f},{y:.8f}" for x, y in normalized_points)
    )
    return {
        "segment_count": len(exact_lengths),
        "bend_count": len(exact_turns),
        "closed": bool(geometry.get("closed")),
        "parameter_invariant_topology_signature": topology_only_signature,
        "transform_invariant_coordinate_signature": coordinate_signature,
        "segment_length_ratios_in_selected_order": ratios,
        "signed_turn_angles_deg_in_selected_order": [
            round(value, 6) for value in exact_turns
        ],
        "turn_magnitudes_deg": [round(abs(value), 6) for value in exact_turns],
        "bend_directions": [
            "LEFT" if value > 0 else "RIGHT" if value < 0 else "NONE"
            for value in exact_turns
        ],
        "canonical_signature": str(geometry.get("canonical_signature") or ""),
        "canonical_points_unit_total": [
            [round(x, 8), round(y, 8)] for x, y in normalized_points
        ],
        "invariance": {
            "translation": True,
            "rotation": True,
            "reversed_direction": True,
            "closed_start_vertex": True,
            "mirror_for_duplicate_search_only": True,
        },
    }


def _topology_only_signature(
    *,
    segment_count: int,
    turns: Sequence[float],
    closed: bool,
) -> str:
    rounded = tuple(round(value, 2) for value in turns)
    base_variants = (
        rounded,
        tuple(-value for value in rounded),
        tuple(reversed(rounded)),
        tuple(-value for value in reversed(rounded)),
    )
    variants: list[tuple[float, ...]] = []
    for variant in base_variants:
        if closed and variant:
            variants.extend(
                variant[shift:] + variant[:shift]
                for shift in range(len(variant))
            )
        else:
            variants.append(variant)
    canonical = min(variants) if variants else ()
    angles = ",".join(f"{value:.2f}" for value in canonical)
    return f"{'C' if closed else 'O'}|n={segment_count}|a={angles}"


def _geometry_profile(
    points: Sequence[tuple[float, float]],
    *,
    closed: bool,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    work = _geometry_vertices(points, closed=closed)
    if len(work) < 2:
        return (), ()
    vectors: list[tuple[float, float]] = []
    lengths: list[float] = []
    for left, right in pairwise(work):
        dx = right[0] - left[0]
        dy = right[1] - left[1]
        length = math.hypot(dx, dy)
        if length <= 1e-10:
            continue
        vectors.append((dx, dy))
        lengths.append(length)
    turns: list[float] = []
    count = len(vectors) if closed else max(0, len(vectors) - 1)
    for index in range(count):
        left = vectors[index]
        right = vectors[(index + 1) % len(vectors)]
        cross = left[0] * right[1] - left[1] * right[0]
        dot = left[0] * right[0] + left[1] * right[1]
        turns.append(math.degrees(math.atan2(cross, dot)))
    return (
        tuple(round(value, 6) for value in lengths),
        tuple(round(value, 6) for value in turns),
    )


def _canonical_normalized_points(
    points: Sequence[tuple[float, float]],
    *,
    closed: bool,
) -> tuple[tuple[float, float], ...]:
    work = list(_geometry_vertices(points, closed=closed))
    if len(work) < 2:
        return ()
    vertices = (
        work[:-1]
        if (
            closed
            and math.hypot(
                work[0][0] - work[-1][0],
                work[0][1] - work[-1][1],
            )
            <= 1e-10
        )
        else work
    )
    if len(vertices) < 2:
        return ()

    sequences: list[list[tuple[float, float]]] = []
    if closed:
        for source in (vertices, list(reversed(vertices))):
            for shift in range(len(source)):
                sequences.append(source[shift:] + source[:shift])
    else:
        sequences.extend((vertices, list(reversed(vertices))))

    variants: list[tuple[tuple[float, float], ...]] = []
    for sequence in sequences:
        for mirror in (False, True):
            transformed = [(x, -y if mirror else y) for x, y in sequence]
            normalized = _normalize_one_sequence(transformed, closed=closed)
            if normalized:
                variants.append(normalized)
    return min(variants, key=_flatten_points) if variants else ()


def _normalize_one_sequence(
    points: Sequence[tuple[float, float]],
    *,
    closed: bool,
) -> tuple[tuple[float, float], ...]:
    work = list(points)
    if closed:
        work.append(work[0])
    total = sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in pairwise(work)
    )
    if total <= 1e-10:
        return ()
    origin = work[0]
    first = next(
        (
            point
            for point in work[1:]
            if math.hypot(point[0] - origin[0], point[1] - origin[1]) > 1e-10
        ),
        None,
    )
    if first is None:
        return ()
    heading = math.atan2(first[1] - origin[1], first[0] - origin[0])
    cosine = math.cos(-heading)
    sine = math.sin(-heading)
    normalized: list[tuple[float, float]] = []
    for x, y in work:
        dx = x - origin[0]
        dy = y - origin[1]
        nx = (dx * cosine - dy * sine) / total
        ny = (dx * sine + dy * cosine) / total
        normalized.append((_zero(round(nx, 8)), _zero(round(ny, 8))))
    return tuple(normalized)


def _geometry_vertices(
    points: Sequence[tuple[float, float]],
    *,
    closed: bool,
) -> tuple[tuple[float, float], ...]:
    clean: list[tuple[float, float]] = []
    for point in points:
        if not clean or math.hypot(point[0] - clean[-1][0], point[1] - clean[-1][1]) > 1e-10:
            clean.append(point)
    if (
        closed
        and clean
        and math.hypot(
            clean[0][0] - clean[-1][0],
            clean[0][1] - clean[-1][1],
        )
        > 1e-10
    ):
        clean.append(clean[0])
    return tuple(clean)


def _candidate_id(
    *,
    project_name: str,
    annotation: Mapping[str, Any],
    geometry: Mapping[str, Any],
    normalized_topology: Mapping[str, Any],
) -> str:
    identity = {
        "project_name": project_name,
        "annotation_handle": str(annotation.get("handle") or ""),
        "geometry_handles": sorted(_string_list(geometry.get("handles"))),
        "normalized_topology": normalized_topology,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:16].upper()


def _shape_code(value: ShapeDefinition | Mapping[str, Any] | str) -> str:
    if isinstance(value, ShapeDefinition):
        return value.code
    if isinstance(value, Mapping):
        return str(value.get("code") or value.get("proposedCode") or "")
    return str(value)


def _normalize_form_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text.startswith("F") or not text[1:].isdigit():
        return None
    number = int(text[1:])
    if number < 1 or number > _MAX_FORM_NUMBER:
        return None
    return f"F{number:03d}"


def _points(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    output: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return ()
        try:
            x, y = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            return ()
        if not math.isfinite(x) or not math.isfinite(y):
            return ()
        output.append((x, y))
    return tuple(output)


def _positive_float_sequence(value: Any) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    output: list[float] = []
    for item in value:
        number = _positive_float(item)
        if number is None:
            return ()
        output.append(number)
    return tuple(output)


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item)]


def _flatten_points(points: Sequence[tuple[float, float]]) -> tuple[float, ...]:
    return tuple(coordinate for point in points for coordinate in point)


def _zero(value: float) -> float:
    return 0.0 if abs(value) < 5e-9 else value

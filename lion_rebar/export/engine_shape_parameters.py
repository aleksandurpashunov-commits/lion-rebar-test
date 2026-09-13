"""Production-dimension validation against the pinned Engine shape library.

Legacy F001-F010 forms keep the locked OPEN_ENGINE v0.3.1 formulas.  Active
forms added after F010 are validated from their versioned Engine definition.
The module deliberately preserves :class:`F006AsymmetricHookError` as a typed
signal across the export/recognition boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

from lion_rebar.engine.bend_allowance import segments_match_total
from lion_rebar.engine.models import ParameterRelation, ShapeDefinition
from lion_rebar.export.errors import ExcelExportError, F006AsymmetricHookError
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    MasterShapeParameters,
    parameters_from_segments,
)

LEGACY_MASTER_CODES = {f"F{index:03d}" for index in range(1, 11)}
DEFAULT_LENGTH_TOLERANCE_CM = 0.51
F006_HOOK_TOLERANCE_CM = 0.11


class EngineShapeParameterError(ExcelExportError):
    """The supplied production dimensions violate the pinned Engine shape."""


@dataclass(frozen=True)
class EngineShapeParameters:
    shape_code: str
    values: dict[str, float]
    expanded_segments_cm: tuple[float, ...]
    total_length_cm: float
    validation_source: str


def active_production_shapes(shapes: Iterable[ShapeDefinition]) -> tuple[ShapeDefinition, ...]:
    """Return every active, non-retired shape from the pinned Engine."""

    return tuple(
        sorted(
            (
                shape
                for shape in shapes
                if bool(shape.active) and str(shape.state or "").strip().upper() != "RETIRED"
            ),
            key=lambda shape: (_shape_sort_key(shape.code), str(shape.name or "")),
        )
    )


def shape_by_code(
    shapes: Iterable[ShapeDefinition],
    shape_code: str,
    *,
    require_active: bool = True,
) -> ShapeDefinition:
    code = str(shape_code or "").strip().upper()
    for shape in shapes:
        if str(shape.code or "").strip().upper() != code:
            continue
        if require_active and (
            not bool(shape.active) or str(shape.state or "").strip().upper() == "RETIRED"
        ):
            raise EngineShapeParameterError(f"{code}: формата не е активна в заключения Engine.")
        return shape
    raise EngineShapeParameterError(f"{code or '—'}: формата липсва в заключения Engine.")


def parameters_for_shape(
    shape: ShapeDefinition,
    segment_lengths_cm: Sequence[float],
    *,
    production_length_cm: float | None = None,
    turn_angles_deg: Sequence[float] = (),
) -> EngineShapeParameters:
    """Validate and canonicalize production dimensions for one Engine shape.

    ``F006AsymmetricHookError`` is intentionally raised unchanged.  Do not
    wrap it in ``EngineShapeParameterError`` or ``ExcelExportError``: its type
    is required by the F006 recognition/production split gate.
    """

    code = str(shape.code or "").strip().upper()
    lengths = _positive_lengths(segment_lengths_cm, code=code)
    total = _positive_total(production_length_cm, lengths=lengths, code=code)

    # Preserve the structured signal even when this new layer is called before
    # the legacy MASTER helper.  This is the exact regression that recovery
    # 6.3.1 must prevent.
    if code == "F006" and len(lengths) >= 6:
        hook_start = lengths[0]
        hook_end = lengths[5]
        if abs(hook_start - hook_end) > F006_HOOK_TOLERANCE_CM:
            raise F006AsymmetricHookError(hook_start, hook_end)

    if code in LEGACY_MASTER_CODES:
        try:
            legacy: MasterShapeParameters = parameters_from_segments(
                code,
                lengths,
                production_length_cm=total,
                turn_angles_deg=turn_angles_deg,
            )
        except F006AsymmetricHookError:
            raise
        except MasterDimensionError as exc:
            raise EngineShapeParameterError(str(exc)) from exc
        return EngineShapeParameters(
            shape_code=legacy.shape_code,
            values=dict(legacy.values),
            expanded_segments_cm=tuple(legacy.expanded_segments_cm),
            total_length_cm=float(legacy.total_length_cm),
            validation_source="LOCKED_MASTER_V0_3_1",
        )

    return _parameters_from_engine_definition(shape, lengths, total)


def expected_segment_codes(shape: ShapeDefinition) -> tuple[str, ...]:
    return tuple(
        str(segment.length_code or f"L{segment.index}").strip().upper()
        for segment in sorted(shape.segments, key=lambda item: int(item.index))
    )


def _parameters_from_engine_definition(
    shape: ShapeDefinition,
    lengths: tuple[float, ...],
    total: float,
) -> EngineShapeParameters:
    code = str(shape.code or "").strip().upper()
    segments = tuple(sorted(shape.segments, key=lambda item: int(item.index)))
    required_count = len([segment for segment in segments if bool(segment.required)])
    if not segments:
        raise EngineShapeParameterError(f"{code}: Engine формата няма сегментна дефиниция.")
    if len(lengths) != len(segments):
        raise EngineShapeParameterError(
            f"{code} очаква {len(segments)} сегмента, получени са {len(lengths)}."
        )
    if len(lengths) < required_count:
        raise EngineShapeParameterError(
            f"{code}: липсват задължителни производствени сегменти."
        )

    values: dict[str, float] = {}
    first_by_code: dict[str, float] = {}
    for definition, value in zip(segments, lengths):
        key = str(definition.length_code or f"L{definition.index}").strip().upper()
        if definition.fixed_length_cm is not None:
            tolerance = _segment_tolerance(definition.raw)
            if abs(value - float(definition.fixed_length_cm)) > tolerance:
                raise EngineShapeParameterError(
                    f"{code} сегмент {definition.index} трябва да е "
                    f"{float(definition.fixed_length_cm):g} cm, получен е {value:g} cm."
                )
        if key in first_by_code:
            tolerance = _segment_tolerance(definition.raw)
            if abs(first_by_code[key] - value) > tolerance:
                raise EngineShapeParameterError(
                    f"{code}: повтореният параметър {key} трябва да е еднакъв "
                    f"({first_by_code[key]:g}/{value:g} cm)."
                )
        else:
            first_by_code[key] = value
            values[key] = value

    for relation in shape.parameter_relations:
        if not bool(relation.required):
            continue
        _validate_relation(code, relation, lengths)

    calculated = sum(lengths)
    tolerance = _shape_total_tolerance(shape)
    if not segments_match_total(code, calculated, total, tolerance=tolerance):
        raise EngineShapeParameterError(
            f"{code}: Engine сегментите дават {calculated:g} cm, "
            f"а производствената L е {total:g} cm."
        )

    return EngineShapeParameters(
        shape_code=code,
        values=values,
        expanded_segments_cm=lengths,
        total_length_cm=calculated,
        validation_source="PINNED_ENGINE_SHAPE_DEFINITION",
    )


def _validate_relation(
    code: str,
    relation: ParameterRelation,
    lengths: tuple[float, ...],
) -> None:
    left_index = int(relation.left_segment) - 1
    right_index = int(relation.right_segment) - 1
    if left_index < 0 or right_index < 0 or left_index >= len(lengths) or right_index >= len(lengths):
        raise EngineShapeParameterError(
            f"{code}: невалидна Engine relation за сегменти "
            f"{relation.left_segment}/{relation.right_segment}."
        )
    left = lengths[left_index]
    right = lengths[right_index]
    tolerance = (
        float(relation.tolerance)
        if relation.tolerance is not None
        else DEFAULT_LENGTH_TOLERANCE_CM
    )
    kind = str(relation.kind or "EQUAL").strip().upper()
    if kind == "EQUAL":
        expected = right
    elif kind == "RATIO":
        ratio = float(relation.ratio)
        if not math.isfinite(ratio) or ratio <= 0:
            raise EngineShapeParameterError(f"{code}: невалидно отношение ratio={ratio!r}.")
        expected = ratio * right
    else:
        raise EngineShapeParameterError(f"{code}: неподдържана relation {kind!r}.")
    if abs(left - expected) > tolerance:
        raise EngineShapeParameterError(
            f"{code}: relation {kind} между сегменти {relation.left_segment}/"
            f"{relation.right_segment} не е изпълнена ({left:g}/{right:g} cm)."
        )


def _positive_lengths(values: Sequence[float], *, code: str) -> tuple[float, ...]:
    result: list[float] = []
    for raw in values:
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise EngineShapeParameterError(f"{code}: невалиден сегмент {raw!r}.") from exc
        if not math.isfinite(value) or value <= 0:
            raise EngineShapeParameterError(
                f"{code}: сегментните размери трябва да са положителни крайни числа."
            )
        result.append(value)
    return tuple(result)


def _positive_total(
    value: float | None,
    *,
    lengths: tuple[float, ...],
    code: str,
) -> float:
    try:
        total = float(value) if value is not None else float(sum(lengths))
    except (TypeError, ValueError) as exc:
        raise EngineShapeParameterError(f"{code}: невалидна производствена дължина.") from exc
    if not math.isfinite(total) or total <= 0:
        raise EngineShapeParameterError(f"{code}: липсва валидна производствена дължина.")
    return total


def _segment_tolerance(raw: object) -> float:
    if isinstance(raw, dict):
        for key in ("dimension_tolerance_cm", "length_tolerance_cm", "tolerance_cm"):
            try:
                value = float(raw.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                return value
    return DEFAULT_LENGTH_TOLERANCE_CM


def _shape_total_tolerance(shape: ShapeDefinition) -> float:
    for source in (shape.recognition, shape.rendering, shape.legacy_raw):
        if not isinstance(source, dict):
            continue
        for key in ("production_total_tolerance_cm", "total_length_tolerance_cm"):
            try:
                value = float(source.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                return value
    return DEFAULT_LENGTH_TOLERANCE_CM


def _shape_sort_key(code: str) -> tuple[int, int | str]:
    value = str(code or "").strip().upper()
    if len(value) > 1 and value[0] == "F" and value[1:].isdigit():
        return (0, int(value[1:]))
    return (1, value)

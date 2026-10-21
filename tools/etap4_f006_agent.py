"""Класификация на MANUAL позициите по НАЛИЧНО РАЗМЕРНО ДОКАЗАТЕЛСТВО.

Въпросът не е „коя форма е“, а „има ли надписан размер за всеки участък,
който формата изисква, и еднозначно ли е свързан с тази позиция“.

Само за четене: работи върху записаните пакетни отчети
(``*_geometry_batch.json``) и върху ``working/extracted_entities.json`` на
копията на проектите. Нищо в ``lion_rebar/`` не се пипа и нищо в проектите
не се записва. Единственият изход е ``F006_AGENT.json``.

Класове
-------
FULLY_DIMENSIONED
    Всеки участък, който формата изисква, има надписан размер, който е
    еднозначно свързан с ТАЗИ позиция. „Еднозначно“ е доказано по един от
    два независими начина:
    * ``OWNED_CONTOUR`` — двете опорни точки на котата лежат върху
      върховете на собствения контур на кандидата и нито един друг контур
      не е на същото разстояние (``strict_unique_dimension_records_by_contours``);
      това е връзка, начертана от проектанта.
    * ``GEOMETRY_CONFIRMED_PRINTED`` — прочетените по близост коти дават
      СЪЩИЯ производствен вектор, който е независимо измерен от самата
      DXF геометрия на кандидата. Две независими съгласни свидетелства.
DIMENSIONS_EXIST_UNLINKED
    Котите с нужните стойности съществуват в чертежа (доказани с handle и
    измерена стойност), но не са свързани с позицията:
    * ``OWNED_BY_NEIGHBOUR_CONTOUR`` — начертани са върху друг контур
      (близнака на същото стреме), не върху кандидата;
    * ``SPLIT_ACROSS_TWIN_CONTOURS`` — част от размерите са върху
      собствения контур, останалите върху близнака;
    * ``SIBLING_IN_REVIEW_GROUP`` — котиран е един екземпляр от групата,
      копията са без кота;
    * без модел — липсва начертана връзка, стойността се носи от няколко
      коти, или уравнението не се затваря по прочетеното.
INCOMPLETE
    Поне един нужен участък няма надписана стойност в обхвата. Два случая:
    * ``NO_EVIDENCE`` — няма го нито надписано, нито измерено; числото, което
      програмата показва, е ДОСМЕТНАТО от уравнението. Тук наистина трябва
      проектно правило.
    * ``PRINTED_MISSING_BUT_DRAWN`` — участъкът е начертан и измерен от
      геометрията с HIGH confidence, липсва само печатна кота. Тук не трябва
      норма, а решение на собственика приема ли се измерен участък.
CONTRADICTORY
    Наличните числа не се съгласуват: надписаното чете различна форма от
    измереното по геометрия, или две различни разчитания затварят
    уравнението еднакво близо.
UNKNOWN
    Не може да се определи какви участъци изисква формата (няма MASTER
    форма и няма производствен вектор).

Разчетено от чертежа ≠ проверено срещу норма. Този инструмент мери само
първото: съществува ли надписаното число и на кого принадлежи. Добавката
за огъване се ползва само като ПРОВЕРКА на уравнението (правилото на
самата програма, ``segments_match_total``), никога за да се досмята
липсващ участък.

Пускане (от ``release/``):
    LION_REBAR_DATA_ROOT=C:\\lr_exp\\data PYTHONIOENCODING=utf-8 \\
        python tools/etap4_f006_agent.py
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402
from lion_rebar.dxf.vertical_shape_resolver import (  # noqa: E402
    DIMENSION_RADIUS,
    collect_dimensions,
    derive_shape,
    dimensions_owned_by_contours,
    infer_preferred_gap,
    infer_preferred_hook,
    read_owned_stirrup,
    snap,
    strict_unique_dimension_records_by_contours,
)
from lion_rebar.engine.bend_allowance import segments_match_total  # noqa: E402

DEFAULT_REPORT_GLOB = (
    r"C:\lr_exp\data\full_path_runs\*\output\04_REVIEW_REPORTS\*"
    r"\*_geometry_batch.json"
)
DEFAULT_PROJECT_ROOT = Path(r"C:\lr_exp\proekti")
DEFAULT_OUTPUT = Path(r"C:\lr_exp\rezultat\etap_4\F006_AGENT.json")

F006_STATUS = "СИЛНО СТРЕМЕ F006"

#: Колко може да се разминат две прочетени стойности, за да са „същата“.
VALUE_TOLERANCE_CM = 0.35


# --------------------------------------------------------------------------
# Зареждане
# --------------------------------------------------------------------------
def newest_reports(pattern: str) -> dict[str, Path]:
    """Най-новият пакетен отчет за всеки проект."""

    newest: dict[str, tuple[float, Path]] = {}
    for raw in glob.glob(pattern):
        path = Path(raw)
        project = path.name[: -len("_geometry_batch.json")]
        stamp = path.stat().st_mtime
        if project not in newest or stamp > newest[project][0]:
            newest[project] = (stamp, path)
    return {project: path for project, (_stamp, path) in sorted(newest.items())}


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_entities(path: Path) -> tuple[list[DxfEntityRecord], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [
        DxfEntityRecord(
            handle=str(item.get("handle") or ""),
            entity_type=str(item.get("entity_type") or ""),
            layer=str(item.get("layer") or ""),
            geometry=item.get("geometry") or {},
            text=item.get("text"),
            block_name=item.get("block_name"),
            color_index=item.get("color_index"),
            linetype=item.get("linetype"),
            raw=item.get("raw") or {},
        )
        for item in payload.get("entities") or []
    ]
    return records, payload.get("data_identity") or {}


# --------------------------------------------------------------------------
# Размерно доказателство
# --------------------------------------------------------------------------
def dimension_records(entities: Sequence[DxfEntityRecord]) -> list[dict[str, Any]]:
    """Котите, както програмата ги вижда, но с запазен handle."""

    records: list[dict[str, Any]] = []
    for entity in entities:
        if entity.entity_type != "DIMENSION":
            continue
        measurement = entity.geometry.get("measurement")
        if not measurement:
            continue
        point = (
            entity.geometry.get("text_midpoint")
            or entity.geometry.get("defpoint")
            or (0.0, 0.0)
        )
        records.append(
            {
                "handle": str(entity.handle),
                "layer": entity.layer,
                "measurement": float(measurement),
                "snapped_cm": snap(float(measurement)),
                "display_text": entity.geometry.get("display_text"),
                "point": (float(point[0]), float(point[1])),
            }
        )
    return records


def numeric_texts(entities: Sequence[DxfEntityRecord]) -> list[dict[str, Any]]:
    """TEXT/MTEXT с чисто число — възможен размер в детайл или легенда."""

    out: list[dict[str, Any]] = []
    for entity in entities:
        if entity.entity_type not in ("TEXT", "MTEXT", "BLOCK_ATTR_TEXT"):
            continue
        raw = str(entity.text or "").strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        if not (4.0 <= value <= 2000.0):
            continue
        anchor = (
            entity.geometry.get("insert")
            or entity.geometry.get("visual_anchor")
            or entity.geometry.get("text_midpoint")
        )
        if not anchor:
            continue
        out.append(
            {
                "handle": str(entity.handle),
                "layer": entity.layer,
                "value_cm": snap(value),
                "text": raw,
                "point": (float(anchor[0]), float(anchor[1])),
            }
        )
    return out


def contour_vertices(entities: Sequence[DxfEntityRecord]) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for entity in entities:
        points = entity.geometry.get("points") or []
        vertices = [
            (float(p[0]), float(p[1])) for p in points if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        if vertices:
            out[str(entity.handle)] = vertices
    return out


def needed_parameters(row: Mapping[str, Any]) -> tuple[str, tuple[float, ...]] | None:
    """Кои РАЗЛИЧНИ размера изисква формата на тази позиция.

    За F006 това са кука, ширина и височина — три числа описват шест
    участъка. За F001 (прав прът) няма отделни участъци: единственият
    производствен размер е общата дължина от надписа — точно това връща
    ``excel_export._segments_for_link`` за F001. За останалите форми се
    ползва самият производствен вектор. Не се измисля нищо: ако няма
    вектор, връща None.
    """

    code = str(row.get("master_shape_code") or "").strip().upper()
    if code == "F001":
        declared = row.get("declared_length_cm")
        if isinstance(declared, (int, float)) and float(declared) > 0:
            return code, (float(declared),)
        return None
    vector = [
        float(value)
        for value in (
            row.get("production_segments_cm")
            or row.get("production_segment_lengths_cm")
            or row.get("canonical_segment_lengths_cm")
            or ()
        )
        if isinstance(value, (int, float)) and float(value) > 0
    ]
    if code == "F006" and len(vector) >= 6:
        hook = min(vector[0], vector[-1])
        sides = sorted({round(v, 2) for v in vector[1:-1]})
        if len(sides) == 2:
            return code, (hook, max(sides), min(sides))
        if len(sides) == 1:
            return code, (hook, sides[0], sides[0])
        return code, tuple(sorted({round(v, 2) for v in vector}, reverse=True))
    if vector:
        return code, tuple(sorted({round(v, 2) for v in vector}, reverse=True))
    return None


def same_value(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= VALUE_TOLERANCE_CM


def find_printed(
    value: float,
    *,
    point: tuple[float, float] | None,
    dimensions: Sequence[Mapping[str, Any]],
    texts: Sequence[Mapping[str, Any]],
    radius: float,
) -> list[dict[str, Any]]:
    """Всички надписани носители на една стойност, подредени по близост."""

    hits: list[dict[str, Any]] = []
    for record in dimensions:
        if not same_value(record["snapped_cm"], value):
            continue
        distance = (
            math.hypot(record["point"][0] - point[0], record["point"][1] - point[1])
            if point
            else math.inf
        )
        hits.append(
            {
                "handle": record["handle"],
                "kind": "DIMENSION",
                "layer": record["layer"],
                "value_cm": record["snapped_cm"],
                "measurement_cm": round(record["measurement"], 3),
                "distance_raw": None if math.isinf(distance) else round(distance, 1),
                "within_radius": distance <= radius,
            }
        )
    for record in texts:
        if not same_value(record["value_cm"], value):
            continue
        distance = (
            math.hypot(record["point"][0] - point[0], record["point"][1] - point[1])
            if point
            else math.inf
        )
        hits.append(
            {
                "handle": record["handle"],
                "kind": "TEXT",
                "layer": record["layer"],
                "value_cm": record["value_cm"],
                "measurement_cm": record["value_cm"],
                "distance_raw": None if math.isinf(distance) else round(distance, 1),
                "within_radius": distance <= radius,
            }
        )
    hits.sort(key=lambda item: (item["distance_raw"] is None, item["distance_raw"] or 0.0))
    return hits


def closing_triples(
    values: Sequence[float],
    distance_of: Mapping[float, float],
    declared: float,
) -> list[tuple[float, float, float, float]]:
    """Всички (кука, ширина, височина) от НАДПИСАНИТЕ коти, които затварят L.

    Служи само за откриване на двусмислие: ако две различни тройки затварят
    уравнението на почти същото разстояние, разчитането не е еднозначно.
    """

    out: list[tuple[float, float, float, float]] = []
    pool = [float(v) for v in values]
    for hook in pool:
        for index, width in enumerate(pool):
            for height in pool[index:]:
                if width < hook or height < hook:
                    continue
                total = 2.0 * (width + height) + 2.0 * hook
                if not segments_match_total("F006", total, declared):
                    continue
                span = (
                    distance_of.get(hook, DIMENSION_RADIUS)
                    + distance_of.get(width, DIMENSION_RADIUS)
                    + distance_of.get(height, DIMENSION_RADIUS)
                )
                out.append((span, hook, max(width, height), min(width, height)))
    out.sort()
    deduped: list[tuple[float, float, float, float]] = []
    seen: set[tuple[float, float, float]] = set()
    for span, hook, width, height in out:
        key = (round(hook, 2), round(width, 2), round(height, 2))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((span, hook, width, height))
    return deduped


# --------------------------------------------------------------------------
# Класификация
# --------------------------------------------------------------------------
#: Причини за MANUAL, които НЕ са размерни: позицията чака решение кой
#: обект е нейният, не кой размер липсва.
NON_DIMENSIONAL_BLOCKERS = frozenset(
    {
        "FAR_ASSOCIATION",
        "WEAK_CANDIDATE_SEPARATION",
        "GEOMETRY_OWNERSHIP_CONFLICT",
        "HOOKED_CLOSURE",
        "LENGTH_MISMATCH",
    }
)


def classify_row(
    row: Mapping[str, Any],
    *,
    project: str,
    dimensions: Sequence[Mapping[str, Any]],
    program_dimensions: Sequence[Mapping[str, Any]],
    texts: Sequence[Mapping[str, Any]],
    strict_owned: Mapping[str, Sequence[Mapping[str, Any]]],
    loose_owned: Mapping[str, Sequence[float]],
    positions: Mapping[str, Any],
    preferred_hook: float | None,
    preferred_gap: float | None,
    group_owned: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    dimension_owner: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    handle = str(row.get("annotation_handle") or "")
    code = str(row.get("master_shape_code") or "").strip().upper()
    declared = row.get("declared_length_cm")
    declared = float(declared) if isinstance(declared, (int, float)) else None
    candidates = [str(v) for v in (row.get("candidate_handles") or []) if str(v)]
    point = positions.get(handle)
    if point is not None:
        point = (float(point[0]), float(point[1]))

    verdict: dict[str, Any] = {
        "project": project,
        "annotation_handle": handle,
        "annotation_text": row.get("annotation_text"),
        "group": "F006_HOOKED"
        if row.get("batch_status") == F006_STATUS and row.get("hooked_closure")
        else "OTHER_MANUAL",
        "batch_status": row.get("batch_status"),
        "master_shape_code": code or None,
        "declared_length_cm": declared,
        "candidate_handles": candidates,
        "frame_title": row.get("frame_title"),
        "review_group_key": row.get("review_group_key"),
        "review_group_size": row.get("review_group_size"),
        "blocker_codes": list(row.get("matcher_blocker_codes") or []),
        "f006_production_status": row.get("f006_production_status"),
        "f006_production_reason": row.get("f006_production_reason"),
        "dimension_evidence_applied": bool(row.get("dimension_evidence_applied")),
        "f006_printed_dimensions_resolved": bool(
            row.get("f006_printed_dimensions_resolved")
        ),
        "local_f006_printed_dimension_handles": list(
            row.get("local_f006_printed_dimension_handles") or []
        ),
        "nearby_dimensions_cm": list(row.get("nearby_dimensions_cm") or []),
        "production_segments_cm": list(row.get("production_segments_cm") or []),
        "production_segment_source": (
            (row.get("production_segment_evidence") or {}).get("source")
        ),
        "production_segment_confidence": (
            (row.get("production_segment_evidence") or {}).get("confidence")
        ),
        "program_dimension_evidence": row.get("dimension_derived_evidence"),
        "program_suggested_segments_cm": list(row.get("suggested_segments_cm") or []),
    }

    needed = needed_parameters(row)
    if needed is None:
        verdict.update(
            {
                "class": "UNKNOWN",
                "segments_needed": None,
                "segments_with_dimension": None,
                "printed_dimensions": [],
                "dimension_handles": [],
                "why": (
                    "Няма производствен вектор и няма MASTER форма — не може да се "
                    "каже кои участъци изисква формата, значи не може да се каже и "
                    "какво липсва."
                ),
                "automatable": False,
                "rule_if_automatable": None,
            }
        )
        return verdict

    _code, params = needed
    verdict["segments_needed"] = len(params)
    verdict["required_parameters_cm"] = [round(v, 2) for v in params]

    # 1) Начертана връзка: собствени коти на контура.
    owned_hits: list[dict[str, Any]] = []
    owned_values: list[float] = []
    for candidate in candidates:
        for record in strict_owned.get(candidate, ()):  # строгият, еднозначен
            owned_hits.append(
                {
                    "handle": str(record.get("handle") or ""),
                    "value_cm": float(record.get("measurement_cm") or 0.0),
                    "owner_geometry_handle": candidate,
                    "ownership_score_raw": record.get("ownership_score_raw"),
                }
            )
            owned_values.append(float(record.get("measurement_cm") or 0.0))
    verdict["owned_contour_dimensions"] = [
        {
            "handle": hit["handle"],
            "value_cm": hit["value_cm"],
            "owner_geometry_handle": hit["owner_geometry_handle"],
            "endpoint_score_raw": hit["ownership_score_raw"],
        }
        for hit in sorted(owned_hits, key=lambda item: item["value_cm"])
    ]

    # 2) Близост: точно както програмата чете котите.
    nearby = collect_dimensions(point, program_dimensions) if point else None
    nearby_values = list(nearby.values) if nearby else []
    distance_of = dict(nearby.distance) if nearby else {}

    evidence = None
    if point and declared:
        evidence = derive_shape(
            point,
            program_dimensions,
            declared,
            expected_shape=code or None,
            preferred_hook=preferred_hook,
            preferred_gap=preferred_gap,
        )

    # 3) Покритие на всеки нужен участък с надписано число.
    coverage: list[dict[str, Any]] = []
    for value in params:
        by_owned = [hit for hit in owned_hits if same_value(hit["value_cm"], value)]
        hits = find_printed(
            value,
            point=point,
            dimensions=dimensions,
            texts=texts,
            radius=DIMENSION_RADIUS,
        )
        inside = [hit for hit in hits if hit["within_radius"]]
        for hit in inside:
            hit["owner_contour"] = (dimension_owner or {}).get(hit["handle"])
        coverage.append(
            {
                "value_cm": round(value, 2),
                "owned": [hit["handle"] for hit in by_owned],
                "within_radius": inside[:4],
                "foreign_owners": sorted(
                    {
                        str(hit["owner_contour"])
                        for hit in inside
                        if hit.get("owner_contour")
                        and str(hit["owner_contour"]) not in candidates
                    }
                ),
                "anywhere": len(hits),
            }
        )
    with_dimension = sum(1 for item in coverage if item["owned"] or item["within_radius"])
    verdict["segments_with_dimension"] = with_dimension
    verdict["printed_dimensions"] = [
        {
            "value_cm": item["value_cm"],
            "owned_dimension_handles": item["owned"],
            "nearest_handles": [hit["handle"] for hit in item["within_radius"]],
            "nearest_distance_raw": (
                item["within_radius"][0]["distance_raw"] if item["within_radius"] else None
            ),
            "carriers_within_radius": len(item["within_radius"]),
            "carriers_anywhere": item["anywhere"],
        }
        for item in coverage
    ]
    verdict["dimension_handles"] = sorted(
        {
            handle
            for item in coverage
            for handle in (item["owned"] + [hit["handle"] for hit in item["within_radius"]])
        }
    )

    equation_closes = None
    if declared:
        vector = [
            float(v) for v in (row.get("production_segments_cm") or row.get("canonical_segment_lengths_cm") or ())
        ]
        if vector:
            equation_closes = bool(
                segments_match_total(code or "F006", sum(vector), declared)
            )
    verdict["equation_closes_on_geometry_vector"] = equation_closes

    printed_reading = None
    if evidence is not None and code == "F006" and len(evidence.segments) == 3:
        width, height, hook = (float(v) for v in evidence.segments)
        printed_reading = (hook, width, height)
        verdict["printed_reading_cm"] = {
            "hook": hook,
            "width": width,
            "height": height,
            "bend_allowance_cm": evidence.allowance,
            "confidence": evidence.confidence,
            "measured_count": evidence.measured_count,
            "computed_count": evidence.computed_count,
            "source": evidence.source,
        }
    elif evidence is not None:
        verdict["printed_reading_cm"] = {
            "segments": [float(v) for v in evidence.segments],
            "confidence": evidence.confidence,
            "measured_count": evidence.measured_count,
            "computed_count": evidence.computed_count,
            "source": evidence.source,
        }

    alternatives: list[tuple[float, float, float, float]] = []
    if code == "F006" and declared and nearby_values:
        alternatives = closing_triples(nearby_values, distance_of, declared)
        verdict["closing_readings"] = [
            {
                "hook": hook,
                "width": width,
                "height": height,
                "distance_span_raw": round(span, 1),
            }
            for span, hook, width, height in alternatives[:5]
        ]

    geometry_params = tuple(params)
    printed_params: tuple[float, ...] | None = None
    if evidence is not None:
        if code == "F006" and len(evidence.segments) == 3:
            width, height, hook = (float(v) for v in evidence.segments)
            printed_params = (hook, max(width, height), min(width, height))
        else:
            printed_params = tuple(
                sorted({round(float(v), 2) for v in evidence.segments}, reverse=True)
            )

    def params_agree() -> bool:
        if printed_params is None or len(printed_params) != len(geometry_params):
            return False
        return all(
            same_value(left, right)
            for left, right in zip(sorted(geometry_params), sorted(printed_params))
        )

    # ------------------------------------------------------------------
    # Решението. Редът е нарочен: първо липсва ли число, после спорят ли
    # двата независими източника, после НАЧЕРТАНАТА връзка (тя отменя
    # двусмислието по близост), и само накрая — двусмислие без нищо, което
    # да го разреши.
    # ------------------------------------------------------------------
    missing = [item["value_cm"] for item in coverage if not (item["owned"] or item["within_radius"])]
    all_covered = not missing
    owned_covers_all = all(item["owned"] for item in coverage) and bool(owned_hits)
    owned_extra = sorted(
        {
            round(value, 2)
            for value in owned_values
            if not any(same_value(value, param) for param in params)
        }
    )
    verdict["owned_extra_values_cm"] = owned_extra
    verdict["manual_reason_is_dimensional"] = bool(
        set(verdict["blocker_codes"]) - NON_DIMENSIONAL_BLOCKERS
    ) or bool(row.get("production_export_blocked"))

    # Правият прът няма отделни участъци: единственото производствено число
    # е общата дължина и тя е НАПИСАНА в самия надпис на позицията.
    if code == "F001":
        verdict.update(
            {
                "class": "FULLY_DIMENSIONED",
                "pattern": "LABEL_TOTAL_ONLY",
                "segments_with_dimension": 1,
                "why": (
                    "Прав прът: няма отделни участъци, за които да има какво да "
                    "липсва. Единственото производствено число е общата дължина "
                    f"L={declared:g} cm и тя е надписана в самия надпис на "
                    f"позицията ({row.get('annotation_text')!r}); точно това "
                    "връща и excel_export._segments_for_link за F001. Позицията "
                    "е MANUAL по причини за ПРИНАДЛЕЖНОСТ "
                    f"({', '.join(verdict['blocker_codes']) or 'без код'}), "
                    "не заради липсващ размер."
                ),
                "automatable": True,
                "rule_if_automatable": (
                    "За F001 не се чете размерна информация от чертежа — общата "
                    "дължина от надписа е единственият производствен размер. "
                    "Оставащият въпрос е кой DXF обект принадлежи на надписа; "
                    "той се решава по принадлежност, не по размер."
                ),
            }
        )
        return verdict

    if owned_covers_all and equation_closes is not False:
        note = (
            "Всеки нужен размер има кота, чиито ДВЕ опорни точки лежат върху "
            "върховете на собствения контур на кандидата и нито един друг контур "
            "не е на същото разстояние (strict_unique_dimension_records_by_contours). "
            "Това е връзка, начертана от проектанта, а не близост."
        )
        if printed_params is not None and not params_agree():
            note += (
                " Прочитането по БЛИЗОСТ дава друго ("
                + "/".join(f"{value:g}" for value in printed_params)
                + " cm) — то взима коти на съседни фигури и затова не се ползва "
                "там, където има начертана връзка."
            )
        if owned_extra:
            note += (
                " Контурът носи и допълнителни коти ("
                + ", ".join(f"{value:g} cm" for value in owned_extra)
                + "), затова четенето минава през read_owned_stirrup, не през "
                "произволен избор."
            )
        verdict.update(
            {
                "class": "FULLY_DIMENSIONED",
                "pattern": "OWNED_CONTOUR",
                "why": note,
                "automatable": True,
                "rule_if_automatable": (
                    "Чети размерите от котите, притежавани от контура "
                    "(strict_unique_dimension_records_by_contours), и приеми "
                    "вектора само ако segments_match_total затвори заявената L. "
                    "Нищо не се досмята; при липсваща собствена кота — отказ."
                ),
            }
        )
        return verdict

    if not all_covered:
        # Преди отказ: същият размер може да е начертан върху ДРУГ член на
        # същата група за преглед (един детайл, много копия).
        sibling_hits: list[dict[str, Any]] = []
        group_key = str(row.get("review_group_key") or "")
        for record in (group_owned or {}).get(group_key, ()):  # братята в групата
            if str(record.get("annotation_handle") or "") == handle:
                continue
            if any(same_value(record["value_cm"], value) for value in missing):
                sibling_hits.append(record)
        still_missing = [
            value
            for value in missing
            if not any(same_value(record["value_cm"], value) for record in sibling_hits)
        ]
        if sibling_hits and not still_missing:
            verdict.update(
                {
                    "class": "DIMENSIONS_EXIST_UNLINKED",
                    "pattern": "SIBLING_IN_REVIEW_GROUP",
                    "missing_parameters_cm": missing,
                    "sibling_dimension_evidence": sibling_hits[:8],
                    "dimension_handles": sorted(
                        set(verdict["dimension_handles"])
                        | {str(record["handle"]) for record in sibling_hits}
                    ),
                    "why": (
                        "Липсващите размери ("
                        + ", ".join(f"{value:g} cm" for value in missing)
                        + ") са начертани като собствени коти върху ДРУГ член на "
                        f"същата група за преглед ({group_key[:14]}…, "
                        f"{row.get('review_group_size')} позиции): "
                        + ", ".join(
                            f"{record['handle']}={record['value_cm']:g} cm "
                            f"(при {record['annotation_handle']})"
                            for record in sibling_hits[:4]
                        )
                        + ". Проектантът е котирал един екземпляр; копията носят "
                        "същата канонична подпис-геометрия, но нямат своя кота, "
                        "затова връзката не стига до тях."
                    ),
                    "automatable": True,
                    "rule_if_automatable": (
                        "Пренеси котите от котирания член на групата към копията "
                        "САМО когато каноничният подпис (canonical_signature) и "
                        "заявената L са идентични и групата има точно един "
                        "котиран източник. Две различни котирани четения в една "
                        "група — отказ."
                    ),
                }
            )
            return verdict
        # Различаваме „няма го никъде“ от „начертано е, но не е НАДПИСАНО“.
        # Второто не иска норма: участъкът е измерен от самата геометрия.
        drawn_only = bool(
            row.get("production_segments_cm")
            and str(verdict["production_segment_confidence"] or "").upper() == "HIGH"
            and equation_closes
            and all(
                any(
                    same_value(value, float(segment))
                    for segment in row.get("production_segments_cm") or ()
                )
                for value in missing
            )
        )
        verdict.update(
            {
                "class": "INCOMPLETE",
                "pattern": "PRINTED_MISSING_BUT_DRAWN" if drawn_only else "NO_EVIDENCE",
                "why": (
                    "Няма НАДПИСАН размер за участък(ци) "
                    + ", ".join(f"{value:g} cm" for value in missing)
                    + f" в радиус {DIMENSION_RADIUS:g} единици около позицията, "
                    "нито като DIMENSION, нито като числов TEXT, нито като "
                    "собствена кота на друг член на същата група за преглед. "
                    + (
                        "Участъкът обаче е НАЧЕРТАН и измерен от самата DXF "
                        f"геометрия ({verdict['production_segment_source']}, "
                        "confidence HIGH) и уравнението се затваря със заявената "
                        "L — липсва само печатно потвърждение, НЕ е нужно проектно "
                        "правило."
                        if drawn_only
                        else "Стойността не идва и от доказана геометрия — тук "
                        "наистина липсва информация и без проектно правило "
                        "участъкът не може да се твърди."
                    )
                    + (
                        " Самата програма отчита "
                        f"{(row.get('dimension_derived_evidence') or {}).get('computed_dimensions')} "
                        "ДОСМЕТНАТИ и "
                        f"{(row.get('dimension_derived_evidence') or {}).get('measured_dimensions')} "
                        "измерени размера за тази позиция "
                        f"({(row.get('dimension_derived_evidence') or {}).get('source')})."
                        if (row.get("dimension_derived_evidence") or {}).get(
                            "computed_dimensions"
                        )
                        else ""
                    )
                    + (
                        " Съществува и РАЗЛИЧНО предложение "
                        + "/".join(
                            f"{float(value):g}"
                            for value in row.get("suggested_segments_cm") or ()
                        )
                        + " cm — значи повече от едно разчитане е възможно."
                        if row.get("suggested_segments_cm")
                        and [
                            round(float(v), 2) for v in row.get("suggested_segments_cm") or ()
                        ]
                        != [
                            round(float(v), 2)
                            for v in row.get("production_segments_cm") or ()
                        ]
                        else ""
                    )
                ),
                "automatable": False,
                "rule_if_automatable": (
                    "Ако собственикът приеме измерен (а не надписан) участък, "
                    "правилото е: приеми само когато production_segment_evidence "
                    "е HIGH, кандидатът е един и segments_match_total затваря L; "
                    "надписаните участъци остават авторитет над измерените."
                    if drawn_only
                    else None
                ),
                "missing_parameters_cm": missing,
            }
        )
        return verdict

    if printed_params is not None and not params_agree():
        verdict.update(
            {
                "class": "CONTRADICTORY",
                "why": (
                    "Двата независими източника не се съгласуват: надписаните коти "
                    "се четат като "
                    + "/".join(f"{value:g}" for value in printed_params)
                    + " cm, а измереното по DXF геометрия на кандидата дава "
                    + "/".join(f"{value:g}" for value in geometry_params)
                    + " cm. И двете затварят проверката по дължина, значи "
                    "дължината не различава кое е вярно."
                ),
                "automatable": False,
                "rule_if_automatable": None,
            }
        )
        return verdict

    # Котите носят нужните числа, но са НАЧЕРТАНИ върху друг контур. Числото
    # е същото; връзката сочи друг обект. Реален случай: d-1 4A65 — котите
    # 4A5C/4A5D/4A5E лежат върху 4A5F, а кандидатът е 4A60 (близнак на 7-19
    # единици, затова и WEAK_CANDIDATE_SEPARATION).
    neighbour_owners = sorted(
        {owner for item in coverage for owner in item["foreign_owners"]}
    )
    own_covered = {
        item["value_cm"] for item in coverage if item["owned"]
    }
    foreign_only = [
        item["value_cm"]
        for item in coverage
        if not item["owned"] and item["foreign_owners"]
    ]
    neighbour_covers_rest = bool(foreign_only) and all(
        item["owned"] or item["foreign_owners"] for item in coverage
    )
    if neighbour_covers_rest:
        single = len(neighbour_owners) == 1
        mixed = bool(own_covered)
        verdict.update(
            {
                "class": "DIMENSIONS_EXIST_UNLINKED",
                "pattern": "SPLIT_ACROSS_TWIN_CONTOURS"
                if mixed
                else "OWNED_BY_NEIGHBOUR_CONTOUR",
                "neighbour_owner_handles": neighbour_owners,
                "own_covered_values_cm": sorted(own_covered),
                "foreign_covered_values_cm": sorted(foreign_only),
                "why": (
                    "Котите с нужните стойности съществуват и са НАЧЕРТАНИ "
                    "(опорните им точки лежат върху върхове на контур), но "
                    + (
                        "разпределени между два обекта: "
                        + ", ".join(f"{value:g} cm" for value in sorted(own_covered))
                        + " лежат върху собствения контур "
                        + ", ".join(candidates)
                        + ", а "
                        + ", ".join(f"{value:g} cm" for value in sorted(foreign_only))
                        + " — върху "
                        + ", ".join(neighbour_owners)
                        + ". "
                        if mixed
                        else "върху "
                        + ("контур " if single else "контури ")
                        + ", ".join(neighbour_owners)
                        + f", а кандидатът на позицията е {', '.join(candidates)}. "
                    )
                    + "Асоциацията не се е получила не заради разстояние, слой или "
                    "рамка, а защото начертаната връзка сочи ДРУГ обект — близнак "
                    "на същото стреме, нарисуван на 6–19 единици до него (затова "
                    "и WEAK_CANDIDATE_SEPARATION). Числата съвпадат с независимо "
                    "измерената геометрия на кандидата"
                    + (
                        ", тоест разчитането е едно и също; спорно е само на кой "
                        "обект принадлежи."
                        if params_agree()
                        else ", но прочитането по близост дава друго."
                    )
                ),
                "automatable": bool(single and params_agree() and equation_closes),
                "rule_if_automatable": (
                    "Приеми котите на близнака САМО когато: точно един чужд контур "
                    "ги притежава; неговият каноничен подпис/сегментен вектор е "
                    "идентичен с този на кандидата; и segments_match_total затваря "
                    "заявената L. Иначе първо се решава принадлежността на "
                    "геометрията, не размерът."
                )
                if single and params_agree()
                else None,
            }
        )
        return verdict

    geometry_confirms_printed = bool(
        printed_params is not None
        and params_agree()
        and evidence is not None
        and evidence.confidence in ("HIGH", "MEDIUM")
        and evidence.computed_count == 0
    )
    if geometry_confirms_printed and equation_closes:
        note = (
            "Всеки нужен размер е надписан в чертежа и прочетеното по близост "
            "дава СЪЩИЯ вектор, който е независимо измерен от самата DXF "
            f"геометрия на кандидата ({verdict['production_segment_source']}); "
            "уравнението се затваря със заявената L. Нито едно число не е "
            "досметнато (computed_count=0)."
        )
        if len(alternatives) >= 2:
            note += (
                " Второто разчитане по близост "
                f"({alternatives[1][1]:g}/{alternatives[1][2]:g}/{alternatives[1][3]:g} cm) "
                "е изключено от независимо измерената геометрия, не по разстояние."
            )
        verdict.update(
            {
                "class": "FULLY_DIMENSIONED",
                "pattern": "GEOMETRY_CONFIRMED_PRINTED",
                "why": note,
                "automatable": True,
                "rule_if_automatable": (
                    "Приеми производствения вектор, когато НЕЗАВИСИМО измереният "
                    "по геометрия вектор и прочетеният по коти вектор съвпадат "
                    "участък по участък (±0.35 cm), всеки участък е надписан "
                    "(computed_count == 0) и segments_match_total затвори L. "
                    "При разминаване — отказ, не избор."
                ),
            }
        )
        return verdict

    if len(alternatives) >= 2:
        verdict.update(
            {
                "class": "CONTRADICTORY",
                "why": (
                    "Повече от едно разчитане е възможно и нищо не го разрешава: "
                    + " и ".join(
                        f"{hook:g}/{width:g}/{height:g} cm (сума на разстоянията "
                        f"{span:.0f})"
                        for span, hook, width, height in alternatives[:3]
                    )
                    + ". Няма начертана връзка към контура и измерената геометрия "
                    "не потвърждава нито едно."
                ),
                "automatable": False,
                "rule_if_automatable": None,
            }
        )
        return verdict

    # Стойностите ги има, но връзката не е доказана.
    reasons: list[str] = []
    if not owned_covers_all:
        no_owned = [item["value_cm"] for item in coverage if not item["owned"]]
        reasons.append(
            "няма начертана връзка (кота върху върховете на контура) за "
            + ", ".join(f"{value:g} cm" for value in no_owned)
        )
    shared = [
        item["value_cm"] for item in coverage if len(item["within_radius"]) > 1
    ]
    if shared:
        reasons.append(
            "стойността се носи от повече от една кота в радиуса ("
            + ", ".join(f"{value:g} cm" for value in shared)
            + "), значи принадлежността е по близост, не по чертеж"
        )
    if evidence is None:
        reasons.append(
            "уравнението по близост не се затваря (derive_shape отказва), "
            "затова програмата не свързва котите с позицията"
        )
    elif evidence.confidence not in ("HIGH", "MEDIUM"):
        reasons.append(
            f"доказателството по близост е слабо ({evidence.confidence})"
        )
    elif evidence.computed_count:
        reasons.append(
            f"{evidence.computed_count} от участъците идват от досмятане, не от кота"
        )
    if row.get("production_segments_cm"):
        reasons.append(
            "редът вече има производствен вектор от геометрията "
            f"({verdict['production_segment_source']}), затова "
            "_apply_dimension_derived_production_sizes прескача позицията "
            "(`if row.get(\"production_segments_cm\"): continue`) и полето за "
            "надписани коти остава празно"
        )

    verdict.update(
        {
            "class": "DIMENSIONS_EXIST_UNLINKED",
            "why": (
                "Котите с нужните стойности съществуват (handle-ите по-горе), но "
                "връзката с позицията не е доказана: " + "; ".join(reasons) + "."
            ),
            "automatable": bool(params_agree()),
            "rule_if_automatable": (
                "Свържи котата с позицията само когато независимо измереният по "
                "геометрия вектор я потвърждава участък по участък и "
                "segments_match_total затвори L; иначе остави за оператор."
            )
            if params_agree()
            else None,
        }
    )
    return verdict


# --------------------------------------------------------------------------
# Изпълнение
# --------------------------------------------------------------------------
def analyse(
    report_glob: str,
    project_root: Path,
    *,
    only_project: str | None = None,
) -> dict[str, Any]:
    reports = newest_reports(report_glob)
    if only_project:
        reports = {k: v for k, v in reports.items() if k == only_project}
    verdicts: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    for project, report_path in reports.items():
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        rows = [
            row
            for row in payload.get("rows") or []
            if row.get("matcher_disposition") == "MANUAL"
        ]
        entities_path = project_root / project / "working" / "extracted_entities.json"
        if not entities_path.exists():
            sources[project] = {
                "report": str(report_path),
                "entities": None,
                "note": "липсва extracted_entities.json — позициите остават UNKNOWN",
                "manual_rows": len(rows),
            }
            for row in rows:
                verdicts.append(
                    {
                        "project": project,
                        "annotation_handle": row.get("annotation_handle"),
                        "annotation_text": row.get("annotation_text"),
                        "group": "F006_HOOKED"
                        if row.get("batch_status") == F006_STATUS
                        and row.get("hooked_closure")
                        else "OTHER_MANUAL",
                        "class": "UNKNOWN",
                        "why": "няма копие на DXF обектите за независима проверка",
                        "automatable": False,
                        "rule_if_automatable": None,
                        "segments_needed": None,
                        "segments_with_dimension": None,
                        "printed_dimensions": [],
                        "dimension_handles": [],
                    }
                )
            continue

        entities, identity = load_entities(entities_path)
        dims = dimension_records(entities)
        program_dims = [
            {"measurement": item["measurement"], "point": item["point"]} for item in dims
        ]
        texts = numeric_texts(entities)
        strict_owned = strict_unique_dimension_records_by_contours(entities)
        loose_owned = dimensions_owned_by_contours(entities)
        positions = {
            str(entity.handle): entity.geometry.get("insert")
            for entity in entities
            if entity.geometry.get("insert")
        }
        preferred_hook = infer_preferred_hook(
            [float(item["measurement"]) for item in program_dims]
        )
        gaps: list[float] = []
        for probe in payload.get("rows") or []:
            if str(probe.get("master_shape_code") or "").strip().upper() != "F006":
                continue
            probe_point = positions.get(str(probe.get("annotation_handle") or ""))
            probe_length = probe.get("declared_length_cm")
            if not probe_point or not isinstance(probe_length, (int, float)):
                continue
            probe_evidence = derive_shape(
                probe_point,
                program_dims,
                float(probe_length),
                expected_shape="F006",
                preferred_hook=preferred_hook,
            )
            if probe_evidence is not None:
                gaps.append(probe_evidence.allowance)
        preferred_gap = infer_preferred_gap(gaps)

        # Собствените коти на ВСИЧКИ редове, групирани по група за преглед.
        # Така се вижда кога проектантът е котирал само един екземпляр, а
        # копията са останали без кота.
        group_owned: dict[str, list[dict[str, Any]]] = {}
        for other in payload.get("rows") or []:
            key = str(other.get("review_group_key") or "")
            if not key:
                continue
            for candidate in other.get("candidate_handles") or []:
                for record in strict_owned.get(str(candidate), ()):  # начертана връзка
                    group_owned.setdefault(key, []).append(
                        {
                            "handle": str(record.get("handle") or ""),
                            "value_cm": float(record.get("measurement_cm") or 0.0),
                            "owner_geometry_handle": str(candidate),
                            "annotation_handle": str(other.get("annotation_handle") or ""),
                            "annotation_text": other.get("annotation_text"),
                            "matcher_disposition": other.get("matcher_disposition"),
                        }
                    )

        dimension_owner = {
            str(record.get("handle") or ""): contour
            for contour, records in strict_owned.items()
            for record in records
        }

        report_identity = payload.get("data_identity") or {}
        sources[project] = {
            "report": str(report_path),
            "report_sha256": file_sha256(report_path),
            "report_mtime": report_path.stat().st_mtime,
            "report_generated_at": payload.get("generated_at"),
            "entities": str(entities_path),
            "total_rows": len(payload.get("rows") or []),
            "manual_rows": len(rows),
            "frame_titles": sorted(
                {
                    str(other.get("frame_title") or "")
                    for other in payload.get("rows") or []
                    if other.get("frame_title")
                }
            ),
            "report_generation_id": report_identity.get("source_generation_id"),
            "entities_generation_id": identity.get("source_generation_id"),
            "generation_matches": (
                report_identity.get("source_generation_id")
                == identity.get("source_generation_id")
            ),
            "engine_version": (payload.get("analysis_engine") or {}).get(
                "engine_version"
            ),
            "dimension_count": len(dims),
            "contours_with_owned_dimensions": len(strict_owned),
            "preferred_hook_cm": preferred_hook,
            "preferred_bend_gap_cm": preferred_gap,
        }

        for row in rows:
            verdicts.append(
                classify_row(
                    row,
                    project=project,
                    dimensions=dims,
                    program_dimensions=program_dims,
                    texts=texts,
                    strict_owned=strict_owned,
                    loose_owned=loose_owned,
                    positions=positions,
                    preferred_hook=preferred_hook,
                    preferred_gap=preferred_gap,
                    group_owned=group_owned,
                    dimension_owner=dimension_owner,
                )
            )
    return {"sources": sources, "verdicts": verdicts}


def summarise(verdicts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classes = ("FULLY_DIMENSIONED", "DIMENSIONS_EXIST_UNLINKED", "INCOMPLETE", "CONTRADICTORY", "UNKNOWN")
    by_class = collections.Counter(v["class"] for v in verdicts)
    by_project: dict[str, dict[str, int]] = {}
    for verdict in verdicts:
        bucket = by_project.setdefault(
            verdict["project"], {name: 0 for name in classes}
        )
        bucket[verdict["class"]] += 1
    f006 = [v for v in verdicts if v.get("group") == "F006_HOOKED"]
    other = [v for v in verdicts if v.get("group") != "F006_HOOKED"]
    return {
        "totals": {
            "manual_total": len(verdicts),
            "f006_hooked": len(f006),
            "other_manual": len(other),
        },
        "by_class": {name: by_class.get(name, 0) for name in classes},
        "by_class_f006_hooked": {
            name: sum(1 for v in f006 if v["class"] == name) for name in classes
        },
        "by_class_other_manual": {
            name: sum(1 for v in other if v["class"] == name) for name in classes
        },
        "by_pattern": dict(
            sorted(
                collections.Counter(
                    (v["class"] + "/" + str(v.get("pattern") or "-")) for v in verdicts
                ).items()
            )
        ),
        "by_project": by_project,
        "by_project_f006_hooked": {
            project: {
                name: sum(
                    1
                    for v in f006
                    if v["project"] == project and v["class"] == name
                )
                for name in classes
            }
            for project in sorted({v["project"] for v in f006})
        },
    }


def automation_candidates(verdicts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Какво може да се чете машинно, без да се измисля нищо."""

    def count(pattern: str, *, group: str | None = None) -> int:
        return sum(
            1
            for v in verdicts
            if v.get("pattern") == pattern
            and (group is None or v.get("group") == group)
        )

    owned = [v for v in verdicts if v.get("pattern") == "OWNED_CONTOUR"]
    confirmed = [v for v in verdicts if v.get("pattern") == "GEOMETRY_CONFIRMED_PRINTED"]
    candidates = [
        {
            "pattern": "OWNED_CONTOUR — котата е начертана върху собствения контур",
            "positions": len(owned),
            "positions_f006_hooked": count("OWNED_CONTOUR", group="F006_HOOKED"),
            "projects": sorted({v["project"] for v in owned}),
            "rule": (
                "За позиция с точно един кандидат-контур: вземи котите, за които "
                "strict_unique_dimension_records_by_contours дава ТОЗИ контур като "
                "единствен собственик на ДВЕТЕ опорни точки (endpoint score ≤ 3.0 "
                "единици и всеки втори по близост контур по-далеч с повече от една "
                "толерансна единица). Прочети ги през read_owned_stirrup и приеми "
                "вектора само ако segments_match_total(code, сума, declared) е вярно. "
                "Нищо не се досмята; при липсваща собствена кота за някой участък — "
                "отказ."
            ),
            "risk": (
                "Ниск за принадлежността (връзката е начертана), но НЕ нулев за "
                "ролята: коя от собствените коти е кука и коя страна се решава от "
                "read_owned_stirrup. Там, където контурът носи повече коти от "
                "нужните, ролята трябва да е проверима — иначе отказ. Този път "
                "вече съществува в кода като F006_OWNED_CONTOUR_DIMENSIONS; при "
                "тези 266 позиции той не е достигнат, защото по-ранният геометричен "
                "доказател вече е записал production_segments_cm."
            ),
            "evidence_required": (
                "handle на всяка собствена кота + измерената стойност + endpoint "
                "score на двете опорни точки + резултата от segments_match_total."
            ),
        },
        {
            "pattern": (
                "GEOMETRY_CONFIRMED_PRINTED — измереното по геометрия и "
                "прочетеното по коти съвпадат участък по участък"
            ),
            "positions": len(confirmed),
            "positions_f006_hooked": count(
                "GEOMETRY_CONFIRMED_PRINTED", group="F006_HOOKED"
            ),
            "projects": sorted({v["project"] for v in confirmed}),
            "rule": (
                "Приеми вектора, когато ДВА независими източника дадат същите "
                "числа: (1) production_segment_evidence от геометрията на кандидата "
                "с confidence HIGH и (2) derive_shape по надписаните коти с "
                "confidence HIGH/MEDIUM и computed_dimensions == 0 (всеки участък е "
                "надписан). Съвпадението е участък по участък (±0.35 cm), не по "
                "обща дължина. При разминаване — отказ, не избор."
            ),
            "risk": (
                "Средният риск е в източника по близост: derive_shape взима коти в "
                "радиус 260 единици и в d-1/s-10 това понякога е кота на съседно "
                "стреме. Затова тук той НЕ решава сам — служи само за потвърждение "
                "на независимо измерената геометрия. Ако се ползва самостоятелно, "
                "рискът става висок (мерено: в 25 позиции проксимити-четенето "
                "противоречи на измерената геометрия)."
            ),
            "evidence_required": (
                "production_segment_evidence (source, confidence, geometry handle) + "
                "handle-ите на надписаните коти + measured/computed брой + "
                "segments_match_total."
            ),
        },
        {
            "pattern": (
                "LABEL_TOTAL_ONLY — прав прът: няма отделни участъци за четене"
            ),
            "positions": count("LABEL_TOTAL_ONLY"),
            "positions_f006_hooked": count("LABEL_TOTAL_ONLY", group="F006_HOOKED"),
            "projects": sorted(
                {v["project"] for v in verdicts if v.get("pattern") == "LABEL_TOTAL_ONLY"}
            ),
            "rule": (
                "За F001 общата дължина от надписа е единственият производствен "
                "размер (така работи и excel_export._segments_for_link). Размерен "
                "въпрос тук няма. Оставащият въпрос е ПРИНАДЛЕЖНОСТ — кой DXF обект "
                "е на този надпис — и той не се решава с котировки."
            ),
            "risk": (
                "Нула за размера. Целият риск е в принадлежността: тези позиции са "
                "MANUAL заради FAR_ASSOCIATION, WEAK_CANDIDATE_SEPARATION или "
                "GEOMETRY_OWNERSHIP_CONFLICT. Автоматизирането на размера не ги "
                "отблокира и не трябва да се представя като напредък."
            ),
            "evidence_required": "надписът на позицията и заявената L.",
        },
        {
            "pattern": (
                "OWNED_BY_NEIGHBOUR_CONTOUR / SPLIT_ACROSS_TWIN_CONTOURS — котите "
                "са начертани върху близнака на стремето, не върху кандидата"
            ),
            "positions": count("OWNED_BY_NEIGHBOUR_CONTOUR")
            + count("SPLIT_ACROSS_TWIN_CONTOURS"),
            "positions_f006_hooked": count(
                "OWNED_BY_NEIGHBOUR_CONTOUR", group="F006_HOOKED"
            )
            + count("SPLIT_ACROSS_TWIN_CONTOURS", group="F006_HOOKED"),
            "positions_with_single_foreign_owner": sum(
                1
                for v in verdicts
                if v.get("pattern")
                in ("OWNED_BY_NEIGHBOUR_CONTOUR", "SPLIT_ACROSS_TWIN_CONTOURS")
                and v.get("automatable")
            ),
            "projects": sorted(
                {
                    v["project"]
                    for v in verdicts
                    if v.get("pattern")
                    in ("OWNED_BY_NEIGHBOUR_CONTOUR", "SPLIT_ACROSS_TWIN_CONTOURS")
                }
            ),
            "rule": (
                "Когато ТОЧНО ЕДИН чужд контур притежава липсващите коти и неговият "
                "сегментен вектор/каноничен подпис е идентичен с този на кандидата, "
                "котите описват същото стреме и могат да се прочетат — при "
                "затворено segments_match_total. Когато чуждите собственици са "
                "два или повече (мерено: 28 от 43 позиции), не се чете нищо: първо "
                "се решава кой обект е на надписа."
            ),
            "risk": (
                "Висок, ако се приложи без условието за един собственик. Това е "
                "същият дефект, от който правилата предупреждават: близост, приета "
                "за принадлежност. Реален случай d-1/4A9F: котата за куката (4A94) "
                "е върху кандидата 4A96, а ширината и височината (4A92, 4A93) са "
                "върху близнака 4A95 на 6–19 единици."
            ),
            "evidence_required": (
                "handle на чуждия контур-собственик, handle на всяка кота, "
                "сегментните вектори на двата контура за доказване на идентичност."
            ),
        },
        {
            "pattern": (
                "SIBLING_IN_REVIEW_GROUP — котиран е един екземпляр, копията са без кота"
            ),
            "positions": count("SIBLING_IN_REVIEW_GROUP"),
            "positions_f006_hooked": count(
                "SIBLING_IN_REVIEW_GROUP", group="F006_HOOKED"
            ),
            "projects": sorted(
                {
                    v["project"]
                    for v in verdicts
                    if v.get("pattern") == "SIBLING_IN_REVIEW_GROUP"
                }
            ),
            "rule": (
                "Пренеси котите от котирания член на групата за преглед към "
                "копията САМО когато canonical_signature и declared L са "
                "идентични, групата има точно ЕДИН котиран източник и "
                "segments_match_total затваря. Две различни котирани четения в "
                "една група — отказ за цялата група."
            ),
            "risk": (
                "Средно висок: това е пренос между обекти и е точно защитата, "
                "заради която решения не се наследяват. Затова условието е пълно "
                "съвпадение на подписа, а не близост, и остава по едно решение за "
                "група, а не за позиция."
            ),
            "evidence_required": (
                "review_group_key, canonical_signature на двата члена, handle на "
                "котата и на нейния контур-собственик."
            ),
        },
    ]
    # Модел с нула позиции не се предлага: в този корпус всяка F006 позиция се
    # реши през НАЧЕРТАНА връзка, затова чисто проксимити правило не е нужно.
    return [item for item in candidates if item["positions"]]


def must_stay_refused(verdicts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    no_evidence = [v for v in verdicts if v.get("pattern") == "NO_EVIDENCE"]
    drawn_only = [v for v in verdicts if v.get("pattern") == "PRINTED_MISSING_BUT_DRAWN"]
    contradictory = [v for v in verdicts if v["class"] == "CONTRADICTORY"]
    unlinked = [
        v
        for v in verdicts
        if v["class"] == "DIMENSIONS_EXIST_UNLINKED" and not v.get("automatable")
    ]
    unknown = [v for v in verdicts if v["class"] == "UNKNOWN"]
    return [
        {
            "reason": (
                "INCOMPLETE/NO_EVIDENCE — участък без нито едно надписано и без "
                "доказано измерено число; стойността, която програмата показва, е "
                "ДОСМЕТНАТА от уравнението"
            ),
            "positions": len(no_evidence),
            "positions_detail": [
                {
                    "project": v["project"],
                    "annotation_handle": v["annotation_handle"],
                    "annotation_text": v["annotation_text"],
                    "master_shape_code": v.get("master_shape_code"),
                    "missing_parameters_cm": v.get("missing_parameters_cm"),
                    "program_vector_cm": v.get("production_segments_cm"),
                    "program_computed_dimensions": (
                        (v.get("program_dimension_evidence") or {}).get(
                            "computed_dimensions"
                        )
                    ),
                    "rival_suggestion_cm": v.get("program_suggested_segments_cm"),
                }
                for v in no_evidence
            ],
            "missing_evidence": (
                "Надписана кота или доказана геометрия за изброените участъци. "
                "Без тях числото е следствие от уравнението, не от чертежа — точно "
                "това правилата забраняват да се представя като разчетено."
            ),
        },
        {
            "reason": (
                "CONTRADICTORY — два източника дават различни четения и дължината "
                "не различава кое е вярно"
            ),
            "positions": len(contradictory),
            "positions_detail": [
                {
                    "project": v["project"],
                    "annotation_handle": v["annotation_handle"],
                    "annotation_text": v["annotation_text"],
                    "printed_reading_cm": v.get("printed_reading_cm"),
                    "geometry_vector_cm": v.get("production_segments_cm"),
                    "closing_readings": v.get("closing_readings"),
                }
                for v in contradictory
            ],
            "missing_evidence": (
                "Начертана връзка (собствена кота на контура) за поне един "
                "участък, която да реши кое четене е на ТАЗИ позиция."
            ),
        },
        {
            "reason": (
                "DIMENSIONS_EXIST_UNLINKED без еднозначен собственик — числата ги "
                "има, но повече от един чужд контур ги притежава или "
                "принадлежността е само по близост"
            ),
            "positions": len(unlinked),
            "positions_detail": [
                {
                    "project": v["project"],
                    "annotation_handle": v["annotation_handle"],
                    "annotation_text": v["annotation_text"],
                    "pattern": v.get("pattern"),
                    "dimension_handles": v.get("dimension_handles"),
                    "neighbour_owner_handles": v.get("neighbour_owner_handles"),
                    "why": v.get("why"),
                }
                for v in unlinked
            ],
            "missing_evidence": (
                "Признак за принадлежност: кота върху върховете на СОБСТВЕНИЯ "
                "контур, или точно един чужд контур с доказано идентичен вектор. "
                "Тук първо се решава кой DXF обект е на надписа — размерът идва "
                "след това."
            ),
        },
        {
            "reason": (
                "INCOMPLETE/PRINTED_MISSING_BUT_DRAWN — участъкът е НАЧЕРТАН и "
                "измерен, но не е НАДПИСАН; това е решение на собственика, не "
                "липсваща информация"
            ),
            "positions": len(drawn_only),
            "positions_detail": [
                {
                    "project": v["project"],
                    "annotation_handle": v["annotation_handle"],
                    "annotation_text": v["annotation_text"],
                    "master_shape_code": v.get("master_shape_code"),
                    "missing_printed_cm": v.get("missing_parameters_cm"),
                    "measured_vector_cm": v.get("production_segments_cm"),
                    "measured_source": v.get("production_segment_source"),
                }
                for v in drawn_only
            ],
            "missing_evidence": (
                "Нищо от чертежа не липсва — липсва РЕШЕНИЕ приема ли се измерен "
                "участък без печатна кота. Това е въпрос към собственика, не "
                "норма."
            ),
        },
        {
            "reason": "UNKNOWN — няма производствен вектор, значи не се знае какво се иска",
            "positions": len(unknown),
            "positions_detail": [
                {
                    "project": v["project"],
                    "annotation_handle": v["annotation_handle"],
                    "annotation_text": v["annotation_text"],
                    "master_shape_code": v.get("master_shape_code"),
                }
                for v in unknown
            ],
            "missing_evidence": (
                "Производствен вектор или форма; без тях не може да се каже кои "
                "участъци изисква формата."
            ),
        },
    ]


METHOD = (
    "Прочетени са записаните пакетни отчети (най-новият за всеки проект) и "
    "``working/extracted_entities.json`` на копията на проектите. За всяка "
    "MANUAL позиция е определено КОИ различни размера изисква формата "
    "(за F006: кука, ширина, височина — три числа за шест участъка; за F001: "
    "само общата дължина, защото прав прът няма отделни участъци), и после е "
    "търсен надписан носител за всеки от тях. Принадлежността е мерена по три "
    "независими признака, а не по един: (1) начертана връзка — двете опорни "
    "точки на котата лежат върху върховете на собствения контур на кандидата и "
    "никой друг контур не е на същото разстояние "
    "(strict_unique_dimension_records_by_contours, толеранс 3.0 единици); "
    "(2) четене по близост със собствените правила на програмата "
    "(collect_dimensions/derive_shape, радиус 260 единици, обичайната кука и "
    "добавка за огъване, изведени от самия чертеж); (3) независимо измереният "
    "от геометрията производствен вектор (production_segment_evidence). "
    "Затварянето на уравнението се проверява само с "
    "lion_rebar.engine.bend_allowance.segments_match_total — правилото на "
    "самата програма, не плосък допуск. Добавката за огъване се ползва "
    "единствено като ПРОВЕРКА; никога за да се досмята липсващ участък. "
    "РАЗЧЕТЕНО ОТ ЧЕРТЕЖА ≠ ПРОВЕРЕНО СРЕЩУ НОРМА: тук се мери само първото — "
    "съществува ли надписаното число и на кого принадлежи. Нищо не е сравнявано "
    "с БДС/Еврокод и нищо не е одобрявано. Инструментът е само за четене: не е "
    "пипан нито ред в lion_rebar/, нито проект."
)

CAVEATS = (
    "Корпусът се движеше по време на анализа: други сесии пишеха нови пакетни "
    "отчети. Затова точният файл, неговият sha256 и mtime са записани за всеки "
    "проект в ``sources``. Числата 234 MANUAL / 131 F006+HOOKED_CLOSURE от "
    "задачата отговарят на по-стар срез без d-1, d-2 и s-11.",
    "Устойчивост, мерена върху три последователни среза на корпуса (21:31, "
    "21:52-22:01 и 22:42 на 2026-09-27): групата F006+HOOKED_CLOSURE остана "
    "272 позиции със същото разпределение 208 FULLY_DIMENSIONED / 64 "
    "DIMENSIONS_EXIST_UNLINKED / 0 INCOMPLETE / 0 CONTRADICTORY. Общият MANUAL "
    "брой се движеше (298 → 493 → 512), защото се появяваха нови отчети за "
    "d-1, d-2 и s-11; промяната е изцяло в групата „останалите MANUAL“.",
    "d-1 и s-10 НЕ са един чертеж: рамките са „ВЕРТИКАЛИ ДО КОТА +11,90“ и "
    "„+5,90“ — два етажа на същата сграда, 2 общи handle-а от 126. Не се "
    "броят двойно.",
    "тест-004: отчетът и копието на обектите са от различни анализни "
    "прогони (source_generation_id се различава, а sha256 в отчета е празен). "
    "Всички 159 annotation handle-а и всички 142 candidate handle-а от отчета "
    "съществуват в копието, тоест handle-ите са съгласувани, но идентичността "
    "не е потвърдена по sha.",
    "Отчетите са с два различни engine-а: 0.3.14 (d-1, d-2, s-10, s-11, s-7, "
    "s-8, s-9) и 0.3.21 (Тест-002, Тест-003, тест-004, тест-006). Класовете са "
    "за наличното доказателство, не за качеството на engine-а.",
    "Като носител на надписан размер се приемат DIMENSION с измерена "
    "стойност и TEXT/MTEXT с ЧИСТО число. Размер, скрит в съставен текст "
    "(„25x20“, „ø8/20“) или във вградена OLE ведомост, не се разпознава — "
    "затова INCOMPLETE означава „не е намерено по този начин“, не „сигурно го "
    "няма в чертежа“.",
    "Класификацията е за размерното доказателство. Причината позицията да е "
    "MANUAL често е друга: полето manual_reason_is_dimensional показва дали "
    "блокерът е размерен изобщо.",
    "Моделът GEOMETRY_CONFIRMED_PRINTED (размерите само по близост, "
    "потвърдени от независимо измерената геометрия) завърши с 0 позиции: всяка "
    "F006 позиция в корпуса се реши през НАЧЕРТАНА връзка — собствена кота на "
    "контура или кота на близнака. Затова чисто проксимити правило не се "
    "предлага и не е нужно.",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", default=DEFAULT_REPORT_GLOB)
    parser.add_argument("--projects", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--only", default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--explore", action="store_true")
    args = parser.parse_args(argv)

    result = analyse(args.reports, Path(args.projects), only_project=args.only)
    verdicts = result["verdicts"]
    summary = summarise(verdicts)

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.explore:
        for verdict in verdicts[:400]:
            print(
                "%-10s %-16s %-24s %-26s needed=%s covered=%s"
                % (
                    verdict["project"],
                    verdict.get("annotation_handle"),
                    verdict.get("annotation_text"),
                    verdict["class"],
                    verdict.get("segments_needed"),
                    verdict.get("segments_with_dimension"),
                )
            )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": METHOD,
        "caveats": list(CAVEATS),
        "sources": result["sources"],
        "totals": summary["totals"],
        "by_class": summary["by_class"],
        "by_class_f006_hooked": summary["by_class_f006_hooked"],
        "by_class_other_manual": summary["by_class_other_manual"],
        "by_pattern": summary["by_pattern"],
        "by_project": summary["by_project"],
        "by_project_f006_hooked": summary["by_project_f006_hooked"],
        "evidence": verdicts,
        "automation_candidates": automation_candidates(verdicts),
        "must_stay_refused": must_stay_refused(verdicts),
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("написано:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
